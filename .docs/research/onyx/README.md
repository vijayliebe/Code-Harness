# Onyx: research notes and steal matrix

> Source: [onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx), `main` as read on 2026-10-23. Code was read remotely (raw files and the GitHub tree API). Nothing was cloned. Paths below are relative to that repo.
> Licence: content outside `ee/` directories is MIT ("Copyright (c) 2023-present DanswerAI, Inc."). `backend/ee/**` is under the Onyx Enterprise License. **Do not adapt code from `ee/`**, which is where permission sync lives. If any MIT code is adapted, keep the copyright notice in the file header and add the source to `.docs/research/INDEX.md`.
> Code-Harness status is judged from `README.md` and a few `harness/*.py` files on `main`. A "no" means I could not find it there. It does not mean it has been proven absent.

## TL;DR

Onyx is an enterprise knowledge platform, not a code retriever. Its hybrid index is ordinary: OpenSearch with 0.5/0.5 min-max hybrid scoring. The parts worth borrowing are the **harness around retrieval**:

- weighted multi-query RRF
- an LLM selection step followed by a 4-way "how much more of this doc do I need" expansion
- a per-document cap so no single document floods the selection budget
- index-time title and metadata enrichment of chunk text
- hard cycle caps that force an answer on the last cycle

For the new "external context" goal (Jira, Confluence, Slack, Figma), the **connector contract** is the most reusable piece: load, poll, checkpoint, slim, and failures as data. Onyx does **not** link tickets to code, and it has **no Figma connector**. That linking is the gap Code-Harness can own.

---

## 1. Retrieval harness: what Onyx actually does

### 1.1 Search tool flow (`backend/onyx/tools/tool_implementations/search/search_tool.py`)
The module docstring describes 5 steps, and the code matches it:
1. **Queries.** The chat LLM's own tool-call queries get weight 0.7. A dedicated **semantic rephrase** (`secondary_llm_flows/query_expansion.py::semantic_query_rephrase`) gets weight 1.3. Up to **3 keyword-only queries** (`keyword_query_expansion`, with the prompt in `prompts/search_prompts.py::KEYWORD_REPHRASE_*`, which says "Do not replace or expand niche, proprietary, or obscure terms") get weight 1.0. The original user query gets 0.5. The weights live in `search/constants.py`. Duplicate queries are case-folded and their **weights summed** (`deduplicate_queries`).
2. **Parallel retrieval and weighted RRF.** Each query runs as its own hybrid search. Keyword queries run with `hybrid_alpha=0.2`, which leans towards BM25. The ranked lists are fused with `weighted_reciprocal_rank_fusion` (`search_utils.py`, k=50). Tie-breaks go to rank-in-source, then source index. Adjacent chunks are then merged into sections (`context/search/pipeline.py::merge_individual_chunks`).
3. **Selection.** Sections are trimmed to a token budget of `MAX_CHUNKS_FED_TO_CHAT(25) × DOC_EMBEDDING_CONTEXT_SIZE × 2`. Only **≤3 chunks per section** count towards that budget (`MAX_CHUNKS_FOR_RELEVANCE`), "to avoid documents with good titles … flood[ing] out the rest". An LLM then picks up to 10 sections (`secondary_llm_flows/document_filter.py::select_sections_for_expansion`). The prompt is deliberately lenient ("if at all in doubt, include").
4. **Expansion.** For each selected section, run in parallel, a small LLM call sees the section plus 2 chunks above and below. It classifies the section as `NOT_RELEVANT` (drop), `MAIN_SECTION_ONLY`, `INCLUDE_ADJACENT_SECTIONS`, or `FULL_DOCUMENT` (±5 chunks) (`search_utils.py::expand_section_with_context`, `prompts/search_prompts.py::DOCUMENT_CONTEXT_SELECTION_PROMPT`, `context/search/models.py::ContextExpansionType`). Documents the selector flagged as "best" skip the classification and get full expansion. On any error the code falls back to the original section.
5. **Packing.** `merge_overlapping_sections` deduplicates overlapping spans. The output is a numbered JSON-ish string plus a `citation_mapping`.

