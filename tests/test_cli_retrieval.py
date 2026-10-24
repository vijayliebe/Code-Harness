"""Config-gated CLI / config retrieval (default stays off)."""

import unittest

from harness.cli_retrieval import (
    argparse_catalog_from_chunks,
    apply_cli_retrieval_config,
    infer_cli_symbols,
    index_text_for_chunk,
    make_cli_doc_chunks,
    parse_argparse_commands,
    tokenize,
)
from harness.config import DEFAULT_CONFIG, Config
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult
from harness.retriever import Retriever


class FakeEmbedder:
    def embed_query(self, query, expand=True):
        return [0.1, 0.2, 0.3]


class FakeVectorStore:
    supports_allowlist = False

    def search(self, embedding, top_k=10, repo_name=None, allowlist_ids=None):
        return []


def _chunk(name, path="main.py", content=None, entity_id=None):
    body = content if content is not None else f"def {name}(args):\n    return 1\n"
    eid = entity_id or f"func:{path}:{name}"
    return Chunk(
        id=f"{eid}:aaaa",
        content=body,
        entity_id=eid,
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=3,
    )


def _retriever(**cli_kwargs):
    cfg = Config()
    cli = dict(cfg.retrieval.get("cli_retrieval") or {})
    cli.update(cli_kwargs)
    cfg.retrieval["cli_retrieval"] = cli
    cfg.retrieval["expand_neighbors"] = 0
    kg = KnowledgeGraph(cfg, repo_name="fixture")
    kg.enabled = False
    return Retriever(cfg, FakeEmbedder(), FakeVectorStore(), kg, repo_name="fixture")


SAMPLE_ARGPARSE = '''
def build():
    subparsers = parser.add_subparsers()
    q = subparsers.add_parser("query", help="Query the indexed repository")
    q.set_defaults(func=cmd_query)
    idx = subparsers.add_parser("index", help="Index a repository")
    idx.set_defaults(func=cmd_index)
    ev = subparsers.add_parser(
        "eval",
        help="Score retrieval against a golden suite (no LLM)",
    )
    ev.set_defaults(func=cmd_eval)
    srv = subparsers.add_parser(
        "serve",
        help="Serve POST /v1/retrieve + MCP on 127.0.0.1",
    )
'''


class TestCliRetrievalDefaults(unittest.TestCase):
    def test_default_is_doc_index_only(self):
        cli = DEFAULT_CONFIG["retrieval"]["cli_retrieval"]
        self.assertFalse(cli.get("ident_split"))
        self.assertFalse(cli.get("argparse_enrich"))
        self.assertTrue(cli.get("doc_index"))
        self.assertEqual(float(cli.get("symbol_boost") or 0), 0.0)
        live = Config().retrieval.get("cli_retrieval") or {}
        self.assertTrue(live.get("doc_index"))
        self.assertFalse(live.get("ident_split"))
        self.assertEqual(float(live.get("symbol_boost") or 0), 0.0)


class TestIdentSplit(unittest.TestCase):
    def test_plain_keeps_cmd_query_whole(self):
        tokens = tokenize("def cmd_query(args):")
        self.assertIn("cmd_query", tokens)
        self.assertNotIn("query", tokens)

    def test_split_adds_cmd_and_query(self):
        tokens = tokenize("def cmd_query(args):", ident_split=True)
        self.assertIn("cmd_query", tokens)
        self.assertIn("cmd", tokens)
        self.assertIn("query", tokens)

    def test_split_camel_case(self):
        tokens = tokenize("QueryLoop", ident_split=True)
        self.assertIn("queryloop", tokens)
        self.assertIn("query", tokens)
        self.assertIn("loop", tokens)

    def test_retriever_respects_flag(self):
        off = _retriever(ident_split=False)
        on = _retriever(ident_split=True)
        self.assertEqual(off._tokenize("cmd_query"), ["cmd_query"])
        self.assertIn("query", on._tokenize("cmd_query"))


