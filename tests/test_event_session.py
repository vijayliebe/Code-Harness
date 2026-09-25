"""Event-sourced session + prefix-stable packing (DeepSeek steal #3).

Default interactive path stays on the mixed turn JSONL. Event log + derive
and frozen prefixes are opt-in (``--event-session`` / ``session.event_session``).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.context_builder import ContextBuilder
from harness.models import Chunk, EntityType, RetrievalResult
from harness.session import Session, SessionTurn, handle_slash, parse_slash


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _chunk(cid="func:harness/demo.py:fn:abcd", path="harness/demo.py", name="fn"):
    return Chunk(
        id=cid,
        content="def fn():\n    return 1\n",
        entity_id=":".join(cid.split(":")[:3]),
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=2,
        docstring="demo",
        metadata={},
    )


def _rr(chunk=None, score=0.9):
    return RetrievalResult(chunk=chunk or _chunk(), score=score, source="test")


def _dump(tag: str) -> str:
    lines = [f"# Query: {tag}", "## Relevant Code Context", "### File: harness/demo.py"]
    lines.extend(f"dump_line_{tag}_{i} = {i} " + ("x" * 16) for i in range(30))
    return "\n".join(lines)


def _pair(session: Session, i: int, *, dump: bool = False):
    session.record_turn(SessionTurn(role="user", text=f"question {i} about module_{i}"))
    session.record_turn(
        SessionTurn(
            role="assistant",
            text=f"answer {i}",
            chunk_ids=[f"func:harness/demo.py:fn_{i}:abcd"],
            packed_tokens=40,
            full_tokens=120,
            completion_tokens=10,
            loop_attempts=1,
            pack_mode="ccr_lite",
            paths=["harness/demo.py"],
            tool_name="retrieve",
            tool_args=f"question {i}",
            tool_result=_dump(f"turn{i}") if dump else "",
        )
    )


class TestEnablementDefaultOff(unittest.TestCase):
    def test_config_and_env_default_off(self):
        from harness.events import event_session_enabled

        self.assertFalse(DEFAULT_CONFIG["session"]["event_session"])
        self.assertFalse(DEFAULT_CONFIG["context"]["prefix_stable"])
        self.assertFalse(event_session_enabled())
        self.assertFalse(event_session_enabled(config=Config()))
        self.assertTrue(
            event_session_enabled(environ={"CODEHARNESS_EVENT_SESSION": "1"})
        )
        self.assertFalse(
            event_session_enabled(
                environ={"CODEHARNESS_EVENT_SESSION": "0"},
                config=Config.from_dict({"session": {"event_session": True}}),
            )
        )
        self.assertTrue(
            event_session_enabled(
                config=Config.from_dict({"session": {"event_session": True}})
            )
        )

    def test_chat_help_exposes_event_session_flag(self):
        run = subprocess.run(
            [sys.executable, "main.py", "chat", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--event-session", run.stdout)
        self.assertIn("--no-event-session", run.stdout)


class TestDefaultPathUnchanged(unittest.TestCase):
    def test_legacy_jsonl_still_uses_event_turn_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(repo="demo", session_dir=tmp, session_id="legacy-1")
            session.record_turn(SessionTurn(role="user", text="hello"))
            session.record_turn(
                SessionTurn(role="assistant", text="world", chunk_ids=["id-1"])
            )
            with open(session.jsonl_path(), encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            kinds = [row.get("event") for row in rows]
            self.assertIn("session_start", kinds)
            self.assertIn("turn", kinds)
            self.assertFalse(any(row.get("type") in ("user", "assistant") for row in rows))
            self.assertFalse(session.event_session)

    def test_compact_mutates_working_list_when_opt_in_off(self):
        session = Session(repo=".")
        for i in range(3):
            _pair(session, i)
        before = session.turn_count()
        session.compact()
        self.assertLess(session.turn_count(), before)
        self.assertTrue(any(t.role == "compact" for t in session.turns))


class TestAppendDeriveRoundtrip(unittest.TestCase):
    def test_append_derive_roundtrip(self):
        from harness.events import EventLog, derive_messages, make_event

        log = EventLog()
        log.append(make_event("meta", kind="session_start", repo="demo"))
        log.append(make_event("user", text="how does packing work?"))
        log.append(
            make_event(
                "tool_use",
                tool_name="retrieve",
                tool_args="how does packing work?",
                chunk_ids=["func:harness/ccr.py:pack_chunk:abcd"],
            )
        )
        result = log.append(
            make_event(
                "tool_result",
                text="packed preview",
                chunk_ids=["func:harness/ccr.py:pack_chunk:abcd"],
            )
        )
        log.append(make_event("assistant", text="see the packer"))
        messages = derive_messages(log.events)
        roles = [m.role for m in messages]
        self.assertEqual(roles, ["user", "tool_use", "tool_result", "assistant"])
        self.assertEqual(messages[0].text, "how does packing work?")
        self.assertEqual(messages[-1].text, "see the packer")
        self.assertEqual(messages[2].event_id, result.id)
        again = derive_messages(log.events)
        self.assertEqual([m.to_dict() for m in again], [m.to_dict() for m in messages])


class TestResumeEqualsLive(unittest.TestCase):
    def test_reload_events_rederives_same_history(self):
        from harness.events import EventLog

        with tempfile.TemporaryDirectory() as tmp:
            live = Session(
                repo="demo",
                session_dir=tmp,
                session_id="evt-1",
                event_session=True,
            )
            _pair(live, 0)
            _pair(live, 1)
            live_history = live.history_for_model()
            live_msgs = [m.to_dict() for m in live.derive_messages()]
            live_ids = live.latest_pack_ids()

            resumed = Session.resume(live.jsonl_path(), event_session=True)
            self.assertEqual([m.to_dict() for m in resumed.derive_messages()], live_msgs)
            self.assertEqual(resumed.history_for_model(), live_history)
            self.assertEqual(resumed.latest_pack_ids(), live_ids)
            self.assertEqual(resumed.turn_count(), live.turn_count())
            # No divergent parallel chat list — turns come from the same log.
            self.assertEqual(len(resumed.events), len(live.events))
            self.assertGreaterEqual(
                len(EventLog.load(live.jsonl_path()).events), 1
            )


class TestClearCompactAreEvents(unittest.TestCase):
    def test_compact_and_clear_append_events_not_rewrite_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(
                repo=".",
                session_dir=tmp,
                session_id="evt-2",
                event_session=True,
                clear_tool_results=True,
                clear_tool_keep=1,
            )
            for i in range(3):
                _pair(session, i, dump=True)
            raw_before = list(session.events)
            self.assertGreaterEqual(len(raw_before), 8)

            cleared = session.clear_tool_results(enabled=True)
            self.assertTrue(cleared.fired)
            self.assertTrue(any(e.type == "clear" for e in session.events))
            # Original dump events stay in the raw log.
            self.assertGreater(len(session.events), len(raw_before))
            dumps = [e for e in session.events if e.type == "tool_result"]
            self.assertTrue(any("dump_line" in (e.text or "") for e in dumps))

            compact = session.compact()
            self.assertGreater(compact.dropped_turns, 0)
            self.assertTrue(any(e.type == "compact" for e in session.events))
            self.assertTrue(any(t.role == "compact" for t in session.turns))
            # Shadowed dumps remain in the append-only file.
            with open(session.jsonl_path(), encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            types = [row.get("type") for row in rows]
            self.assertIn("clear", types)
            self.assertIn("compact", types)
            self.assertIn("tool_result", types)
            self.assertIn("user", types)

            derived = session.derive_messages()
            self.assertTrue(any(m.kind == "compact" for m in derived))
            # Latest pack survives on the derived surface.
            self.assertEqual(session.latest_pack_ids(), ["func:harness/demo.py:fn_2:abcd"])

    def test_slash_compact_records_event(self):
        session = Session(repo=".", event_session=True)
        for i in range(3):
            _pair(session, i)
        out = handle_slash(session, parse_slash("/compact"))
        self.assertEqual(out.kind, "compact")
        self.assertTrue(any(e.type == "compact" for e in session.events))


class TestMigrateLegacy(unittest.TestCase):
    def test_migrate_helper_reads_old_turn_jsonl(self):
        from harness.events import migrate_legacy_events

        rows = [
            {"event": "session_start", "id": "old", "repo": "demo", "profile": "default"},
            {"event": "turn", "role": "user", "text": "hello", "chunk_ids": []},
            {
                "event": "turn",
                "role": "assistant",
                "text": "world",
                "chunk_ids": ["id-1"],
                "tool_name": "retrieve",
                "tool_result": "dump",
            },
        ]
        events = migrate_legacy_events(rows)
        types = [e.type for e in events]
        self.assertEqual(types[0], "meta")
        self.assertIn("user", types)
        self.assertIn("assistant", types)
        self.assertIn("tool_use", types)
        self.assertIn("tool_result", types)


class TestPrefixBytesStable(unittest.TestCase):
    def test_two_packs_same_config_same_prefix_bytes(self):
        from harness.prefix import PREFIX_INVARIANTS, snapshot_prefix

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ARCHITECTURE.md"), "w", encoding="utf-8") as fh:
                fh.write("# Architecture\nStable prefix docs.\n")
            with open(os.path.join(tmp, "AGENTS.md"), "w", encoding="utf-8") as fh:
                fh.write("Be careful.\n")
            os.makedirs(os.path.join(tmp, "knowledge", "wiki"), exist_ok=True)
            with open(
                os.path.join(tmp, "knowledge", "wiki", "vector-store.md"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write("# Vector store\nChroma stays default.\n")
            with open(
                os.path.join(tmp, "knowledge", "wiki", "coffee.md"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write("# Coffee\nDo not index grounds.\n")
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "context": {
                        "pack_mode": "ccr_lite",
                        "knowledge_prefix": True,
                        "prefix_stable": True,
                    },
                    "ccr": {"spill_dir": os.path.join(tmp, "ccr")},
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            a = builder.build_context_report("why is Chroma the default?", [_rr()])
            b = builder.build_context_report("how do we brew coffee?", [_rr(_chunk(cid="other:id"))])
            self.assertTrue(a.prefix_bytes)
            self.assertEqual(a.prefix_bytes, b.prefix_bytes)
            self.assertEqual(a.prefix_hash, b.prefix_hash)
            self.assertIn("ARCHITECTURE.md", a.prefix_bytes)
            self.assertIn("retrieve", a.prefix_bytes)
            freeze = snapshot_prefix(builder)
            self.assertEqual(freeze.digest, a.prefix_hash)
            self.assertTrue(PREFIX_INVARIANTS)
            # Volatile hits still differ / sit after the frozen prefix.
            self.assertNotEqual(a.context, b.context)

    def test_query_rank_still_runs_when_prefix_stable_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "knowledge", "wiki"), exist_ok=True)
            with open(
                os.path.join(tmp, "knowledge", "wiki", "vector-store.md"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write("# Vector store\nChroma stays default.\n")
            with open(
                os.path.join(tmp, "knowledge", "wiki", "coffee.md"),
                "w",
                encoding="utf-8",
            ) as fh:
                fh.write("# Coffee ritual\nDo not index grounds.\n")
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "context": {"knowledge_prefix": True, "prefix_stable": False},
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            chroma = builder.build_context_report(
                "why is Chroma the default vector store?", [_rr()]
            )
            coffee = builder.build_context_report("coffee ritual grounds", [_rr()])
            self.assertIn("Knowledge vault", chroma.context)
            self.assertNotEqual(chroma.context, coffee.context)


class TestEventSessionWiresPrefix(unittest.TestCase):
    def test_event_session_freezes_prefix_route(self):
        from harness.prefix import snapshot_prefix

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "AGENTS.md"), "w", encoding="utf-8") as fh:
                fh.write("Be careful.\n")
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "session": {"event_session": True},
                    "context": {"prefix_stable": True},
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            session = Session(repo="demo", event_session=True, config=config)
            freeze = snapshot_prefix(builder)
            session.bind_prefix(freeze)
            builder.bind_prefix_freeze(freeze)
            first = builder.build_context_report("q1", [_rr()])
            second = builder.build_context_report("q2", [_rr(_chunk(cid="x"))])
            self.assertEqual(first.prefix_bytes, second.prefix_bytes)
            self.assertEqual(session.prefix_digest, freeze.digest)
            self.assertTrue(any(e.type == "system" for e in session.events) or session.prefix_digest)


if __name__ == "__main__":
    unittest.main()
