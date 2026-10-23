"""R4: skip-expansion, unknown-filter rejection, error envelope, list_filters."""

import json
import tempfile
import unittest

from harness.config import Config
from harness.mcp_filters import error_envelope, list_filters, validate_retrieve_args
from harness.models import Chunk, EntityType, RetrievalResult
from harness.serve import RetrieveService, handle_mcp


def _chunk():
    return Chunk(
        id="func:harness/demo.py:run_eval:abcd",
        content="def run_eval():\n    return 1\n",
        entity_id="func:harness/demo.py:run_eval",
        entity_name="run_eval",
        entity_type=EntityType.FUNCTION,
        file_path="harness/demo.py",
        start_line=1,
        end_line=2,
    )


def _config(tmp):
    cfg = Config()
    cfg.repo_path = tmp
    cfg.redaction["enabled"] = False
    return cfg


class RecordingRetriever:
    def __init__(self):
        self.calls = []
        self.skip_expansion = False

    def retrieve(self, query, top_k=None, **kwargs):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "skip": self.skip_expansion,
                **kwargs,
            }
        )
        return [RetrievalResult(chunk=_chunk(), score=0.9, source="test")]


class TestMcpFilters(unittest.TestCase):
    def test_unknown_filter_rejected_with_suggestion(self):
        err, _ = validate_retrieve_args({"query": "q", "source_types": "jira"})
        self.assertIsNotNone(err)
        self.assertEqual(err["results"], [])
        self.assertIn("unknown filter", err["error"])
        self.assertIn("source_types", err["error"])

    def test_invalid_pack_mode_suggests_close_match(self):
        err, _ = validate_retrieve_args({"query": "q", "pack_mode": "lite"})
        self.assertIsNotNone(err)
        self.assertIn("ccr_lite", err["error"])

    def test_skip_expansion_alias_normalizes(self):
        err, norm = validate_retrieve_args({"query": "q", "skip_expansion": True})
        self.assertIsNone(err)
        self.assertTrue(norm["skip_query_expansion"])

    def test_list_filters_names(self):
        payload = list_filters()
        self.assertIn("pack_mode", payload["names"])
        self.assertIn("skip_query_expansion", payload["names"])
        self.assertNotIn("query", payload["names"])

    def test_retrieve_tool_returns_envelope_on_bad_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(
                config=_config(tmp),
                retriever=RecordingRetriever(),
                repo_name="t",
            )
            reply = handle_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "retrieve",
                        "arguments": {"query": "q", "source_type": "jira"},
                    },
                },
                service,
            )
        structured = reply["result"]["structuredContent"]
        self.assertEqual(structured["results"], [])
        self.assertIn("unknown filter", structured["error"])
        self.assertIn("error", json.loads(reply["result"]["content"][0]["text"]))

    def test_list_filters_tool_and_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(config=_config(tmp), repo_name="t")
            listed = handle_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_filters", "arguments": {}},
                },
                service,
            )
            self.assertIn("skip_query_expansion", json.dumps(listed))
            resources = handle_mcp(
                {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
                service,
            )
            uris = [r["uri"] for r in resources["result"]["resources"]]
            self.assertIn("resource://retrieve_filters", uris)

    def test_skip_query_expansion_sets_retriever_flag_during_call(self):
        retriever = RecordingRetriever()
        with tempfile.TemporaryDirectory() as tmp:
            service = RetrieveService(
                config=_config(tmp),
                retriever=retriever,
                repo_name="t",
            )
            handle_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "retrieve",
                        "arguments": {"query": "ContextBuilder", "skip_query_expansion": True},
                    },
                },
                service,
            )
        self.assertTrue(retriever.calls)
        self.assertTrue(retriever.calls[0]["skip"])
        self.assertFalse(retriever.skip_expansion)

    def test_error_envelope_shape(self):
        payload = error_envelope("nope")
        self.assertEqual(set(payload), {"error", "results"})
        self.assertEqual(payload["results"], [])
