"""X0–X1: local-folder connector, ticket linking, retrieve-by-key."""

import os
import subprocess
import tempfile
import unittest

from harness.config import DEFAULT_CONFIG, Config
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RelationshipType
from harness.retriever import Retriever


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
TICKETS = os.path.join(ROOT, "tests", "fixtures", "external", "tickets")


class FakeEmbedder:
    def __init__(self):
        self.hyde_enabled = False

    def embed_query(self, query, expand=True):
        return [0.1, 0.2, 0.3]

    def _generate_hypothetical(self, query):
        return ""


class FakeVectorStore:
    supports_allowlist = False

    def __init__(self, results=None):
        self._results = results or []

    def search(self, embedding, top_k=10, repo_name=None, allowlist_ids=None):
        return list(self._results)[:top_k]


def _chunk(cid, content, path, name="fn", etype=EntityType.FUNCTION):
    return Chunk(
        id=cid,
        content=content,
        entity_id=cid,
        entity_name=name,
        entity_type=etype,
        file_path=path,
        start_line=1,
        end_line=8,
        metadata={},
    )


def _cfg(tmp, **external):
    cfg = Config()
    cfg.repo_path = tmp
    cfg.knowledge_graph["persist_path"] = os.path.join(tmp, "graph.json")
    cfg.external["persist_directory"] = os.path.join(tmp, "external")
    cfg.external.update(external)
    cfg.retrieval["cross_encoder"] = {"enabled": False}
    cfg.retrieval["expand_neighbors"] = 0
    cfg.retrieval["multi_query"]["enabled"] = False
    cfg.retrieval["expansion"]["enabled"] = False
    cfg.context["per_file_chunk_cap"] = 0
    cfg.redaction["audit_path"] = os.path.join(tmp, "audit.jsonl")
    return cfg


def _git(tmp, *args):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.test"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.test"
    return subprocess.run(
        ["git", "-C", tmp, *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _seed_ticket_repo(tmp):
    _git(tmp, "init")
    _git(tmp, "checkout", "-b", "fix/ABC-123-login-timeout")
    os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
    auth = os.path.join(tmp, "src", "auth.py")
    with open(auth, "w") as fh:
        fh.write(
            "def refresh_session(token):\n"
            "    # ABC-123 login timeout after SSO\n"
            "    return token\n"
        )
    _git(tmp, "add", "src/auth.py")
    _git(tmp, "commit", "-m", "ABC-123 fix login timeout after SSO")
    return auth


class TestTicketExtract(unittest.TestCase):
    def test_extracts_jira_keys_and_skips_encodings(self):
        from harness.external.linking import extract_doc_urls, extract_ticket_keys

        text = "See ABC-123 and PROJ-9; ignore UTF-8 and PEP-8. Also https://docs.example.test/x"
        self.assertEqual(extract_ticket_keys(text), ["ABC-123", "PROJ-9"])
        self.assertEqual(extract_ticket_keys("UTF-8 ISO-8601"), [])
        self.assertTrue(extract_doc_urls(text)[0].startswith("https://docs.example.test/x"))


class TestFileConnector(unittest.TestCase):
    def test_load_splits_headings_and_records_ticket_from_filename(self):
        from harness.external.file_connector import FileConnector
        from harness.external.ingest import consume_load
        from harness.external.models import ExternalDoc

        connector = FileConnector(TICKETS)
        connector.validate()
        items, checkpoint = consume_load(connector.load())
        docs = [i for i in items if isinstance(i, ExternalDoc)]
        ids = {d.id for d in docs}
        self.assertIn("file:ABC-123.md", ids)
        abc = next(d for d in docs if d.id == "file:ABC-123.md")
        self.assertEqual(abc.ticket_keys(), ["ABC-123"])
        self.assertTrue(any(s.link.endswith("#expected") for s in abc.sections))
        self.assertFalse(checkpoint.has_more)
        self.assertEqual(set(connector.slim_ids()), ids)

    def test_poll_respects_mtime_window(self):
        from harness.external.file_connector import FileConnector
        from harness.external.ingest import consume_load
        from harness.external.models import ExternalDoc

        connector = FileConnector(TICKETS)
        items, _ = consume_load(connector.poll("9999999999", None))
        self.assertEqual([i for i in items if isinstance(i, ExternalDoc)], [])


class TestIngestRedactAndStore(unittest.TestCase):
    def test_redacts_secrets_and_keeps_namespace_off_code_index(self):
        from harness.external.ingest import run_ingest
        from harness.external.store import ExternalStore

        fake = "sk-proj-" + "TESTFAKESECRETKEY1234567890ABCD"
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "exports")
            os.makedirs(src)
            with open(os.path.join(src, "SEC-1.md"), "w") as fh:
                fh.write(f"# SEC-1\n\nAPI_KEY={fake}\n")
            cfg = _cfg(tmp)
            result = run_ingest(cfg, src)
            self.assertEqual(result["upserted"], 1)
            store = ExternalStore.for_config(cfg, tmp)
            doc = store.list_docs()[0]
            self.assertIn("[REDACTED:", doc.body())
            self.assertNotIn(fake, doc.body())
            self.assertTrue(store.root.endswith("external"))
            self.assertNotIn("chromadb", store.root)

    def test_prune_drops_missing_ids(self):
        from harness.external.ingest import run_ingest
        from harness.external.store import ExternalStore

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "exports")
            os.makedirs(src)
            with open(os.path.join(src, "A.md"), "w") as fh:
                fh.write("# A\n")
            with open(os.path.join(src, "B.md"), "w") as fh:
                fh.write("# B\n")
            cfg = _cfg(tmp)
            run_ingest(cfg, src)
            os.remove(os.path.join(src, "B.md"))
            run_ingest(cfg, src)
            ids = [d.id for d in ExternalStore.for_config(cfg, tmp).list_docs()]
            self.assertEqual(ids, ["file:A.md"])


