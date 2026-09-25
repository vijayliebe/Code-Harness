"""Session-event FTS (DeepSeek steal #5).

Sibling of BM25-over-memory. This indexes append-only session events,
not the OKF vault. Default interactive JSONL stays unindexed.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import DEFAULT_CONFIG
from harness.session import Session, SessionTurn, handle_slash, parse_slash


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _event_session(**kwargs):
    kwargs.setdefault("event_session", True)
    return Session(repo="demo", **kwargs)


class TestIndexSearchHitMiss(unittest.TestCase):
    def test_hit_on_indexed_user_and_tool_result(self):
        from harness.events import EventLog, make_event
        from harness.session_fts import SessionEventIndex

        log = EventLog()
        log.append(make_event("meta", kind="session_start"))
        log.append(make_event("user", text="how does prefix-stable packing work?"))
        log.append(
            make_event(
                "tool_result",
                text="CCR-lite omits bodies and keeps chunk_id for retrieve_chunk",
                tool_name="retrieve",
            )
        )
        log.append(make_event("assistant", text="see the packer"))
        index = SessionEventIndex()
        indexed = index.sync(log.events)
        self.assertGreaterEqual(indexed, 3)
        hits = index.search("prefix-stable packing")
        self.assertTrue(hits, "expected a hit on the user turn")
        self.assertEqual(hits[0].type, "user")
        self.assertIn("packing", hits[0].snippet.lower())
        self.assertTrue(hits[0].event_id)
        self.assertTrue(hits[0].ts)

        tool_hits = index.search("retrieve_chunk")
        self.assertTrue(tool_hits)
        self.assertEqual(tool_hits[0].type, "tool_result")

    def test_miss_returns_empty(self):
        from harness.events import EventLog, make_event
        from harness.session_fts import SessionEventIndex

        index = SessionEventIndex()
        index.sync(
            [
                make_event("user", text="how does packing work?"),
                make_event("assistant", text="see the packer"),
            ]
        )
        self.assertEqual(index.search("xyzzy-no-such-token"), [])

    def test_skips_meta_clear_verify_noise(self):
        from harness.events import make_event
        from harness.session_fts import SessionEventIndex, indexable_body

        self.assertEqual(indexable_body(make_event("meta", kind="session_start")), "")
        self.assertEqual(indexable_body(make_event("clear", tokens_freed=12)), "")
        self.assertEqual(indexable_body(make_event("verify", extra={"ok": False})), "")
        compact = make_event("compact", text="kept latest pack about packing")
        self.assertIn("packing", indexable_body(compact))

        index = SessionEventIndex()
        index.sync(
            [
                make_event("meta", kind="session_start", extra={"repo": "packing"}),
                make_event("clear", extra={"note": "packing leftover"}),
                make_event("user", text="unrelated question about wiki pages"),
            ]
        )
        self.assertEqual(index.search("packing"), [])


class TestRanking(unittest.TestCase):
    def test_more_term_overlap_ranks_first(self):
        from harness.events import make_event
        from harness.session_fts import SessionEventIndex

        rare = make_event("user", text="once: packing")
        dense = make_event(
            "assistant",
            text="packing packing packing details about prefix-stable packing",
        )
        other = make_event("user", text="wiki generate dirty pages")
        index = SessionEventIndex()
        index.sync([rare, dense, other])
        hits = index.search("packing")
        self.assertGreaterEqual(len(hits), 2)
        self.assertEqual(hits[0].event_id, dense.id)
        self.assertGreater(hits[0].score, hits[1].score)


class TestIncrementalAndRebuild(unittest.TestCase):
    def test_append_indexes_incrementally(self):
        from harness.events import EventLog, make_event
        from harness.session_fts import SessionEventIndex

        index = SessionEventIndex()
        log = EventLog(fts=index)
        log.append(make_event("user", text="first question about wiki"))
        self.assertEqual(len(index.search("wiki")), 1)
        log.append(make_event("assistant", text="then we talked about packing"))
        packing = index.search("packing")
        self.assertEqual(len(packing), 1)
        self.assertEqual(packing[0].type, "assistant")
        self.assertEqual(len(index.search("wiki")), 1)

    def test_rebuild_on_migrate_and_stale_sidecar(self):
        from harness.events import migrate_legacy_session_file
        from harness.session_fts import SessionEventIndex, default_fts_path

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "legacy.jsonl")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "event": "session_start",
                            "id": "old",
                            "repo": "demo",
                            "profile": "default",
                        }
                    )
                    + "\n"
                )
                fh.write(
                    json.dumps(
                        {
                            "event": "turn",
                            "role": "user",
                            "text": "why keep chroma until turbovec gates pass?",
                        }
                    )
                    + "\n"
                )
            dest = migrate_legacy_session_file(src)
            self.assertTrue(os.path.isfile(dest))
            hits = SessionEventIndex.for_log(dest).search("chroma turbovec")
            self.assertTrue(hits)
            self.assertEqual(hits[0].type, "user")

            sidecar = default_fts_path(dest)
            self.assertTrue(os.path.isfile(sidecar))
            # Stale sidecar: wipe FTS rows but keep the file; sync must rebuild.
            stale = SessionEventIndex(sidecar)
            stale.invalidate()
            self.assertEqual(stale.search("chroma"), [])
            rebuilt = SessionEventIndex.for_log(dest)
            self.assertTrue(rebuilt.search("chroma"))


class TestEventSessionWiring(unittest.TestCase):
    def test_event_session_search_hits_recorded_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _event_session(session_dir=tmp, session_id="fts-1")
            session.record_turn(
                SessionTurn(role="user", text="how does prefix-stable packing work?")
            )
            session.record_turn(
                SessionTurn(role="assistant", text="freeze the system prefix")
            )
            hits = session.search_events("prefix-stable packing")
            self.assertTrue(hits)
            self.assertEqual(hits[0].type, "user")
            self.assertTrue(os.path.isfile(session.jsonl_path()))
            sidecar = session.jsonl_path().replace(".jsonl", ".fts.sqlite")
            self.assertTrue(os.path.isfile(sidecar))

    def test_slash_search_requires_event_session(self):
        session = Session(repo=".")
        self.assertFalse(session.event_session)
        missing = handle_slash(session, parse_slash("/search packing"))
        self.assertEqual(missing.kind, "search")
        self.assertIn("event-session", missing.message.lower())
        usage = handle_slash(
            _event_session(session_dir=None), parse_slash("/search")
        )
        self.assertIn("Usage", usage.message)

    def test_slash_search_hits_when_event_session_on(self):
        session = _event_session()
        session.record_turn(SessionTurn(role="user", text="compact older dialogue"))
        out = handle_slash(session, parse_slash("/search compact dialogue"))
        self.assertEqual(out.kind, "search")
        self.assertIn("compact", out.message.lower())
        help_out = handle_slash(session, parse_slash("/help"))
        self.assertIn("/search", help_out.message)

    def test_default_path_does_not_create_fts_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(repo="demo", session_dir=tmp, session_id="legacy-fts")
            session.record_turn(SessionTurn(role="user", text="hello packing"))
            session.record_turn(SessionTurn(role="assistant", text="world"))
            self.assertFalse(session.event_session)
            names = os.listdir(tmp)
            self.assertTrue(any(name.endswith(".jsonl") for name in names))
            self.assertFalse(any(name.endswith(".fts.sqlite") for name in names))


class TestSearchSessionTool(unittest.TestCase):
    def test_search_session_helper_returns_ranked_dicts(self):
        from harness.events import EventLog, make_event
        from harness.session_fts import search_session

        log = EventLog()
        log.append(make_event("user", text="decision: keep chroma default"))
        log.append(make_event("assistant", text="until turbovec recall gates pass"))
        payload = search_session("chroma default", events=log.events, top_k=5)
        self.assertIn("hits", payload)
        self.assertTrue(payload["hits"])
        hit = payload["hits"][0]
        self.assertEqual(hit["type"], "user")
        self.assertIn("event_id", hit)
        self.assertIn("snippet", hit)
        self.assertIn("ts", hit)
        self.assertIn("score", hit)

    def test_mcp_lists_and_calls_search_session(self):
        from harness.serve import handle_mcp

        with tempfile.TemporaryDirectory() as tmp:
            session = _event_session(session_dir=tmp, session_id="mcp-fts")
            session.record_turn(
                SessionTurn(role="user", text="why keep chroma until gates pass?")
            )
            listed = handle_mcp(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                service=None,
            )
            names = {t["name"] for t in listed["result"]["tools"]}
            self.assertIn("search_session", names)

            called = handle_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "search_session",
                        "arguments": {
                            "query": "chroma",
                            "session": session.jsonl_path(),
                        },
                    },
                },
                service=None,
            )
            blob = json.dumps(called)
            self.assertIn("chroma", blob.lower())
            self.assertIn("event_id", blob)


class TestCli(unittest.TestCase):
    def test_session_search_help_and_top_help(self):
        run = subprocess.run(
            [sys.executable, "main.py", "session", "search", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("event", run.stdout.lower())
        top = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertRegex(top.stdout, r"session search|session-search")

    def test_session_search_cli_hit_and_legacy_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _event_session(session_dir=tmp, session_id="cli-fts")
            session.record_turn(
                SessionTurn(role="user", text="how does prefix-stable packing work?")
            )
            hit = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "session",
                    "search",
                    "prefix-stable packing",
                    "--session",
                    session.jsonl_path(),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(hit.returncode, 0, hit.stderr + hit.stdout)
            self.assertIn("packing", hit.stdout.lower())
            self.assertIn("e", hit.stdout.lower())

            legacy = Session(repo="demo", session_dir=tmp, session_id="legacy-cli")
            legacy.record_turn(SessionTurn(role="user", text="packing lives here too"))
            refused = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "session",
                    "search",
                    "packing",
                    "--session",
                    legacy.jsonl_path(),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("event-session", (refused.stdout + refused.stderr).lower())

    def test_config_default_unchanged(self):
        self.assertFalse(DEFAULT_CONFIG["session"]["event_session"])
        self.assertNotIn("fts", DEFAULT_CONFIG["session"])


if __name__ == "__main__":
    unittest.main()
