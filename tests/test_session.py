"""Tests for interactive session slash commands, cost, compact, and cite style."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.context_builder import ContextBuilder


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


class TestSlashParse(unittest.TestCase):
    def test_parse_named_commands_and_aliases(self):
        from harness.session import parse_slash

        compact = parse_slash("  /compact  ")
        self.assertIsNotNone(compact)
        self.assertEqual(compact.name, "compact")
        self.assertEqual(compact.args, "")

        cost = parse_slash("/cost")
        self.assertEqual(cost.name, "cost")

        help_cmd = parse_slash("/help")
        self.assertEqual(help_cmd.name, "help")

        self.assertEqual(parse_slash("/exit").name, "exit")
        self.assertEqual(parse_slash("/quit").canonical, "exit")
        self.assertEqual(parse_slash("/EXIT").canonical, "exit")

        profile = parse_slash("/profile sage")
        self.assertEqual(profile.name, "profile")
        self.assertEqual(profile.args, "sage")

        expand = parse_slash("/expand harness/chunker.py:CodeChunker")
        self.assertEqual(expand.canonical, "expand")
        self.assertEqual(expand.args, "harness/chunker.py:CodeChunker")
        self.assertEqual(parse_slash("/retrieve chunk-id").canonical, "expand")

        memory = parse_slash("/memory brief")
        self.assertEqual(memory.name, "memory")
        self.assertEqual(memory.args, "brief")

        wiki = parse_slash("/wiki architecture")
        self.assertEqual(wiki.name, "wiki")
        self.assertEqual(wiki.args, "architecture")

    def test_non_slash_is_a_query(self):
        from harness.session import parse_slash

        self.assertIsNone(parse_slash("how does the chunker work?"))
        self.assertIsNone(parse_slash(""))
        self.assertIsNone(parse_slash("   "))
        self.assertIsNone(parse_slash("compact"))

    def test_unknown_slash_is_still_parsed(self):
        from harness.session import parse_slash

        cmd = parse_slash("/nosuch extra")
        self.assertEqual(cmd.name, "nosuch")
        self.assertEqual(cmd.args, "extra")
        self.assertEqual(cmd.canonical, "nosuch")


class TestSessionCost(unittest.TestCase):
    def test_cost_tracks_prompt_completion_and_loop_attempts(self):
        from harness.session import Session, SessionTurn

        session = Session(repo=".", profile="default")
        session.record_turn(
            SessionTurn(
                role="user",
                text="how does packing work?",
            )
        )
        session.record_turn(
            SessionTurn(
                role="assistant",
                text="see the packer",
                chunk_ids=["func:harness/ccr.py:pack_chunk:abcd"],
                packed_tokens=120,
                full_tokens=400,
                completion_tokens=40,
                loop_attempts=1,
                pack_mode="ccr_lite",
            )
        )
        session.record_turn(
            SessionTurn(
                role="user",
                text="and the loop?",
            )
        )
        session.record_turn(
            SessionTurn(
                role="assistant",
                text="bounded retries",
                chunk_ids=["func:harness/loop.py:QueryLoop.run:ef01"],
                packed_tokens=80,
                full_tokens=200,
                completion_tokens=20,
                loop_attempts=2,
                pack_mode="ccr_lite",
            )
        )

        cost = session.cost()
        self.assertEqual(cost.prompt_tokens, 200)
        self.assertEqual(cost.packed_tokens, 200)
        self.assertEqual(cost.full_tokens, 600)
        self.assertEqual(cost.completion_tokens, 60)
        self.assertEqual(cost.loop_attempts, 3)
        self.assertIsNone(cost.approx_usd)

        report = session.format_cost()
        self.assertIn("200", report)
        self.assertIn("600", report)
        self.assertIn("60", report)
        self.assertIn("3", report)
        self.assertRegex(report.lower(), r"rate unknown|n/a|unknown")

    def test_cost_approx_usd_when_rate_known(self):
        from harness.session import Session, SessionTurn

        session = Session(
            repo=".",
            input_usd_per_1m=1.0,
            output_usd_per_1m=5.0,
        )
        session.record_turn(
            SessionTurn(
                role="assistant",
                text="ok",
                packed_tokens=1_000_000,
                full_tokens=2_000_000,
                completion_tokens=1_000_000,
                loop_attempts=1,
            )
        )
        cost = session.cost()
        self.assertAlmostEqual(cost.approx_usd, 6.0)
        self.assertIn("$", session.format_cost())


class TestSessionCompact(unittest.TestCase):
    def _filled_session(self, n_pairs=4):
        from harness.session import Session, SessionTurn

        session = Session(repo=".")
        for i in range(n_pairs):
            session.record_turn(
                SessionTurn(role="user", text=f"question {i} about module_{i} " * 20)
            )
            session.record_turn(
                SessionTurn(
                    role="assistant",
                    text=("answer body " * 80) + f" for turn {i}",
                    chunk_ids=[f"func:harness/demo.py:fn_{i}:abcd"],
                    packed_tokens=400,
                    full_tokens=1200,
                    completion_tokens=50,
                    loop_attempts=1,
                )
            )
        return session

    def test_compact_reduces_turn_count_and_history_tokens(self):
        session = self._filled_session(4)
        before_turns = session.turn_count()
        before_history = session.history_tokens()
        self.assertGreaterEqual(before_turns, 8)
        self.assertGreater(before_history, 200)

        result = session.compact()
        after_turns = session.turn_count()
        after_history = session.history_tokens()

        self.assertLess(after_turns, before_turns)
        self.assertLess(after_history, before_history)
        self.assertGreater(result.dropped_turns, 0)
        self.assertIn("question 0", result.summary)
        # Latest pack ids must survive (Strands: never drop latest pack).
        latest_ids = session.latest_pack_ids()
        self.assertEqual(latest_ids, ["func:harness/demo.py:fn_3:abcd"])
        self.assertTrue(any("fn_3" in tid for tid in session.all_kept_chunk_ids()))

        # Cumulative spend is not erased by compact.
        cost = session.cost()
        self.assertEqual(cost.packed_tokens, 1600)
        self.assertEqual(cost.completion_tokens, 200)

    def test_compact_keeps_user_lines_and_last_assistant(self):
        session = self._filled_session(3)
        session.compact()
        user_texts = [t.text for t in session.turns if t.role == "user"]
        assistant_texts = [t.text for t in session.turns if t.role == "assistant"]
        compact_turns = [t for t in session.turns if t.role == "compact"]
        self.assertTrue(compact_turns)
        self.assertTrue(any("question 0" in t.text for t in session.turns))
        self.assertTrue(any("question 2" in t for t in user_texts) or any(
            "question 2" in t.text for t in session.turns
        ))
        self.assertTrue(any("turn 2" in t for t in assistant_texts))
        # Older assistant bodies are not kept verbatim.
        self.assertFalse(any("turn 0" in t for t in assistant_texts))

    def test_budget_never_drops_latest_pack(self):
        from harness.session import Session, SessionTurn

        session = Session(repo=".", budget_tokens=80)
        session.record_turn(SessionTurn(role="user", text="old " * 40))
        session.record_turn(
            SessionTurn(
                role="assistant",
                text="old pack body " * 40,
                chunk_ids=["old-id"],
                packed_tokens=200,
            )
        )
        session.record_turn(SessionTurn(role="user", text="new question"))
        session.record_turn(
            SessionTurn(
                role="assistant",
                text="latest pack body " * 40,
                chunk_ids=["latest-id"],
                packed_tokens=200,
            )
        )
        framed = session.history_for_prompt()
        self.assertIn("latest-id", framed)
        self.assertIn("new question", framed)


class TestCitationInstruction(unittest.TestCase):
    def test_system_prompt_requires_path_symbol_cites(self):
        from harness.session import CITATION_INSTRUCTION

        prompt = ContextBuilder(Config()).build_system_prompt()
        self.assertIn("`path:symbol`", prompt)
        self.assertIn("path:symbol", CITATION_INSTRUCTION)
        self.assertIn(CITATION_INSTRUCTION, prompt)


class TestSageProfile(unittest.TestCase):
    def test_sage_raises_pack_budget_and_graph_expand_not_rrf(self):
        from harness.session import apply_profile, known_profiles

        self.assertIn("default", known_profiles())
        self.assertIn("sage", known_profiles())

        config = Config()
        rrf_before = (
            config.retrieval["dense_weight"],
            config.retrieval["sparse_weight"],
            config.retrieval["graph_weight"],
        )
        default_neighbors = config.retrieval["expand_neighbors"]
        default_mode = config.context["pack_mode"]
        self.assertEqual(default_mode, "full")

        apply_profile(config, "sage")
        self.assertEqual(config.context["pack_mode"], "ccr_lite")
        self.assertGreaterEqual(config.retrieval.get("max_loops", 0), 1)
        self.assertGreater(config.retrieval["expand_neighbors"], default_neighbors)
        self.assertGreaterEqual(config.context.get("max_tokens_multiplier", 2), 3)
        self.assertIn("explain", config.ccr.get("expand_on") or [])
        self.assertEqual(
            (
                config.retrieval["dense_weight"],
                config.retrieval["sparse_weight"],
                config.retrieval["graph_weight"],
            ),
            rrf_before,
        )

        with self.assertRaises(ValueError):
            apply_profile(Config(), "apply")

    def test_default_profile_leaves_one_shot_pack_mode(self):
        from harness.session import apply_profile

        config = Config()
        apply_profile(config, "default")
        self.assertEqual(config.context["pack_mode"], "full")
        self.assertEqual(config.retrieval["max_loops"], 0)
        self.assertEqual(DEFAULT_CONFIG["retrieval"]["max_loops"], 0)


class TestExpandOnExplain(unittest.TestCase):
    def test_explain_query_or_high_omit_triggers_expand(self):
        from harness.session import should_auto_expand

        omitted = ["a", "b"]
        packed = ["a", "b", "c"]
        self.assertTrue(
            should_auto_expand(
                query="explain how the chunker works",
                expand_on=["explain"],
                omitted_ids=omitted,
                packed_ids=packed,
                max_loops=0,
            )
        )
        self.assertFalse(
            should_auto_expand(
                query="where is CodeChunker",
                expand_on=["explain"],
                omitted_ids=["a"],
                packed_ids=packed,
                max_loops=0,
            )
        )
        # Omitted > 50% and no tool loop → expand even without cue.
        self.assertTrue(
            should_auto_expand(
                query="where is CodeChunker",
                expand_on=[],
                omitted_ids=["a", "b"],
                packed_ids=packed,
                max_loops=0,
            )
        )
        self.assertFalse(
            should_auto_expand(
                query="where is CodeChunker",
                expand_on=[],
                omitted_ids=["a", "b"],
                packed_ids=packed,
                max_loops=1,
            )
        )


class TestSessionJsonl(unittest.TestCase):
    def test_turns_append_jsonl_under_session_dir(self):
        from harness.session import Session, SessionTurn

        with tempfile.TemporaryDirectory() as tmp:
            session = Session(repo="demo", session_dir=tmp, session_id="20260925-t1")
            session.record_turn(SessionTurn(role="user", text="hello"))
            session.record_turn(
                SessionTurn(
                    role="assistant",
                    text="world",
                    chunk_ids=["id-1"],
                    packed_tokens=10,
                    full_tokens=20,
                    loop_attempts=1,
                )
            )
            path = session.jsonl_path()
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            kinds = [row.get("event") for row in rows]
            self.assertIn("session_start", kinds)
            self.assertIn("turn", kinds)
            self.assertEqual(rows[0]["profile"], "default")
            self.assertEqual(rows[0]["repo"], "demo")


class TestHandleSlash(unittest.TestCase):
    def test_help_cost_exit_and_unknown(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(repo=".")
        help_out = handle_slash(session, parse_slash("/help"))
        self.assertEqual(help_out.kind, "help")
        self.assertIn("/compact", help_out.message)
        self.assertIn("/cost", help_out.message)
        self.assertIn("/exit", help_out.message)

        cost_out = handle_slash(session, parse_slash("/cost"))
        self.assertEqual(cost_out.kind, "cost")
        self.assertIn("0", cost_out.message)

        exit_out = handle_slash(session, parse_slash("/quit"))
        self.assertTrue(exit_out.should_exit)

        unknown = handle_slash(session, parse_slash("/nosuch"))
        self.assertEqual(unknown.kind, "unknown")

        profile = handle_slash(session, parse_slash("/profile sage"))
        self.assertEqual(profile.kind, "profile")
        self.assertEqual(profile.profile, "sage")
        self.assertEqual(session.profile, "sage")


class TestCliAffordance(unittest.TestCase):
    def test_chat_and_session_aliases_expose_profile(self):
        for command in ("chat", "session", "repl", "interactive"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--profile", run.stdout)
            self.assertIn("sage", run.stdout)
            self.assertIn("--clear-tool-results", run.stdout)

        top = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertRegex(top.stdout, r"chat|session|interactive")


if __name__ == "__main__":
    unittest.main()