class TestLinking(unittest.TestCase):
    def test_mines_commit_branch_and_comment(self):
        from harness.external.linking import mine_fixtures, mine_repo

        with tempfile.TemporaryDirectory() as tmp:
            _seed_ticket_repo(tmp)
            mentions = mine_repo(tmp)
            kinds = {(m.source_kind, m.origin, m.source_id) for m in mentions}
            self.assertIn(("ticket", "commit", "ABC-123"), kinds)
            self.assertIn(("ticket", "branch", "ABC-123"), kinds)
            self.assertIn(("ticket", "comment", "ABC-123"), kinds)
            commit = next(m for m in mentions if m.origin == "commit" and m.source_kind == "ticket")
            self.assertEqual(commit.target_path, "src/auth.py")
            fixtures = mine_fixtures(tmp)
            ids = {f["id"] for f in fixtures}
            self.assertIn("ticket-ABC-123-with-key", ids)
            self.assertIn("ticket-ABC-123-no-key", ids)
            no_key = next(f for f in fixtures if f["id"].endswith("no-key"))
            self.assertNotIn("ABC-123", no_key["query"])
            self.assertIn("src/auth.py", no_key["must_cite_paths"])

    def test_applies_mentions_edges(self):
        from harness.external.linking import Mention, apply_mentions, linked_paths_for_ticket

        cfg = Config()
        kg = KnowledgeGraph(cfg, repo_name="fixture")
        kg.graph.add_node(
            "file:src/auth.py",
            name="auth.py",
            type="file",
            file_path="src/auth.py",
            start_line=1,
            end_line=3,
        )
        added = apply_mentions(
            kg,
            [Mention(source_kind="ticket", source_id="ABC-123", target_path="src/auth.py", origin="commit")],
        )
        self.assertGreaterEqual(added, 1)
        self.assertEqual(linked_paths_for_ticket(kg, "ABC-123"), ["src/auth.py"])
        data = kg.graph.edges["ticket:ABC-123", "file:src/auth.py"]
        self.assertEqual(data.get("relationship"), "mentions")
        self.assertEqual(RelationshipType.MENTIONS.value, "mentions")