Cost controls I saw:
- A cheaper **selection LLM** can be injected (`selection_llm_factory`).
- A **repeat call in the same turn reuses the cached expansion** (`_cached_expansion`; `llm_loop.py` passes `skip_search_query_expansion=has_called_search_tool`).
- Secondary flows run with reasoning off and a 60 s timeout (`configs/chat_configs.py::SECONDARY_LLM_FLOW_TIMEOUT_S`).
- `secondary_llm_flows/search_prompt_budget.py` binary-searches the longest text prefix that fits the model's input budget.

### 1.2 When to search more, and when to stop (`backend/onyx/chat/llm_loop.py`)
- Search is a tool. The model decides whether to call it again.
- **Stop conditions:**
  - the model returns no tool calls
  - a "stopping tool" is called
  - the run hits `MAX_LLM_CYCLES` (default 6, `configs/chat_configs.py`). On the last cycle, `tool_choice=NONE` and tools are removed, so the model must answer.
- No retrieval-quality grader decides to "search more". It is purely the agent's judgement plus the cap.
- For multi-source tenants there is also an LLM **scope decision** (`secondary_llm_flows/source_filter.py::decide_search_scope`). It sees the previous cycles' queries and sources so it can back off to the next source, and it **fails open** to "search everything". `time_filter.py` extracts date windows from the query in the same way.

### 1.3 Hybrid index (`backend/onyx/document_index/opensearch/`)
- **The engine is OpenSearch now.** The Vespa document-index code is gone from `backend/onyx/document_index/`. Only Celery task names like `background/celery/tasks/vespa/` remain. Older blog posts about Vespa are out of date.
- Hybrid search is one kNN subquery plus one keyword subquery, combined by an OpenSearch **min-max normalisation pipeline with weights 0.5/0.5** (`search.py::_get_hybrid_search_normalization_weights`). Z-score is optional (`constants.py`), and the comment says it was "not very different" on a ≤10K-doc set.
- The keyword subquery (`_get_title_content_combined_keyword_search_query`) uses these boosts:
  - title `match` 0.1
  - title phrase (slop 1) 0.2
  - content `match` 1.0
  - content phrase (slop 1) 1.5

  It sets `minimum_should_match: 1`. They tried fuzziness `AUTO` and rejected it because it "makes recall slightly worse".
- Candidates: 500 per subquery (`DEFAULT_NUM_HYBRID_SUBQUERY_CANDIDATES`). It was bumped from 100 because of poor recall.
- **Recency and boosts:** `DocumentQuery` has `TODO: Implement document boost` and `TODO: Implement document age`. `DOC_TIME_DECAY` (`chat_configs.py`) is only referenced by the federated Slack search (`context/search/federated/slack_search.py`). So time decay is **not applied** in the main index path, as far as I can see.
- **Rerank:** `natural_language_processing/search_nlp_models.py` still has `RerankingModel` (Cohere, Bedrock, LiteLLM, local), but `warm_up_cross_encoder` is marked "No longer used". I found no cross-encoder call in the search tool path. **The LLM selection step has replaced the reranker.** I did not trace every caller, so treat this as likely rather than proven.

### 1.4 Chunking (`backend/onyx/indexing/chunker.py`, `document_index/chunk_content_enrichment.py`)
- Sentence-aware chunks of 512 tokens (`opensearch/constants.py::DEFAULT_MAX_CHUNK_SIZE`), with **zero overlap** ("unclear if overlaps actually help quality at all").
- **Enrichment:** the indexed text is `title_prefix + doc_summary + content + chunk_context + metadata_suffix`. There are separate **semantic** (for the embedding) and **keyword** (for BM25) metadata suffixes. Metadata is capped at 25% of the chunk (`MAX_METADATA_PERCENTAGE`). The enrichment is stripped again before the text is shown (`cleanup_content_for_chunks`).
- **Contextual RAG** (`ENABLE_CONTEXTUAL_RAG`, off by default; prompts in `prompts/contextual_retrieval.py`) adds an LLM doc summary and a per-chunk "situate this chunk" context. It is skipped when the whole doc fits in one chunk.
- **Multipass:** mini-chunks of 150 tokens and large chunks of 4× size (`ENABLE_MULTIPASS_INDEXING`, `generate_large_chunks`). These are off by default, and I found **no reference to large or mini chunks in the OpenSearch index code**. They look vestigial (unverified).

