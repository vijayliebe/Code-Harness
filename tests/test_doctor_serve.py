"""Fusion PR #5 — doctor CLI + localhost retrieve / MCP."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from harness.config import Config
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

# Unique fake secret so GitHub push protection does not treat this file as a leak.
FAKE_OPENAI = "sk-proj-" + "TESTFAKESECRETKEY1234567890ABCD"


def _chunk(content, cid="func:harness/demo.py:run_eval:abcd1234"):
    return Chunk(
        id=cid,
        content=content,
        entity_id="func:harness/demo.py:run_eval",
        entity_name="run_eval",
        entity_type=EntityType.FUNCTION,
        file_path="harness/demo.py",
        start_line=1,
        end_line=max(2, content.count("\n") + 1),
    )


def _rr(content, score=0.9):
    return RetrievalResult(chunk=_chunk(content), score=score, source="test")


def _healthy_repo(tmp, repo_name="fixture"):
    """Minimal on-disk index + graph so doctor required checks can pass."""
    chroma = Path(tmp) / ".code-harness" / "chromadb"
    chroma.mkdir(parents=True)
    (chroma / "chroma.sqlite3").write_bytes(b"sqlite")
    graph = Path(tmp) / ".code-harness" / f"graph_{repo_name}.json"
    graph.write_text(
        json.dumps({"directed": True, "multigraph": False, "graph": {}, "nodes": [{"id": "n1"}], "links": []}),
        encoding="utf-8",
    )
    audit_dir = Path(tmp) / ".code-harness" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return tmp


def _config(tmp, repo_name="fixture"):
    cfg = Config()
    cfg.repo_path = tmp
    cfg.vector_store["persist_directory"] = os.path.join(tmp, ".code-harness", "chromadb")
    cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
    cfg.redaction["audit_path"] = os.path.join(tmp, ".code-harness", "audit", "audit.jsonl")
    cfg.embedding["provider"] = "local"
    cfg.embedding["model"] = "all-MiniLM-L6-v2"
    cfg.llm["provider"] = "ollama"
    cfg.llm["api_key"] = None
    return cfg, repo_name


class TestDoctorChecks(unittest.TestCase):
    def test_missing_index_fails_with_actionable_hint(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            cfg, repo_name = _config(tmp)
            report = run_doctor(cfg, repo_path=tmp, repo_name=repo_name, environ={})
            self.assertFalse(report.ok)
            self.assertEqual(report.exit_code, 1)
            names = {c.name: c for c in report.checks}
            self.assertEqual(names["index"].status, "fail")
            self.assertIn("index", names["index"].hint.lower())
            text = report.format()
            self.assertIn("[fail]", text)
            self.assertIn("index", text)

    def test_healthy_fixture_exits_zero_with_pass_lines(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            _healthy_repo(tmp)
            cfg, repo_name = _config(tmp)
            report = run_doctor(cfg, repo_path=tmp, repo_name=repo_name, environ={})
            self.assertTrue(report.ok, report.format())
            self.assertEqual(report.exit_code, 0)
            names = {c.name: c for c in report.checks}
            for required in ("python", "deps", "index", "graph", "embedding", "redact", "audit"):
                self.assertIn(required, names)
                self.assertNotEqual(names[required].status, "fail", required)
            self.assertEqual(names["index"].status, "pass")
            self.assertEqual(names["graph"].status, "pass")
            self.assertEqual(names["redact"].status, "pass")
            self.assertEqual(names["audit"].status, "pass")
            text = report.format()
            self.assertIn("[ok]", text)
            self.assertIn("[pass]", text + "[ok]")

    def test_missing_cloud_llm_key_is_warn_not_fail(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            _healthy_repo(tmp)
            cfg, repo_name = _config(tmp)
            cfg.llm["provider"] = "openai"
            cfg.llm["api_key"] = None
            report = run_doctor(cfg, repo_path=tmp, repo_name=repo_name, environ={})
            self.assertTrue(report.ok, report.format())
            llm = next(c for c in report.checks if c.name == "llm")
            self.assertEqual(llm.status, "warn")
            self.assertIn("OPENAI_API_KEY", llm.hint)

    def test_disabled_redact_is_warn_with_hint(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            _healthy_repo(tmp)
            cfg, repo_name = _config(tmp)
            cfg.redaction["enabled"] = False
            report = run_doctor(
                cfg, repo_path=tmp, repo_name=repo_name, environ={"CODEHARNESS_REDACT": "0"},
            )
            redact = next(c for c in report.checks if c.name == "redact")
            self.assertEqual(redact.status, "warn")
            self.assertIn("CODEHARNESS_REDACT", redact.hint)

    def test_unwritable_audit_path_fails(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            _healthy_repo(tmp)
            cfg, repo_name = _config(tmp)
            blocked = os.path.join(tmp, "blocked-audit")
            os.makedirs(blocked, exist_ok=True)
            os.chmod(blocked, 0o500)
            cfg.redaction["audit_path"] = os.path.join(blocked, "nested", "audit.jsonl")
            try:
                report = run_doctor(cfg, repo_path=tmp, repo_name=repo_name, environ={})
            finally:
                os.chmod(blocked, 0o700)
            audit = next(c for c in report.checks if c.name == "audit")
            self.assertEqual(audit.status, "fail")
            self.assertFalse(report.ok)

    def test_voyage_embed_without_key_fails(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            _healthy_repo(tmp)
            cfg, repo_name = _config(tmp)
            cfg.embedding["provider"] = "voyage"
            cfg.embedding["model"] = "voyage-code-2"
            cfg.embedding["api_key"] = None
            report = run_doctor(cfg, repo_path=tmp, repo_name=repo_name, environ={})
            emb = next(c for c in report.checks if c.name == "embedding")
            self.assertEqual(emb.status, "fail")
            self.assertIn("VOYAGE_API_KEY", emb.hint)
            self.assertFalse(report.ok)

    def test_cli_exit_codes(self):
        empty = subprocess.run(
            [sys.executable, "main.py", "doctor", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertIn("health", empty.stdout.lower())

        with tempfile.TemporaryDirectory() as tmp:
            fail = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "doctor", tmp],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertEqual(fail.returncode, 1, fail.stdout + fail.stderr)
            self.assertIn("index", fail.stdout.lower())

            _healthy_repo(tmp, repo_name=os.path.basename(os.path.abspath(tmp)))
            ok = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "doctor", tmp],
                capture_output=True,
                text=True,
                cwd=tmp,
                env={**os.environ, "LLM_PROVIDER": "ollama"},
            )
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertRegex(ok.stdout, r"\[(ok|pass)\]")

        top = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("doctor", top.stdout)


class TestBindGuard(unittest.TestCase):
    def test_loopback_allowed_by_default(self):
        from harness.serve import resolve_bind_host

        self.assertEqual(resolve_bind_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(resolve_bind_host("localhost"), "localhost")
        self.assertEqual(resolve_bind_host("::1"), "::1")
        self.assertEqual(resolve_bind_host(None), "127.0.0.1")

    def test_all_interfaces_refused_without_flag(self):
        from harness.serve import BindError, resolve_bind_host

        for host in ("0.0.0.0", "::", "*"):
            with self.assertRaises(BindError) as ctx:
                resolve_bind_host(host)
            self.assertIn("allow-public", str(ctx.exception).lower())
            self.assertIn("dangerous", str(ctx.exception).lower())

    def test_all_interfaces_require_explicit_flag(self):
        from harness.serve import resolve_bind_host

        self.assertEqual(resolve_bind_host("0.0.0.0", allow_public=True), "0.0.0.0")

    def test_non_loopback_ip_refused(self):
        from harness.serve import BindError, resolve_bind_host

        with self.assertRaises(BindError):
            resolve_bind_host("192.168.1.10")


class TestRetrieveHandler(unittest.TestCase):
    def test_retrieve_uses_injected_retriever_and_packs(self):
        from harness.serve import RetrieveService

        calls = []

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                calls.append((query, top_k))
                return [_rr("def run_eval():\n    return 1\n")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = _config(tmp)
            service = RetrieveService(
                config=cfg,
                retriever=FakeRetriever(),
                repo_name="fixture",
            )
            payload = service.retrieve({"query": "how does eval work?", "top_k": 5})
            self.assertEqual(calls, [("how does eval work?", 5)])
            self.assertEqual(payload["query"], "how does eval work?")
            self.assertIn("run_eval", payload["context"])
            self.assertEqual(payload["chunk_ids"], ["func:harness/demo.py:run_eval:abcd1234"])
            self.assertEqual(payload["results"][0]["path"], "harness/demo.py")
            self.assertFalse(payload["cached"])

    def test_retrieve_response_is_redacted(self):
        from harness.serve import RetrieveService

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                return [_rr(f"OPENAI_API_KEY={FAKE_OPENAI}\n")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = _config(tmp)
            service = RetrieveService(config=cfg, retriever=FakeRetriever(), repo_name="fixture")
            payload = service.retrieve({"query": "leak"})
            blob = json.dumps(payload)
            self.assertNotIn(FAKE_OPENAI, blob)
            self.assertIn("REDACTED", blob)
            self.assertGreaterEqual(payload.get("redaction_count", 0), 1)
            audit = Path(cfg.redaction["audit_path"])
            self.assertTrue(audit.is_file())
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(rows)
            self.assertNotIn(FAKE_OPENAI, json.dumps(rows))

    def test_query_hash_cache_skips_second_retrieve(self):
        from harness.serve import QueryCache, RetrieveService

        calls = []

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                calls.append(query)
                return [_rr("def cached():\n    return 1\n")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = _config(tmp)
            cache = QueryCache(os.path.join(tmp, "cache.sqlite"))
            service = RetrieveService(
                config=cfg,
                retriever=FakeRetriever(),
                repo_name="fixture",
                cache=cache,
            )
            first = service.retrieve({"query": "same question"})
            second = service.retrieve({"query": "same question"})
            self.assertEqual(calls, ["same question"])
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(first["chunk_ids"], second["chunk_ids"])

    def test_health_payload(self):
        from harness.serve import handle_health

        cfg = Config()
        body = handle_health(cfg, host="127.0.0.1")
        self.assertTrue(body["ok"])
        self.assertEqual(body["bind"], "127.0.0.1")
        self.assertTrue(body["redact"])


class TestMcpSurface(unittest.TestCase):
    def test_tools_list_and_retrieve_call(self):
        from harness.serve import RetrieveService, handle_mcp

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                return [_rr("def tool():\n    pass\n")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = _config(tmp)
            service = RetrieveService(config=cfg, retriever=FakeRetriever(), repo_name="fixture")
            listed = handle_mcp(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                service=service,
            )
            names = {t["name"] for t in listed["result"]["tools"]}
            self.assertIn("retrieve", names)
            self.assertIn("doctor", names)
            called = handle_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "retrieve", "arguments": {"query": "tool?"}},
                },
                service=service,
            )
            text = json.dumps(called)
            self.assertIn("tool", text.lower())
            self.assertEqual(called["id"], 2)


class TestLocalhostHttp(unittest.TestCase):
    def test_health_and_retrieve_over_loopback(self):
        from harness.serve import RetrieveService, make_server

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                return [_rr(f"token={FAKE_OPENAI}\n")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = _config(tmp)
            service = RetrieveService(config=cfg, retriever=FakeRetriever(), repo_name="fixture")
            server = make_server(host="127.0.0.1", port=0, service=service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                self.assertEqual(host, "127.0.0.1")
                conn = HTTPConnection(host, port, timeout=5)
                conn.request("GET", "/health")
                health = json.loads(conn.getresponse().read().decode("utf-8"))
                self.assertTrue(health["ok"])
                conn.request(
                    "POST",
                    "/v1/retrieve",
                    body=json.dumps({"query": "secret?"}),
                    headers={"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                payload = json.loads(resp.read().decode("utf-8"))
                conn.close()
                self.assertNotIn(FAKE_OPENAI, json.dumps(payload))
                self.assertIn("REDACTED", json.dumps(payload))
            finally:
                server.shutdown()
                server.server_close()

    def test_cli_help_lists_serve_aliases(self):
        for args in (
            ["serve", "--help"],
            ["mcp", "--help"],
            ["mcp", "serve", "--help"],
            ["api", "--help"],
            ["api", "serve", "--help"],
        ):
            run = subprocess.run(
                [sys.executable, "main.py", *args],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, f"{args}: {run.stderr}")
            self.assertRegex(run.stdout.lower(), r"retrieve|127\.0\.0\.1|localhost|allow-public")

        top = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertIn("serve", top.stdout)
        self.assertIn("mcp", top.stdout)
        self.assertIn("doctor", top.stdout)

    def test_no_external_host_in_handler(self):
        """Retrieve/MCP paths must not open sockets to non-loopback hosts."""
        from harness.serve import RetrieveService

        opened = []
        real_create = socket.socket

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                return [_rr("ok\n")]

        def guarded(*args, **kwargs):
            sock = real_create(*args, **kwargs)
            orig = sock.connect

            def connect(address):
                opened.append(address)
                raise AssertionError(f"unexpected connect {address}")

            sock.connect = connect
            return sock

        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = _config(tmp)
            service = RetrieveService(config=cfg, retriever=FakeRetriever(), repo_name="fixture")
            with patch("socket.socket", side_effect=guarded):
                payload = service.retrieve({"query": "offline"})
        self.assertIn("ok", payload["context"])
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
