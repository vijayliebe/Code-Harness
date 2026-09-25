"""Fusion G5 remainder — MCP JSON-RPC over stdin/stdout (no network bind)."""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.config import Config
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

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


def _config(tmp):
    cfg = Config()
    cfg.repo_path = tmp
    cfg.vector_store["persist_directory"] = os.path.join(tmp, ".code-harness", "chromadb")
    cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
    cfg.redaction["audit_path"] = os.path.join(tmp, ".code-harness", "audit", "audit.jsonl")
    cfg.embedding["provider"] = "local"
    cfg.embedding["model"] = "all-MiniLM-L6-v2"
    cfg.llm["provider"] = "ollama"
    cfg.llm["api_key"] = None
    return cfg


def _rpc(method, req_id, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def _write_ndjson(stream, messages):
    for msg in messages:
        stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
    stream.seek(0)


def parse_stdout_protocol(text):
    """Parse stdout as MCP frames. Raises if any non-protocol bytes remain."""
    messages = []
    i = 0
    raw = text
    length = len(raw)
    while i < length:
        if raw[i].isspace():
            i += 1
            continue
        rest = raw[i:]
        if rest.lower().startswith("content-length:"):
            header_end = rest.find("\r\n\r\n")
            sep_len = 4
            if header_end < 0:
                header_end = rest.find("\n\n")
                sep_len = 2
            if header_end < 0:
                raise AssertionError(f"truncated Content-Length header: {rest[:80]!r}")
            headers = rest[:header_end]
            nbytes = None
            for line in headers.replace("\r\n", "\n").split("\n"):
                if line.lower().startswith("content-length:"):
                    nbytes = int(line.split(":", 1)[1].strip())
            if nbytes is None:
                raise AssertionError(f"missing Content-Length: {headers!r}")
            body_start = i + header_end + sep_len
            encoded = raw[body_start:].encode("utf-8")
            if len(encoded) < nbytes:
                raise AssertionError(
                    f"truncated body: need {nbytes} bytes, have {len(encoded)}"
                )
            body = encoded[:nbytes].decode("utf-8")
            messages.append(json.loads(body))
            i = body_start + len(body)
            continue
        if rest[0] == "{":
            nl = rest.find("\n")
            line = rest if nl < 0 else rest[:nl]
            messages.append(json.loads(line))
            i += len(line) if nl < 0 else nl + 1
            continue
        raise AssertionError(f"non-protocol stdout at offset {i}: {rest[:60]!r}")
    return messages


class TestMcpStdioProtocol(unittest.TestCase):
    def _run(self, messages, service, banner=True):
        from harness.serve import serve_stdio

        stdin = io.StringIO()
        _write_ndjson(stdin, messages)
        stdout = io.StringIO()
        stderr = io.StringIO()
        serve_stdio(service, stdin=stdin, stdout=stdout, stderr=stderr)
        out = stdout.getvalue()
        err = stderr.getvalue()
        parsed = parse_stdout_protocol(out)
        return parsed, out, err

    def test_initialize_tools_list_and_retrieve_call(self):
        from harness.serve import RetrieveService

        calls = []

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                calls.append((query, top_k))
                return [_rr("def tool():\n    return 1\n")]

        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(
                config=_config(tmp),
                retriever=FakeRetriever(),
                repo_name="fixture",
            )
            parsed, out, err = self._run(
                [
                    _rpc(
                        "initialize",
                        1,
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"},
                        },
                    ),
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    _rpc("tools/list", 2),
                    _rpc(
                        "tools/call",
                        3,
                        {"name": "retrieve", "arguments": {"query": "tool?"}},
                    ),
                ],
                service,
            )

        self.assertEqual(len(parsed), 3, parsed)
        init = parsed[0]
        self.assertEqual(init["id"], 1)
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(init["result"]["serverInfo"]["name"], "code-harness")
        self.assertIn("tools", init["result"]["capabilities"])

        listed = parsed[1]
        self.assertEqual(listed["id"], 2)
        names = {t["name"] for t in listed["result"]["tools"]}
        for expected in (
            "retrieve",
            "retrieve_chunk",
            "doctor",
            "wiki_show",
            "memory_brief",
            "graph_neighbors",
            "search_session",
        ):
            self.assertIn(expected, names)

        called = parsed[2]
        self.assertEqual(called["id"], 3)
        blob = json.dumps(called)
        self.assertIn("tool", blob.lower())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "tool?")
        self.assertNotIn("[*]", out)
        self.assertNotIn("Loading retrieve", out)
        self.assertTrue(err.strip(), "expected ready/status logs on stderr")

    def test_stdout_is_only_jsonrpc_even_if_code_prints(self):
        from harness.serve import RetrieveService, serve_stdio

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                print("ACCIDENTAL STDOUT LEAK")
                return [_rr("ok\n")]

        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(
                config=_config(tmp),
                retriever=FakeRetriever(),
                repo_name="fixture",
            )
            stdin = io.StringIO()
            _write_ndjson(
                stdin,
                [
                    _rpc("initialize", 1, {"protocolVersion": "2024-11-05"}),
                    _rpc(
                        "tools/call",
                        2,
                        {"name": "retrieve", "arguments": {"query": "leak?"}},
                    ),
                ],
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            serve_stdio(service, stdin=stdin, stdout=stdout, stderr=stderr)
            out = stdout.getvalue()
            err = stderr.getvalue()

        parsed = parse_stdout_protocol(out)
        self.assertEqual([m["id"] for m in parsed], [1, 2])
        self.assertNotIn("ACCIDENTAL STDOUT LEAK", out)
        self.assertIn("ACCIDENTAL STDOUT LEAK", err)

    def test_retrieve_tool_result_is_redacted(self):
        from harness.serve import RetrieveService

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                return [_rr(f"OPENAI_API_KEY={FAKE_OPENAI}\n")]

        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(
                config=_config(tmp),
                retriever=FakeRetriever(),
                repo_name="fixture",
            )
            parsed, out, _err = self._run(
                [
                    _rpc(
                        "tools/call",
                        9,
                        {"name": "retrieve", "arguments": {"query": "secret?"}},
                    )
                ],
                service,
            )
        blob = json.dumps(parsed) + out
        self.assertNotIn(FAKE_OPENAI, blob)
        self.assertIn("REDACTED", blob)

    def test_no_socket_bind_or_external_connect(self):
        from harness.serve import RetrieveService, serve_stdio

        opened = []
        bound = []
        real_create = socket.socket

        class FakeRetriever:
            def retrieve(self, query, top_k=None, **kwargs):
                return [_rr("ok\n")]

        def guarded(*args, **kwargs):
            sock = real_create(*args, **kwargs)
            orig_bind = sock.bind
            orig_connect = sock.connect

            def bind(address):
                bound.append(address)
                raise AssertionError(f"stdio MCP must not bind {address}")

            def connect(address):
                opened.append(address)
                raise AssertionError(f"unexpected connect {address}")

            sock.bind = bind
            sock.connect = connect
            sock.listen = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("stdio MCP must not listen")
            )
            return sock

        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(
                config=_config(tmp),
                retriever=FakeRetriever(),
                repo_name="fixture",
            )
            stdin = io.StringIO()
            _write_ndjson(stdin, [_rpc("tools/list", 1)])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("socket.socket", side_effect=guarded):
                serve_stdio(service, stdin=stdin, stdout=stdout, stderr=stderr)
            parsed = parse_stdout_protocol(stdout.getvalue())

        self.assertEqual(parsed[0]["id"], 1)
        self.assertIn("tools", parsed[0]["result"])
        self.assertEqual(opened, [])
        self.assertEqual(bound, [])


class TestMcpStdioCli(unittest.TestCase):
    def test_help_lists_stdio_transport(self):
        for args in (
            ["mcp", "--help"],
            ["mcp", "stdio", "--help"],
            ["mcp", "serve", "--help"],
        ):
            run = subprocess.run(
                [sys.executable, "main.py", *args],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, f"{args}: {run.stderr}")
            self.assertRegex(run.stdout.lower(), r"stdio")

        top = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("mcp", top.stdout)


if __name__ == "__main__":
    unittest.main()