### 1.5 Deep Research (`backend/onyx/deep_research/dr_loop.py`, `tools/fake_tools/research_agent.py`, `prompts/deep_research/*`)
- Steps: optional clarification, then a plan (one LLM answer), then an orchestrator loop, then the final report.
- Orchestrator: **≤8 cycles** (4 for reasoning models, which skip the `think` tool). A `think` tool is required between research calls. Prompts cap it at **≤3 parallel `research_agent` calls**.
- Each research agent runs ≤8 cycles (`MAX_RESEARCH_CYCLES`) and is forced to write an intermediate report of ≤10k tokens on its last cycle or after **12 min**.
- The final report is capped at **20k tokens**. A **30 min** wall clock forces the report.
- It refuses models with **<50k** context. The orchestrator's per-step output is capped at 1024 tokens "to prevent endless loops of null tokens".
- Budget control is therefore **cycle caps, wall-clock caps and output caps**, not a running token ledger.

### 1.6 MCP server (`backend/onyx/mcp_server/`)
- **Tools** (`tools/search.py`):
  - `search_indexed_documents(query, source_types?, document_set_names?, time_cutoff?, skip_query_expansion?, agent?)`
  - `search_web(query, limit=5)`
  - `open_urls(urls)`, capped at 20 per call
- **Resources:** `resource://indexed_sources`, document sets and agents (`resources/*.py`), so a client can discover valid filters.
- **Response shape:** `{"results":[{title,url,source_type,content,updated_at}]}`. Errors use the **same envelope** `{"error": "...", "results": []}`.
- An unknown filter value is **rejected with close matches**, "so the scope is never silently wider". The list of alternatives is only included while it is short (`_MAX_NAMES_IN_ERROR=10`), to save tokens.
- The tool calls the full backend pipeline (expansion, hybrid retrieval, LLM selection, expansion). I found no separate context or token cap in the MCP layer; it relies on the pipeline's selection budget.

### 1.7 Permissions (`backend/onyx/access/*`, `context/search/preprocessing/access_filters.py`, `opensearch/search.py::_get_acl_visibility_filter`)
- Every chunk carries `access_control_list` entries such as `user_email:…`, `group:…` and `external_group:<source>_<name>` (`access/utils.py`), plus a `public` flag.
- Query time adds a filter: `public OR acl ∈ user_acl`.
- Sync code is in `ee/` (`backend/ee/onyx/external_permissions/{jira,confluence,github,…}`), so it is **not MIT**.
- **Fit for a single repo:** low. The one useful bit for external sources is a coarse per-doc `visibility` (`public` / `private:<channel>`), so private Slack or Confluence content is never packed into a shared or exported artefact.

### 1.8 Citations and grounding (`backend/onyx/chat/citation_processor.py`, `citation_utils.py`)
- A streaming parser handles `[1]`, `[1, 2]`, `[[1]]`, `【1】` and similar, and skips code fences (`CodeFenceTracker`).
- Unknown numbers are **logged and dropped**.
- Citations are renumbered across deep-research sub-reports (`collapse_citations`).
- **No claim-level grounding or verification.** Code-Harness's verify gate and validity branch already go further.

### 1.9 Eval tooling
- `backend/tests/regression/search_quality/run_search_eval.py`: ground truth is a list of doc links per query, plus optional `categories`. It reports the rank of the ground truth, **top-k accuracy**, best/worst/average rank, and **per-category CSV**. Answers are scored with **RAGAS faithfulness and response relevancy**, which needs an OpenAI key.
- `backend/onyx/evals/*`: chat evals with **tool assertions** (expected tools called, any or all), timings, and Braintrust or local providers.
- There is no nDCG and no failure taxonomy. Code-Harness's retrieval eval is already stronger.

---

## 2. Steal matrix: retrieval and harness

Impact: A = accuracy, L = latency, T = tokens. Effort: S < 1 day, M = a few days, L = a week or more.