class TestRetrieveByTicket(unittest.TestCase):
    def test_ticket_key_surfaces_linked_code_and_ingested_doc(self):
        from harness.external.ingest import run_ingest
        from harness.external.linking import Mention, apply_mentions

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp, enabled=False)
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.graph.add_node(
                "func:src/auth.py:refresh_session",
                name="refresh_session",
                type="function",
                file_path="src/auth.py",
                start_line=1,
                end_line=3,
            )
            apply_mentions(
                kg,
                [Mention(
                    source_kind="ticket",
                    source_id="ABC-123",
                    target_path="src/auth.py",
                    target_symbol="refresh_session",
                    origin="comment",
                )],
            )
            run_ingest(cfg, TICKETS)
            chunks = [
                _chunk(
                    "func:src/auth.py:refresh_session",
                    "def refresh_session(token):\n    return token\n",
                    "src/auth.py",
                    name="refresh_session",
                ),
                _chunk(
                    "func:src/other.py:noop",
                    "def noop():\n    return None\n",
                    "src/other.py",
                    name="noop",
                ),
            ]
            retriever = Retriever(cfg, FakeEmbedder(), FakeVectorStore(), kg, repo_name="fixture")
            retriever.index_chunks(chunks, persist=False)
            hits = retriever.retrieve("ABC-123 session cookie", top_k=10)
            paths = [(h.chunk.file_path or "") for h in hits]
            ids = [h.chunk.id for h in hits]
            self.assertIn("src/auth.py", paths)
            self.assertTrue(any(i.startswith("external:") for i in ids))

    def test_no_key_query_hits_ingested_ticket_when_external_on(self):
        from harness.external.ingest import run_ingest

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp, enabled=True, weight=0.8)
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.enabled = False
            run_ingest(cfg, TICKETS)
            chunks = [
                _chunk("func:src/other.py:noop", "def noop():\n    return None\n", "src/other.py"),
            ]
            retriever = Retriever(cfg, FakeEmbedder(), FakeVectorStore(), kg, repo_name="fixture")
            retriever.index_chunks(chunks, persist=False)
            hits = retriever.retrieve("login timeout after SSO", top_k=10)
            kinds = [(h.chunk.metadata or {}).get("kind") for h in hits]
            self.assertIn("external", kinds)

    def test_external_off_does_not_fuse_sidecar_for_plain_queries(self):
        from harness.external.ingest import run_ingest

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp, enabled=False)
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.enabled = False
            run_ingest(cfg, TICKETS)
            chunks = [
                _chunk("func:src/other.py:noop", "def noop():\n    return None\n", "src/other.py"),
            ]
            retriever = Retriever(cfg, FakeEmbedder(), FakeVectorStore(), kg, repo_name="fixture")
            retriever.index_chunks(chunks, persist=False)
            hits = retriever.retrieve("login timeout after SSO", top_k=10)
            kinds = [(h.chunk.metadata or {}).get("kind") for h in hits]
            self.assertNotIn("external", kinds)


class TestDefaults(unittest.TestCase):
    def test_external_defaults_stay_off(self):
        self.assertFalse(DEFAULT_CONFIG["external"]["enabled"])
        self.assertEqual(DEFAULT_CONFIG["external"]["weight"], 0.15)
        self.assertTrue(DEFAULT_CONFIG["external"]["ticket_lookup"])
        cfg = Config()
        self.assertFalse(cfg.external["enabled"])
        self.assertIsNot(cfg.external, DEFAULT_CONFIG["external"])

    def test_cli_help_lists_ingest_and_link(self):
        run = subprocess.run(
            ["python3", os.path.join(ROOT, "main.py"), "external", "-h"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0)
        self.assertIn("ingest", run.stdout)
        self.assertIn("link", run.stdout)
        self.assertIn("mine", run.stdout)


if __name__ == "__main__":
    unittest.main()
