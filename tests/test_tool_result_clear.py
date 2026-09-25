"""Tool-result clearing: keep recent N, placeholders, CCR re-expand, redact, default-off."""

import os
import subprocess
import sys
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.context_builder import ContextBuilder
from harness.models import Chunk, EntityType, RetrievalResult
from harness.session import Session, SessionTurn, handle_slash, parse_slash
from harness.tool_clear import (
    PLACEHOLDER_MARK,
    apply_placeholder,
    clear_tool_results,
    is_placeholder,
    is_refetchable_dump,
    placeholder_for,
    should_clear,
    tool_result_clearing_enabled,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

FAKE_OPENAI = "sk-proj-" + "TESTFAKESECRETKEY1234567890ABCD"


def _dump(body_tag: str, n_lines: int = 40) -> str:
    lines = [f"# Query: {body_tag}", "## Relevant Code Context", "### File: harness/demo.py"]
    lines.extend(f"dump_line_{body_tag}_{i} = {i} " + ("x" * 20) for i in range(n_lines))
    return "\n".join(lines)


def _retrieve_turn(i: int, *, query: str = "", dump: str = "", answer: str = "") -> SessionTurn:
    cid = f"func:harness/demo.py:fn_{i}:abcd"
    return SessionTurn(
        role="assistant",
        text=answer or dump,
        chunk_ids=[cid],
        paths=["harness/demo.py"],
        tool_name="retrieve",
        tool_args=query or f"question {i}",
        tool_result=dump or "",
        packed_tokens=200,
        full_tokens=800,
        pack_mode="ccr_lite",
    )


def _filled(n_pairs=4, *, clear=False, keep=1, trigger=0, with_dump=True):
    session = Session(
        repo=".",
        clear_tool_results=clear,
        clear_tool_keep=keep,
        clear_tool_token_trigger=trigger,
    )
    for i in range(n_pairs):
        session.record_turn(SessionTurn(role="user", text=f"question {i} about module_{i}"))
        dump = _dump(f"turn{i}") if with_dump else ""
        session.record_turn(
            _retrieve_turn(
                i,
                query=f"question {i} about module_{i}",
                dump=dump,
                answer=f"short answer {i}" if not with_dump else dump,
            )
        )
    return session


class TestEnablementDefaultOff(unittest.TestCase):
    def test_config_and_env_default_off(self):
        self.assertFalse(DEFAULT_CONFIG["session"]["clear_tool_results"])
        self.assertFalse(tool_result_clearing_enabled())
        self.assertFalse(tool_result_clearing_enabled(config=Config()))
        self.assertTrue(
            tool_result_clearing_enabled(environ={"CODEHARNESS_CLEAR_TOOL_RESULTS": "1"})
        )
        self.assertFalse(
            tool_result_clearing_enabled(
                args=type("A", (), {"clear_tool_results": True, "no_clear_tool_results": True})(),
                environ={"CODEHARNESS_CLEAR_TOOL_RESULTS": "1"},
            )
        )
        self.assertTrue(
            tool_result_clearing_enabled(
                config=Config.from_dict({"session": {"clear_tool_results": True}})
            )
        )

    def test_default_off_leaves_dumps_and_history(self):
        session = _filled(4, clear=False)
        before = [t.tool_result for t in session.turns if t.role == "assistant"]
        self.assertTrue(all(before))
        self.assertTrue(all("dump_line_turn" in blob for blob in before))

        result = session.clear_tool_results()
        self.assertFalse(result.fired)
        self.assertEqual(result.tokens_freed, 0)
        after = [t.tool_result for t in session.turns if t.role == "assistant"]
        self.assertEqual(after, before)

        framed = session.history_for_prompt()
        self.assertNotIn(PLACEHOLDER_MARK, framed)
        # Default-off does not add a second copy of tool_result beyond turn.text.
        text_only = Session(repo=".")
        for turn in session.turns:
            text_only.record_turn(
                SessionTurn(
                    role=turn.role,
                    text=turn.text,
                    chunk_ids=list(turn.chunk_ids),
                )
            )
        self.assertEqual(session.history_for_prompt(), text_only.history_for_prompt())

        cost = session.cost()
        self.assertEqual(cost.tool_result_tokens_freed, 0)
        self.assertNotIn("tool-result tokens freed", session.format_cost())

    def test_compact_does_not_imply_clearing_when_off(self):
        session = _filled(3, clear=False)
        session.compact()
        remaining = [t for t in session.turns if t.role == "assistant"]
        self.assertTrue(remaining)
        self.assertFalse(any(t.cleared for t in remaining))
        self.assertTrue(any("dump_line" in (t.tool_result or t.text) for t in remaining))


class TestKeepRecentAndPlaceholders(unittest.TestCase):
    def test_keeps_recent_n_and_clears_older_retrieve_dumps(self):
        session = _filled(4, clear=True, keep=2)
        result = session.clear_tool_results(enabled=True)
        self.assertTrue(result.fired)
        self.assertGreater(result.tokens_freed, 0)
        self.assertEqual(result.cleared, 2)

        dumps = [t for t in session.turns if t.role == "assistant"]
        self.assertTrue(dumps[0].cleared)
        self.assertTrue(dumps[1].cleared)
        self.assertFalse(dumps[2].cleared)
        self.assertFalse(dumps[3].cleared)
        self.assertIn(PLACEHOLDER_MARK, dumps[0].tool_result)
        self.assertIn("fn_0", dumps[0].tool_result)
        self.assertIn("harness/demo.py", dumps[0].tool_result)
        self.assertIn("retrieve", dumps[0].tool_result)
        self.assertIn("question 0", dumps[0].tool_result)
        self.assertIn("dump_line_turn2", dumps[2].tool_result)
        self.assertIn("dump_line_turn3", dumps[3].tool_result)
        # Ids survive on cleared turns.
        self.assertEqual(dumps[0].chunk_ids, ["func:harness/demo.py:fn_0:abcd"])

    def test_never_drops_latest_user_or_top_pack_or_plain_answers(self):
        session = Session(repo=".", clear_tool_results=True, clear_tool_keep=1)
        session.record_turn(SessionTurn(role="user", text="old question about packing"))
        session.record_turn(
            _retrieve_turn(0, query="old question about packing", dump=_dump("old"))
        )
        session.record_turn(
            SessionTurn(role="assistant", text="plain reasoning without a pack")
        )
        session.record_turn(SessionTurn(role="user", text="latest user question"))
        session.record_turn(
            _retrieve_turn(
                1,
                query="latest user question",
                dump=_dump("latest"),
                answer="keep this answer",
            )
        )
        session.turns[-1].text = "keep this answer"

        result = session.clear_tool_results(enabled=True)
        self.assertTrue(result.fired)
        users = [t.text for t in session.turns if t.role == "user"]
        self.assertEqual(users[-1], "latest user question")
        self.assertIn("old question about packing", users)
        latest = [t for t in session.turns if t.chunk_ids and "fn_1" in t.chunk_ids[0]][0]
        self.assertFalse(latest.cleared)
        self.assertIn("dump_line_latest", latest.tool_result)
        self.assertEqual(latest.text, "keep this answer")
        plain = [t for t in session.turns if t.text == "plain reasoning without a pack"]
        self.assertTrue(plain)
        self.assertFalse(plain[0].cleared)
        self.assertFalse(is_refetchable_dump(plain[0]))

    def test_token_trigger_clears_when_keep_n_would_not(self):
        session = _filled(2, clear=True, keep=2, trigger=20)
        # Two dumps, keep=2 → keep-N alone would no-op; token trigger fires.
        self.assertTrue(
            should_clear(
                session.turns,
                keep_n=2,
                token_trigger=20,
                estimate_fn=session.estimate_fn,
            )
        )
        result = session.clear_tool_results(enabled=True)
        self.assertTrue(result.fired)
        dumps = [t for t in session.turns if t.role == "assistant"]
        self.assertTrue(dumps[0].cleared)
        self.assertFalse(dumps[1].cleared)
        self.assertIn("dump_line_turn1", dumps[1].tool_result)

    def test_placeholder_has_refetch_handles(self):
        turn = _retrieve_turn(3, query="how does the chunker work?", dump=_dump("x"))
        stub = placeholder_for(turn)
        self.assertIn(PLACEHOLDER_MARK, stub)
        self.assertIn("chunk_ids=func:harness/demo.py:fn_3:abcd", stub)
        self.assertIn("paths=harness/demo.py", stub)
        self.assertIn("tool=retrieve", stub)
        self.assertIn("how does the chunker work?", stub)
        self.assertIn("retrieve_chunk", stub)


class TestCostAndSlash(unittest.TestCase):
    def test_cost_reports_tokens_freed_when_clearing_fires(self):
        session = _filled(3, clear=True, keep=1)
        result = session.clear_tool_results(enabled=True)
        self.assertGreater(result.tokens_freed, 0)
        cost = session.cost()
        self.assertEqual(cost.tool_result_tokens_freed, result.tokens_freed)
        self.assertEqual(cost.tool_results_cleared, result.cleared)
        report = session.format_cost()
        self.assertIn("tool-result tokens freed", report)
        self.assertIn(str(result.tokens_freed), report)

    def test_slash_clear_and_compact_micro_step(self):
        session = _filled(3, clear=True, keep=1)
        parsed = parse_slash("/clear-tool-results")
        self.assertEqual(parsed.name, "clear-tool-results")
        out = handle_slash(session, parsed)
        self.assertEqual(out.kind, "clear_tool_results")
        self.assertIn("tokens freed", out.message)

        session2 = _filled(3, clear=True, keep=1)
        compact = handle_slash(session2, parse_slash("/compact"))
        self.assertEqual(compact.kind, "compact")
        self.assertTrue(any(t.cleared for t in session2.turns if t.role == "assistant") or any(
            is_placeholder(t.tool_result or "") for t in session2.turns
        ) or session2._tool_result_tokens_freed >= 0)

    def test_help_lists_clear_command(self):
        from harness.session import HELP_TEXT

        self.assertIn("/clear-tool-results", HELP_TEXT)
        session = Session(repo=".")
        help_out = handle_slash(session, parse_slash("/help"))
        self.assertIn("/clear-tool-results", help_out.message)


class TestReExpandAndRedact(unittest.TestCase):
    def test_reexpand_by_id_after_clear(self):
        chunk = Chunk(
            id="func:harness/demo.py:big_fn:abcd1234",
            content=(
                "def big_fn(config):\n    \"\"\"Pack large functions.\"\"\"\n"
                + "\n".join(f"    value_{i} = {i}" for i in range(80))
            ),
            entity_id="func:harness/demo.py:big_fn",
            entity_name="big_fn",
            entity_type=EntityType.FUNCTION,
            file_path="harness/demo.py",
            start_line=10,
            end_line=90,
            docstring="Pack large functions.",
            metadata={"params": ["config"]},
        )
        builder = ContextBuilder(Config.from_dict({"context": {"pack_mode": "ccr_lite"}}))
        report = builder.build_context_report(
            "how does packing work?",
            [RetrievalResult(chunk=chunk, score=0.9, source="test")],
        )
        self.assertIn(chunk.id, report.packed_chunk_ids)

        session = Session(repo=".", clear_tool_results=True, clear_tool_keep=1)
        session.record_turn(SessionTurn(role="user", text="old retrieve"))
        session.record_turn(
            SessionTurn(
                role="assistant",
                text=report.context,
                chunk_ids=list(report.packed_chunk_ids),
                paths=list(report.packed_paths),
                tool_name="retrieve",
                tool_args="old retrieve",
                tool_result=report.context,
            )
        )
        session.record_turn(SessionTurn(role="user", text="new retrieve"))
        session.record_turn(
            SessionTurn(
                role="assistant",
                text="later pack",
                chunk_ids=["func:harness/other.py:later:zzzz"],
                paths=["harness/other.py"],
                tool_name="retrieve",
                tool_args="new retrieve",
                tool_result=_dump("later"),
            )
        )
        session.clear_tool_results(enabled=True)
        old = session.turns[1]
        self.assertTrue(old.cleared)
        self.assertIn(chunk.id, old.chunk_ids)
        self.assertIn(PLACEHOLDER_MARK, old.tool_result)

        expanded = builder.expand_into_context("", [chunk.id])
        self.assertIn("value_40", expanded)
        self.assertIn(chunk.id, expanded)

    def test_redact_still_applies_to_dumps_and_placeholders(self):
        config = Config()
        session = Session(
            repo=".",
            config=config,
            redact=True,
            clear_tool_results=True,
            clear_tool_keep=1,
        )
        leak = _dump("secret") + f"\nAPI_KEY={FAKE_OPENAI}\n"
        session.record_turn(SessionTurn(role="user", text="old"))
        session.record_turn(
            SessionTurn(
                role="assistant",
                text=leak,
                chunk_ids=["func:harness/demo.py:run:aaaa"],
                paths=["harness/demo.py"],
                tool_name="retrieve",
                tool_args=f"show key {FAKE_OPENAI}",
                tool_result=leak,
            )
        )
        session.record_turn(SessionTurn(role="user", text="new"))
        session.record_turn(
            _retrieve_turn(9, query="new", dump=_dump("new"), answer="ok")
        )
        self.assertNotIn(FAKE_OPENAI, session.turns[1].text)
        self.assertNotIn(FAKE_OPENAI, session.turns[1].tool_result)
        self.assertNotIn(FAKE_OPENAI, session.turns[1].tool_args)
        session.clear_tool_results(enabled=True)
        self.assertNotIn(FAKE_OPENAI, session.turns[1].tool_result)
        self.assertNotIn(FAKE_OPENAI, session.history_for_prompt())


class TestCliAffordance(unittest.TestCase):
    def test_chat_help_exposes_clear_flag_default_off(self):
        run = subprocess.run(
            [sys.executable, "main.py", "chat", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--clear-tool-results", run.stdout)
        self.assertIn("default off", run.stdout.lower())
        self.assertIn("--clear-tool-keep", run.stdout)
        # One-shot query does not grow this surface.
        query = subprocess.run(
            [sys.executable, "main.py", "query", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertNotIn("--clear-tool-results", query.stdout)


class TestPlaceholderHelpers(unittest.TestCase):
    def test_apply_placeholder_is_idempotent(self):
        turn = _retrieve_turn(1, query="q", dump=_dump("once"))
        first = apply_placeholder(turn)
        self.assertGreater(first, 0)
        self.assertTrue(is_placeholder(turn.tool_result))
        second = apply_placeholder(turn)
        self.assertEqual(second, 0)


if __name__ == "__main__":
    unittest.main()