| # | Idea (Onyx path) | CH has it? | Expected impact | Effort | How to test in CH eval |
|---|---|---|---|---|---|
| R1 | **Index-time chunk enrichment**: path, symbol and signature prefix plus a short metadata suffix in the *embedded/BM25 text*, kept separate from the displayed text (`indexing/chunker.py`, `document_index/chunk_content_enrichment.py`) | **No** as far as I can tell: `main.py` embeds `c.content` only | A↑ (dense_miss on name-poor bodies). L/T unchanged at query time. Re-index needed | S | Recall@k and nDCG on the fixture; dense_miss count before/after; MiniLM 256-token limit check (cap the prefix at ~25% as Onyx does) |
| R2 | **Weighted multi-query RRF with weight-summing dedupe** (`search_tool.py`, `search/constants.py`, `search_utils.py::weighted_reciprocal_rank_fusion`). Free CH variant: original query, plus an identifier/keyword query from `loop.extract_focus_terms`, plus the HyDE template, each its own list with its own weight | Partial: single query; rewrite/HyDE only on loop retries | A↑ on vague queries; L↑ (more dense/BM25 calls, parallelisable); T neutral | M | Ablation eval: on/off; Recall@k; per-stage latency; check identifier queries do not regress |
| R3 | **Per-document cap before the budget cut** (`MAX_CHUNKS_FOR_RELEVANCE=3`, `_trim_sections_by_tokens`) | Partial: MMR diversifies, but there is no hard per-file cap | A↑ (distinct files), T↓ | S | Recall@k, distinct-files@k, packer_drop; sweep cap ∈ {2,3,∞} |
| R4 | **4-way expansion classification** (drop / main / adjacent / full) instead of a fixed expansion (`search_utils.py::expand_section_with_context`, `DOCUMENT_CONTEXT_SELECTION_PROMPT`). CH form: heuristic first (CE score and graph adjacency); decision model only when `decision.enabled` | Partial: CCR expand, graph deepen, `--expand-chunk` | T↓ (drop the irrelevant), A↑ on "why" questions; L↑ if an LLM is used | M | prompt_tokens_packed, citation-path hit, verify pass-rate; default off until eval wins |
| R5 | **LLM selection as reranker** (`select_sections_for_expansion`) | CH uses a cross-encoder (cheaper and local) | A? (unknown for code); L↑↑; not local-first | M | Only as an ablation arm against CE with `decision.enabled`. Do not default |
| R6 | **Hard cycle cap + forced answer** (`llm_loop.py`: last cycle `tool_choice=NONE`) and **reuse cached expansion on repeat search** | Yes/partial: `max_loops`, stall early-stop on branch | L/T bounded | S | Already covered by the loop-stall eval; add "forced-final" counter |
| R7 | **MCP ergonomics** (`mcp_server/tools/search.py`): `skip_query_expansion`; filter values validated with close-match errors; a constant `{error, results:[]}` envelope; resources for discoverable filters; `updated_at` in results | Partial: CH has 7 tools; I did not check its error shape | A↑ for agents (fewer silent wrong scopes), T↓ on errors | S | MCP contract tests; golden JSON shapes |
| R8 | **Keyword boosts**: phrase match above term match, title as a minor boost (`opensearch/search.py`) | Partial: BM25 has no phrase or path boost | A↑ on exact-identifier queries | S | bm25_miss count; identifier subset |
| R9 | **Category breakdown + tool assertions in eval** (`tests/regression/search_quality/`, `evals/eval.py::evaluate_tool_assertions`) | Partial: easy/hard splits only | Better failure analysis | S | Add `category` to fixture YAML; per-category Recall table |
| R10 | Contextual RAG LLM summaries per chunk (`prompts/contextual_retrieval.py`) | No | A? (+), but indexing cost is an LLM call per chunk | L | Only behind a flag with a local small model; nDCG A/B |
| R11 | Deep Research orchestrator (≤8 cycles, ≤3 parallel sub-agents, report caps, wall clock) | No | Answer depth; T↑↑ | L | Not a retrieval problem. Defer |
| R12 | Time decay `1/(1+0.5·age_years)` (`chat_configs.py::DOC_TIME_DECAY`) | No | Code: likely harmful (old code ≠ stale code). External tickets: maybe | S | Only for the external layer (see X-section) |

