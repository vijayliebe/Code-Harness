"""Tests for KG enrichment: new edges, beam expand, Mermaid, gloss."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.knowledge_graph import KnowledgeGraph
from harness.models import (
    CodeEntity,
    EntityType,
    RelationshipType,
    Chunk,
    RetrievalResult,
)


def _entity(eid, name, etype, path, source="", metadata=None, start=1, end=10):
    return CodeEntity(
        id=eid,
        name=name,
        type=etype,
        file_path=path,
        start_line=start,
        end_line=end,
        source_code=source,
        metadata=metadata or {},
    )


def _file(path, source):
    return _entity(f"file:{path}", os.path.basename(path), EntityType.FILE, path, source)


def _func(path, name, source="", etype=EntityType.FUNCTION):
    prefix = "func" if etype == EntityType.FUNCTION else "method"
    return _entity(f"{prefix}:{path}:{name}", name, etype, path, source)


def _class(path, name, source="", methods=None):
    return _entity(
        f"class:{path}:{name}",
        name,
        EntityType.CLASS,
        path,
        source,
        metadata={"methods": methods or []},
    )


def _kg(entities, enrich=True, **kg_cfg):
    cfg = Config()
    cfg.knowledge_graph["enrich"] = enrich
    cfg.knowledge_graph.update(kg_cfg)
    kg = KnowledgeGraph(cfg, repo_name="fixture")
    kg.build(entities)
    return kg


def _rels(kg, rel_type):
    found = []
    for src, tgt, data in kg.graph.edges(data=True):
        if data.get("relationship") == rel_type:
            found.append((src, tgt, data))
    return found


class TestRelationshipTypes(unittest.TestCase):
    def test_new_types_are_additive(self):
        self.assertEqual(RelationshipType.EXPOSES.value, "exposes")
        self.assertEqual(RelationshipType.TESTED_BY.value, "tested_by")
        self.assertEqual(RelationshipType.GLOSS.value, "gloss")
        self.assertEqual(RelationshipType.CONTAINS.value, "contains")
        self.assertEqual(RelationshipType.CALLS.value, "calls")


class TestExposesExtractor(unittest.TestCase):
    def test_argparse_add_parser_exposes_cli_commands(self):
        source = '''
import argparse
def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers()
    sub.add_parser("index", help="Index a repo")
    sub.add_parser("query", help="Query a repo")
    sub.add_parser("info")
'''
        entities = [
            _file("main.py", source),
            _func("main.py", "cmd_index", "def cmd_index(args): pass"),
            _func("main.py", "cmd_query", "def cmd_query(args): pass"),
            _func("main.py", "main", source),
        ]
        kg = _kg(entities)
        exposed = {(s, t) for s, t, _ in _rels(kg, "exposes")}
        self.assertTrue(
            any(t.endswith(":index") or t.endswith(":query") for _, t in exposed),
            f"missing CLI endpoints in {exposed}",
        )
        self.assertTrue(
            any(s.endswith("file:main.py") or s.endswith("main.py") for s, _ in exposed)
            or any("main.py" in s for s, _ in exposed),
        )

    def test_fastapi_decorator_exposes_http_route(self):
        source = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/v1/retrieve")
def retrieve():
    return {}
'''
        entities = [
            _file("api.py", source),
            _func("api.py", "health", source),
            _func("api.py", "retrieve", '@app.post("/v1/retrieve")\ndef retrieve():\n    return {}\n'),
        ]
        kg = _kg(entities)
        targets = [t for _, t, _ in _rels(kg, "exposes")]
        self.assertTrue(any("health" in t or "/health" in t for t in targets), targets)
        self.assertTrue(any("retrieve" in t or "/v1/retrieve" in t for t in targets), targets)

    def test_extractor_failure_does_not_block_core_graph(self):
        entities = [
            _file("ok.py", "class Foo:\n    pass\n"),
            _class("ok.py", "Foo", "class Foo:\n    pass\n"),
        ]
        cfg = Config()
        cfg.knowledge_graph["enrich"] = True
        kg = KnowledgeGraph(cfg, repo_name="fixture")
        original = kg._extract_exposes
        def boom(*args, **kwargs):
            raise RuntimeError("extractor exploded")
        kg._extract_exposes = boom
        rels = kg.build(entities)
        kg._extract_exposes = original
        self.assertTrue(any(r.relationship_type == RelationshipType.CONTAINS for r in rels))
        self.assertIn("file:ok.py", kg.graph)
        self.assertIn("class:ok.py:Foo", kg.graph)


class TestTestedByExtractor(unittest.TestCase):
    def test_test_file_links_to_imported_class(self):
        code = _class(
            "harness/chunker.py",
            "CodeChunker",
            "class CodeChunker:\n    def chunk_entities(self): pass\n",
            methods=["chunk_entities"],
        )
        method = _func(
            "harness/chunker.py",
            "CodeChunker.chunk_entities",
            "def chunk_entities(self): pass\n",
            etype=EntityType.METHOD,
        )
        test_src = (
            "from harness.chunker import CodeChunker\n"
            "def test_chunk_entities():\n"
            "    CodeChunker().chunk_entities()\n"
        )
        test_file = _file("tests/test_chunker.py", test_src)
        test_fn = _func("tests/test_chunker.py", "test_chunk_entities", test_src)
        kg = _kg([code, method, test_file, test_fn, _file("harness/chunker.py", "class CodeChunker: pass")])
        edges = _rels(kg, "tested_by")
        self.assertTrue(edges, "expected tested_by edges")
        self.assertTrue(
            any("test_chunker" in s and "CodeChunker" in t for s, t, _ in edges),
            edges,
        )

    def test_common_names_are_not_linked(self):
        code = _func("lib.py", "get", "def get(): pass")
        test_src = "from lib import get\ndef test_get():\n    get()\n"
        kg = _kg([
            _file("lib.py", "def get(): pass"),
            code,
            _file("tests/test_lib.py", test_src),
            _func("tests/test_lib.py", "test_get", test_src),
        ])
        edges = _rels(kg, "tested_by")
        self.assertFalse(any(t.endswith(":get") or t.endswith("lib.py:get") for _, t, _ in edges), edges)


class TestGlossLinking(unittest.TestCase):
    def test_gloss_markdown_links_to_entity(self):
        with tempfile.TemporaryDirectory() as tmp:
            gloss_dir = os.path.join(tmp, ".code-harness", "gloss")
            os.makedirs(gloss_dir)
            with open(os.path.join(gloss_dir, "chunker.md"), "w") as f:
                f.write(
                    "---\n"
                    "entity: class:harness/chunker.py:CodeChunker\n"
                    "---\n"
                    "# CodeChunker notes\n"
                    "Splits entities into retrieval chunks.\n"
                )
            entities = [
                _file("harness/chunker.py", "class CodeChunker: pass"),
                _class("harness/chunker.py", "CodeChunker", "class CodeChunker: pass"),
            ]
            cfg = Config()
            cfg.repo_path = tmp
            cfg.knowledge_graph["enrich"] = True
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.build(entities)
            gloss_edges = _rels(kg, "gloss")
            self.assertTrue(gloss_edges, "expected gloss edges")
            self.assertTrue(
                any(t == "class:harness/chunker.py:CodeChunker" for _, t, _ in gloss_edges),
                gloss_edges,
            )
            gloss_nodes = [
                n for n, data in kg.graph.nodes(data=True)
                if data.get("kind") == "gloss" or data.get("type") == "documentation"
            ]
            self.assertTrue(gloss_nodes)

    def test_missing_target_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            gloss_dir = os.path.join(tmp, "knowledge", "gloss")
            os.makedirs(gloss_dir)
            with open(os.path.join(gloss_dir, "orphan.md"), "w") as f:
                f.write("---\nentity: class:missing.py:Nope\n---\n# orphan\n")
            cfg = Config()
            cfg.repo_path = tmp
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.build([_file("a.py", "x=1")])
            self.assertIn("file:a.py", kg.graph)


class TestBeamExpansion(unittest.TestCase):
    def _graph_for_beam(self):
        entities = [
            _file("mod.py", "class Seed:\n    def run(self): Other.x()\n"),
            _class("mod.py", "Seed", methods=["run"]),
            _func("mod.py", "Seed.run", "def run(self): Other.x()\n", etype=EntityType.METHOD),
            _class("mod.py", "Other"),
            _func("mod.py", "unrelated", "def unrelated(): pass"),
        ]
        kg = _kg(entities, enrich=False)
        kg.graph.add_edge(
            "class:mod.py:Seed",
            "endpoint:mod.py:/v1",
            relationship="exposes",
        )
        kg.graph.add_node(
            "endpoint:mod.py:/v1",
            name="/v1",
            type="endpoint",
            file_path="mod.py",
        )
        kg.graph.add_edge(
            "method:mod.py:Seed.run",
            "class:mod.py:Other",
            relationship="calls",
        )
        return kg

    def test_beam_prefers_exposes_and_calls_over_contains(self):
        kg = self._graph_for_beam()
        added = kg.expand_beam(
            {"class:mod.py:Seed": 1.0},
            width=2,
            depth=1,
            max_added=2,
        )
        self.assertIn("endpoint:mod.py:/v1", added)
        self.assertNotIn("func:mod.py:unrelated", added)

    def test_beam_respects_width_and_cap(self):
        kg = self._graph_for_beam()
        added = kg.expand_beam(
            {"file:mod.py": 1.0},
            width=1,
            depth=1,
            max_added=1,
        )
        self.assertLessEqual(len(added), 1)

    def test_bfs_mode_still_available(self):
        kg = self._graph_for_beam()
        bfs = kg.get_related_chunks(["class:mod.py:Seed"], max_depth=1)
        self.assertTrue(bfs)
        beam = kg.expand_beam({"class:mod.py:Seed": 0.5}, width=6, depth=1, max_added=6)
        self.assertTrue(beam)

    def test_default_expand_mode_is_beam(self):
        self.assertEqual(DEFAULT_CONFIG["retrieval"].get("expand_mode"), "beam")
        self.assertEqual(DEFAULT_CONFIG["retrieval"].get("beam_width"), 6)
        self.assertEqual(DEFAULT_CONFIG["retrieval"].get("beam_depth"), 2)
        self.assertEqual(DEFAULT_CONFIG["retrieval"].get("expand_neighbors"), 3)


class TestMermaidExport(unittest.TestCase):
    def test_mermaid_contains_graph_and_edge_labels(self):
        entities = [
            _file("mod.py", "class Seed: pass"),
            _class("mod.py", "Seed", methods=["run"]),
            _func("mod.py", "Seed.run", "def run(self): pass", etype=EntityType.METHOD),
        ]
        kg = _kg(entities, enrich=False)
        text = kg.to_mermaid(focus="class:mod.py:Seed", max_nodes=20)
        self.assertIn("graph TD", text)
        self.assertIn("Seed", text)
        self.assertTrue("contains" in text or "-->" in text)

    def test_mermaid_focus_limits_subgraph(self):
        entities = [
            _file("a.py", "class A: pass"),
            _class("a.py", "A"),
            _file("b.py", "class B: pass"),
            _class("b.py", "B"),
        ]
        kg = _kg(entities, enrich=False)
        text = kg.to_mermaid(focus="class:a.py:A", max_nodes=8, depth=1)
        self.assertIn("A", text)
        self.assertNotIn("class:b.py:B", text)


class TestVisualizerCompat(unittest.TestCase):
    def test_graph_json_loads_with_new_relationship_types(self):
        entities = [
            _file("main.py", 'p.add_parser("query")\n'),
            _func("main.py", "cmd_query", "def cmd_query(): pass"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.knowledge_graph["persist_path"] = os.path.join(tmp, "graph.json")
            cfg.knowledge_graph["enrich"] = True
            kg = KnowledgeGraph(cfg, repo_name="demo")
            kg.build(entities)
            kg.save()
            path = kg.persist_path
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                raw = json.load(f)
            nodes = raw.get("nodes") or []
            links = raw.get("links") or raw.get("edges") or []
            self.assertTrue(nodes)
            # Visualizer walks relationship as a free string.
            rels = [link.get("relationship", "related") for link in links]
            self.assertTrue(rels)
            loaded = KnowledgeGraph(cfg, repo_name="demo")
            loaded.load()
            self.assertGreater(loaded.graph.number_of_nodes(), 0)
            # Unknown relationship values must not crash a visualizer-style walk.
            for link in links:
                label = link.get("relationship") or "related"
                self.assertIsInstance(label, str)


class TestRetrieverBeamWiring(unittest.TestCase):
    def test_graph_search_uses_beam_when_configured(self):
        from harness.retriever import Retriever

        cfg = Config()
        cfg.retrieval["expand_mode"] = "beam"
        cfg.retrieval["beam_width"] = 2
        cfg.retrieval["beam_depth"] = 1
        cfg.retrieval["expand_neighbors"] = 3
        kg = KnowledgeGraph(cfg, repo_name="fixture")
        kg.graph.add_node("class:mod.py:Seed", name="Seed", type="class", file_path="mod.py")
        kg.graph.add_node("endpoint:mod.py:index", name="index", type="endpoint", file_path="mod.py")
        kg.graph.add_edge("class:mod.py:Seed", "endpoint:mod.py:index", relationship="exposes")
        called = {}

        def fake_beam(seeds, width=6, depth=2, max_added=6):
            called["seeds"] = dict(seeds)
            called["width"] = width
            called["depth"] = depth
            called["max_added"] = max_added
            return {"endpoint:mod.py:index"}

        kg.expand_beam = fake_beam
        retriever = Retriever(cfg, embedder=None, vector_store=None, knowledge_graph=kg)
        endpoint_chunk = Chunk(
            id="endpoint:mod.py:index:1",
            content="CLI index",
            entity_id="endpoint:mod.py:index",
            entity_name="index",
            entity_type=EntityType.FUNCTION,
            file_path="mod.py",
            start_line=1,
            end_line=2,
        )
        seed_chunk = Chunk(
            id="class:mod.py:Seed:1",
            content="class Seed",
            entity_id="class:mod.py:Seed",
            entity_name="Seed",
            entity_type=EntityType.CLASS,
            file_path="mod.py",
            start_line=1,
            end_line=4,
        )
        retriever._all_chunks = [seed_chunk, endpoint_chunk]
        existing = [RetrievalResult(chunk=seed_chunk, score=0.9, source="dense")]
        results = retriever._graph_search("what exposes index", existing)
        self.assertIn("seeds", called)
        self.assertEqual(called["width"], 2)
        self.assertTrue(any(r.chunk.entity_id == "endpoint:mod.py:index" for r in results))

    def test_bfs_expand_mode_uses_related_chunks(self):
        from harness.retriever import Retriever

        cfg = Config()
        cfg.retrieval["expand_mode"] = "bfs"
        kg = KnowledgeGraph(cfg, repo_name="fixture")
        called = {}

        def fake_related(entity_ids, max_depth=2):
            called["ids"] = list(entity_ids)
            called["max_depth"] = max_depth
            return set()

        kg.get_related_chunks = fake_related
        retriever = Retriever(cfg, embedder=None, vector_store=None, knowledge_graph=kg)
        seed = Chunk(
            id="class:mod.py:Seed:1",
            content="class Seed",
            entity_id="class:mod.py:Seed",
            entity_name="Seed",
            entity_type=EntityType.CLASS,
            file_path="mod.py",
            start_line=1,
            end_line=4,
        )
        retriever._all_chunks = [seed]
        retriever._graph_search("Seed", [RetrievalResult(chunk=seed, score=0.4, source="dense")])
        self.assertIn("ids", called)


class TestInfoMermaidCli(unittest.TestCase):
    def test_info_help_exposes_mermaid_flags(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
        run = subprocess.run(
            [sys.executable, "main.py", "info", "--help"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--mermaid", run.stdout)
        self.assertIn("--focus", run.stdout)


if __name__ == "__main__":
    unittest.main()