class TestArgparseEnrich(unittest.TestCase):
    def test_parse_maps_handlers(self):
        catalog = parse_argparse_commands(SAMPLE_ARGPARSE)
        self.assertEqual(catalog["cmd_query"]["name"], "query")
        self.assertIn("indexed", catalog["cmd_query"]["help"])
        self.assertEqual(catalog["cmd_eval"]["name"], "eval")
        self.assertEqual(catalog["cmd_serve"]["name"], "serve")

    def test_index_text_appends_help_without_mutating_content(self):
        chunk = _chunk("cmd_query", content="def cmd_query(args):\n    load()\n")
        original = chunk.content
        catalog = parse_argparse_commands(SAMPLE_ARGPARSE)
        text = index_text_for_chunk(chunk, catalog, argparse_enrich=True)
        self.assertEqual(chunk.content, original)
        self.assertIn("CLI command query", text)
        self.assertIn("Query the indexed repository", text)
        self.assertIn("def cmd_query", text)
        plain = index_text_for_chunk(chunk, catalog, argparse_enrich=False)
        self.assertEqual(plain, original)

    def test_bm25_uses_enriched_text(self):
        chunk = _chunk("cmd_query", content="def cmd_query(args):\n    load()\n")
        other = _chunk("cmd_clear", content="def cmd_clear(args):\n    wipe()\n")
        wiring = _chunk(
            "main.py",
            content=SAMPLE_ARGPARSE,
            entity_id="file:main.py",
        )
        wiring.entity_type = EntityType.FILE
        retriever = _retriever(argparse_enrich=True)
        retriever.index_chunks([chunk, other, wiring], persist=False)
        hits = retriever._bm25_search("query CLI command", top_k=5)
        names = [r.chunk.entity_name for r in hits]
        self.assertIn("cmd_query", names)
        self.assertEqual(names[0], "cmd_query")
        self.assertEqual(chunk.content, "def cmd_query(args):\n    load()\n")


class TestSymbolBoost(unittest.TestCase):
    def test_infer_named_cli_command(self):
        names = infer_cli_symbols("Where is the query CLI command defined in main.py?")
        self.assertIn("cmd_query", names)
        self.assertNotIn("cmd_eval", names)

    def test_infer_config_defaults(self):
        names = infer_cli_symbols(
            "Where are the default retrieval weights, top_k, and cross-encoder settings defined?"
        )
        self.assertIn("Config", names)

    def test_unrelated_query_is_empty(self):
        names = infer_cli_symbols(
            "How does a query go from retrieve through cross-encoder rerank to packed context?"
        )
        self.assertEqual(names, set())

    def test_symbol_channel_surfaces_handler(self):
        gold = _chunk("cmd_query")
        noise = _chunk("helper", content="def helper():\n    return query\n")
        retriever = _retriever(symbol_boost=0.25)
        retriever._all_chunks = [gold, noise]
        hits = retriever._cli_symbol_search(
            "Where is the query CLI command defined in main.py?"
        )
        self.assertEqual([r.chunk.entity_name for r in hits], ["cmd_query"])


class TestDocIndex(unittest.TestCase):
    def test_doc_chunks_reuse_gold_entity_ids(self):
        chunks = [
            _chunk("cmd_query"),
            _chunk(
                "Config",
                path="harness/config.py",
                content="@dataclass\nclass Config:\n    pass\n",
                entity_id="class:harness/config.py:Config",
            ),
        ]
        chunks[1].entity_type = EntityType.CLASS
        catalog = parse_argparse_commands(SAMPLE_ARGPARSE)
        docs = make_cli_doc_chunks(chunks, catalog)
        by_name = {d.entity_name: d for d in docs}
        self.assertEqual(by_name["cmd_query"].entity_id, "func:main.py:cmd_query")
        self.assertIn("query cli command", by_name["cmd_query"].content.lower())
        self.assertEqual(by_name["Config"].entity_id, "class:harness/config.py:Config")
        self.assertIn("top_k", by_name["Config"].content)
        self.assertTrue(all((d.metadata or {}).get("kind") == "cli_doc" for d in docs))


class TestCliRetrievalApply(unittest.TestCase):
    def test_apply_flags_and_env(self):
        cfg = Config()
        args = type("A", (), {
            "ident_split": True,
            "argparse_enrich": True,
            "cli_symbol_boost": 0.25,
            "cli_doc_index": True,
        })()
        apply_cli_retrieval_config(cfg, args)
        cli = cfg.retrieval["cli_retrieval"]
        self.assertTrue(cli["ident_split"])
        self.assertTrue(cli["argparse_enrich"])
        self.assertEqual(cli["symbol_boost"], 0.25)
        self.assertTrue(cli["doc_index"])


if __name__ == "__main__":
    unittest.main()