### Top 5 retrieval ideas to adopt (in order)
1. **R1 enrichment.** Cheapest likely accuracy win, with zero query-time cost.
2. **R3 per-file cap.** Small change; protects the token budget.
3. **R2 multi-query weighted RRF** (no-LLM variant first).
4. **R7 MCP ergonomics.** Cheap, and directly improves how coding agents use CH.
5. **R4 expansion classification**, heuristic first and LLM only behind `decision.enabled`.

### Skip, with reasons
- **OpenSearch/Vespa, min-max hybrid normalisation.** A server dependency that breaks local-first. RRF is already the right tool for heterogeneous lists, and Onyx itself uses RRF across queries.
- **LLM-as-reranker as a default (R5).** Adds latency and cost and needs an API key. The cross-encoder is local. Keep it as an ablation arm only.
- **Multipass mini/large chunks.** Appear unused in Onyx's current index path. CH chunks by AST unit already.
- **ACL machinery.** The core is single-user and single-repo, and sync code is `ee/`. Keep only a coarse `visibility` field for external docs.
- **Fuzzy matching.** Onyx measured it as worse for recall.
- **Deep Research (for now).** Out of scope for retrieval quality and expensive. Revisit after the external-context layer.
- **Contextual RAG by default.** An LLM call per chunk at index time conflicts with local-first and fast re-index.

---

## 3. External context for code tasks (Jira, Confluence, Slack, Figma)

### 3a. Onyx connector framework: what to copy
**Interfaces** (`backend/onyx/connectors/interfaces.py`):
- `LoadConnector.load_from_state()`: full load, yields batches.
- `PollConnector.poll_source(start, end)`: time-window incremental sync.
- `CheckpointedConnector.load_from_checkpoint(start, end, checkpoint) -> Generator[Document | HierarchyNode | ConnectorFailure, None, Checkpoint]`: yields docs **or failures as data**, and **returns the new checkpoint** as the generator's return value. Checkpoints are pydantic models with `has_more` (`models.py::ConnectorCheckpoint`) and round-trip via `validate_checkpoint_json`.
- `SlimConnector.retrieve_all_slim_docs()`: IDs only (plus access), used for **pruning deleted docs** cheaply.
- `SlimConnectorWithPermSync` and `CheckpointedConnectorWithPermSync`: same, plus permission data.
- `Resolver.reindex(errors)`: re-fetch exactly the docs that failed last time.
- `OAuthConnector`, `CredentialsConnector` (a credentials-provider interface for rotating tokens), `HierarchyConnector` (spaces, folders, projects).
- `BaseConnector.validate_connector_settings()`, `normalize_url()`.

**Document model** (`backend/onyx/connectors/models.py`):
- `Document{id, sections[TextSection|ImageSection|TabularSection(link,text,heading)], source, semantic_identifier, title, metadata: dict[str, str|list[str]], doc_updated_at, doc_created_at, primary_owners, secondary_owners, external_access, doc_metadata (hierarchy), parent_hierarchy_raw_node_id}`
- Plus `SlimDocument{id, external_access}` and `ConnectorFailure{failed_document|failed_entity(missed_time_range), failure_message}`.
- Note: a **section carries its own link**, so a citation can point to a heading or anchor and not just the doc.

**Per-connector notes:**
- **Jira** (`connectors/jira/connector.py`):
  - Checkpointed + slim + perm + resolver.
  - JQL `updated >= start AND updated <= end`, optionally AND-ed with a user JQL (`build_jql_query`, `scope_jql`). Page size 50. v3 cursor or v2 offset in the checkpoint.
  - One doc per issue. `semantic_identifier = "KEY: summary"`. Metadata covers key, status, priority, labels, assignee, reporter, issuetype, **parent key**, project, and created/updated/resolution dates. Comments are inlined, with an email blacklist.
  - Auth is email + API token, or a PAT (`JIRA_USER_EMAIL`, `JIRA_API_TOKEN`).
  - 401/403/429 fail the run (`_RUN_FAILING_STATUS_CODES`).
- **Confluence** (`connectors/confluence/connector.py`):
  - CQL with `lastmodified` window (the user's CQL may not include its own `lastModified`; `config.py`).
  - `body.storage.value`, comments, attachments (filetype-validated), ancestors for hierarchy.
  - Checkpoint = `next_page_url`. Cloud or server.
- **Slack** (`connectors/slack/connector.py`):
  - **One doc per thread** (`thread_to_doc`, `build_slack_doc_id(channel_id, thread_ts)`).
  - Checkpoint keeps the per-channel completion timestamp, walking backwards in time, plus seen thread timestamps.
  - Channel access is captured per channel.
  - There is also a *federated* live Slack search at query time (`context/search/federated/slack_search.py`), which is the only place time decay is used.
- **GitHub** (`connectors/github/connector.py`):
  - Staged checkpoint (PRS → ISSUES → FILES) with page and cursor fallback.
  - **The PR doc is the PR body only**, plus metadata (state, merged, labels, counts, dates). No diff, no changed-file list, no commits, no linked issues.
  - Rate limit: on `GitHubRateLimitError`, sleep until the reset +1 min (`rate_limit_utils.py`), retrying a bounded number of times.
- **GitLab** (`connectors/gitlab/connector.py`): older `LoadConnector + PollConnector` style.
- **Figma: no connector exists.** The only match in the tree is a logo (`web/lib/opal/src/logos/figma.tsx`). A Linear connector does exist.
- **Generic rate limiter:** `cross_connector_utils/rate_limit_wrapper.py` is a thread-safe `max_calls/period` decorator with exponential sleep backoff. `server_wait.py` bounds server-requested waits.

### 3b. Mixed-source indexing and ranking in Onyx
- All sources share **one index**. Source type, tags, document sets and time window are **filters**, not ranking signals (`opensearch/search.py::_get_search_filters`: `_get_source_type_filter`, `_get_document_time_filter`, etc.).
- Scoping comes from three places: the user's selection, an agent's (persona's) document sets, or the LLM's `decide_search_scope` (fails open). `time_filter.py` handles dates. The search tool appends a **scope note** telling the agent which sources were covered and which queries ran, so it can re-search with different terms.
- **No per-source boosts and no recency decay** in the main index. There are TODOs for both. Cross-source balance comes from per-query RRF and the LLM selection step.
- **Takeaway:** Onyx shows that filters plus LLM selection is workable, but it gives no evidence on source weighting. CH has to measure its own.

### 3c. Entity linking: what Onyx does and does not do
- **Does:**
  - Jira `parent` key in metadata.
  - Jira issue-key regex `[A-Z][A-Z0-9_]*-[0-9]+` (`jira/connector.py::_ISSUE_KEY_RE`), but it is only used to validate keys parsed from IDs.
  - Slack threads kept intact.
  - Confluence ancestors become hierarchy nodes.
  - A KG module defines entity types `JIRA`, `GITHUB_PR`, `GITHUB_ISSUE` and `LINEAR` with attributes taken from metadata, such as assignee email → person (`backend/onyx/kg/setup/kg_default_entity_definitions.py`).
- **Does not:**
  - The KG search tool is **disabled**. Its constructor raises `NotImplementedError("KnowledgeGraphTool should not be getting used right now.")` (`tools/tool_implementations/knowledge_graph/knowledge_graph_tool.py`).
  - No extraction of ticket keys from commit messages, branch names or code comments.
  - No PR → changed files, no ticket → file or symbol edges, no doc-URL → code edges.
  - I did not read all of `backend/onyx/kg/` (only models, entity definitions and the tool), so there may be extraction code I missed. None of it is reachable from search today.
- **This is the gap.** Code-Harness already has a code KG, so ticket → commit → file/symbol is a natural extension and something Onyx cannot do.

### 3d. Proposal: a minimal, local-first "external context" layer for Code-Harness

**Design principles:**
- Code stays the primary corpus.
- External text is opt-in, stored separately, redacted on ingest, cited with its URL, and **off by default in ranking** (`retrieval.external_weight: 0.0`), the same posture as wiki and memory weights.

**Pieces:**
1. **`harness/external/` connector interface**, a trimmed Onyx contract:
   ```python
   class ExternalDoc(BaseModel):       # trimmed Onyx Document
       id: str                         # "jira:ABC-123", "confluence:12345", "slack:C01/1712.0001", "figma:FILEKEY/NODEID"
       source: Literal["jira","confluence","slack","figma","file"]
       title: str; url: str
       sections: list[Section]         # Section{text, link|anchor, heading}
       metadata: dict[str, str | list[str]]   # status, labels, parent, assignee...
       updated_at: datetime | None
       visibility: str = "public"      # "private:<channel/space>" -> never exported / served with --allow-public
   class Connector(Protocol):
       def load(self, checkpoint: Checkpoint | None) -> Generator[ExternalDoc | Failure, None, Checkpoint]: ...
       def slim_ids(self) -> Iterable[str]: ...        # pruning
       def validate(self) -> None: ...                 # fail fast on auth/scope
   ```
   Checkpoints are JSON under `.code-harness/external/<source>/checkpoint.json`. Docs go to `docs.jsonl`, and failures to `failures.jsonl` so a later run can retry them, as Onyx's `Resolver` does.
2. **A separate index namespace:** Chroma collection `external_<repo>`, plus a BM25 sidecar. Code chunks and external chunks never share a collection, so code-only eval stays byte-identical when the layer is off.
3. **Linking into the KG** (no network needed):
   - Scan `git log` (subject, body, trailers), branch names (`git branch -a`, reflog), and code comments for issue keys and Confluence, Slack and Figma URLs.
   - Map `commit → changed files → symbols` (diff hunk line ranges against the entity spans CH already has).
   - Add edges `ticket:ABC-123 -mentioned_in-> commit`, `ticket -touches-> file/symbol`, and `doc_url -referenced_by-> file/symbol`.
   - This works **even without any connector**: the ticket node exists as soon as the key does.
4. **Source-aware fusion:**
   - External dense and BM25 become extra RRF lists with their own weights (like `wiki_weight`/`memory_weight`).
   - A per-source cap (R3).
   - A ticket key in the query triggers a direct ID lookup plus a graph hop to the linked code, mirroring CH's existing identifier → BM25-only shortcut.
   - `--sources jira,confluence` filters, validated against what is indexed, with close-match errors (R7).
   - Recency only for tickets and Slack, as a tie-break and not a multiplier, and only if eval says so.
5. **Packing and citations:**
   - A separate token sub-budget, e.g. `context.external_token_budget` (default 600, ≤25% of the pack).
   - Cite as `` `jira:ABC-123` `` / URL, alongside `` `path:symbol` ``.
   - The validity check extends to "cited external IDs exist in the store".
6. **Redaction and audit:**
   - Run `redact_and_audit` **at ingest** (external text is the most likely place for pasted secrets), and again on output.
   - Store only redacted text.
   - Content with `visibility=private:*` is excluded from OKF export and from `--allow-public` serve.
7. **Auth, offline and cache:**
   - Tokens come from `.env` only (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`; `CONFLUENCE_*`; `SLACK_BOT_TOKEN` with a channel allowlist; `FIGMA_TOKEN`).
   - `doctor` checks presence only, with no network call.
   - Queries **never** call the network. Only `ext sync` does.
   - If sync fails, the last good store is still served and `doctor` shows how stale it is.
   - Rate limits: honour `Retry-After` on 429, plus a token-bucket decorator modelled on `rate_limit_wrapper.py`, with a bounded number of retries.
8. **Figma:** feasible via Figma's REST API with a personal access token: file JSON gives frame, component and text-node names, plus comments. I did not verify current endpoint details or limits this session, and Onyx gives no prior art. Index text only (frame and component names, text layers, comments, dev-mode annotations if exposed). No images. Mark it **experimental**.

**Phased PRs** (each eval-gated, each default-off):

| PR | Scope | Effort | Gate |
|---|---|---|---|
| X0 | `ExternalDoc`/`Connector`/checkpoint/failures + a **`file` connector** (folder of exported `.md`/`.json` tickets and pages) + separate store; fixture with ~10 synthetic tickets/docs | S | Unit tests; code-only eval identical with layer off |
| X1 | **Git-only linking**: issue keys and URLs from commits, branches and comments → KG edges; `graph_neighbors` shows them; `query "ABC-123"` returns touched symbols | S–M | New **ticket→code** golden set (below); code-only Recall unchanged |
| X2 | External RRF list(s) + per-source cap + external sub-budget + `--sources` + citations + validity extension | M | Mixed-source suite; **noise ablation**: code-only queries must not lose Recall@k with the layer on |
| X3 | **Jira** connector (JQL `updated` window, cursor checkpoint, 429 backoff, comments, parent/epic, ingest redaction) | M | Contract tests with recorded HTTP fixtures (no live calls in CI) |
| X4 | **Confluence** (CQL `lastmodified`, storage→text, headings as sections with anchors) and **Slack** (thread = doc, channel allowlist, `visibility`) | M each | Same; redaction tests on seeded fake secrets |
| X5 | `context ticket ABC-123` / MCP `task_context`: ticket + linked docs + KG-linked code in one budgeted pack | M | Agent-task eval: citation-path hit on files later changed for that ticket |
| X6 | **Figma** (experimental) | M–L | Only if a real user dataset exists |

**Eval additions:**
- **Ticket→code golden queries, auto-mined from git history.** For each commit that references `ABC-123`, the golden set is the files and symbols it changed.
  - Two variants: (a) query = the ticket key (tests linking), and (b) query = the ticket *title or description* with the key removed (tests semantic retrieval, and is the honest one).
  - Failure mode: commits that touch many files inflate the golden set. Cap or weight by lines changed.
- **Mixed-source citation hit:** does the answer cite both the ticket and the right `path:symbol`?
- **New failure taxonomy entries:** `link_miss` (key present in git but no KG edge) and `external_noise` (an external hit displaced a golden code hit).
- **Redaction on external text:** seeded fake tokens must never reach the pack.
- **Staleness test:** sync, edit the fixture, run a query with no sync, and assert that the stale-but-served behaviour is reported.

**Trade-offs and failure modes to state up front:**
- External text is long and noisy, which risks crowding out code. That is why it gets its own budget and starts at weight 0.
- Ticket keys in commits are a *convention*. Repos without them only get value from X2–X4 semantic retrieval.
- Slack is the highest-risk source for secrets and private content. Keep an allowlist only, and never use default "all channels".
- Each connector is mostly maintenance work: API versions, pagination quirks, Confluence storage format. Keep the core interface small and the connectors thin. Onyx's connector files run to 1–2k lines each, which shows how much edge-case handling real APIs need.

---

## 4. Unverified or not checked
- Whether any remaining caller still uses the cross-encoder `RerankingModel` in search (likely not).
- Whether multipass large/mini chunks are used anywhere in the OpenSearch path (not found).
- The full `backend/onyx/kg/` pipeline (only the models, entity definitions and tool were read).
- docs.onyx.app was not usable for this pass (the MCP doc URL tried returned 404). All claims are from code.
- Code-Harness "has it?" columns come from README and a few files on `main`, not a full code read. In particular, verify R1 by checking what text `harness/chunker.py` puts in `Chunk.content`.

  **Verified against Code-Harness:** No — the embedded and BM25-indexed text is `Chunk.content` only (raw source or markdown). File path, symbol name, and signature are stored as separate fields and are not prepended into that text. `harness/chunker.py` sets `content` from `source_code` or line slices (`_make_chunk`, `_chunk_file`, `_chunk_class`, `_chunk_function`, `_chunk_documentation`). `main.py` embeds `[c.content for c in chunks]`. `harness/vector_store.py` stores `chunk.content` as the document and puts `file_path` / `entity_name` in metadata. `harness/retriever.py` `index_chunks` BM25-tokenizes `c.content` only.
- Figma REST API details (endpoints, rate limits) were not verified this session.
