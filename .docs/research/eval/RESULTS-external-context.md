# External context (X0–X1) — golden + ticket-to-code

Scored on this repo with `.docs/research/eval/code-harness.fixture.yaml`, plus a mined ticket-to-code pair on a temporary git repo (this history has no `ABC-123` keys).

| Field | Value |
|-------|--------|
| SHA | `6fb42b749db1c723de7a7301aa7c5bd97f7564c7` |
| Branch | `feature/external-context-x0-x1` (stacked on `feature/retrieval-quick-wins` / `96aab77`) |
| Embedder | `all-MiniLM-L6-v2` (local) |
| Vector | Chroma (default), 4372 chunks — same persist dir as the R1–R5 shipped index |
| Decision | off |
| max_loops | 0 |
| verify | off |
| n (code-only) | 8 |
| n (ticket-to-code) | 2 |
| k | 10 |

`python3 main.py external mine .` → **0 fixtures** (no ticket keys in commits, branches, or comments). Ticket-to-code numbers below are from a temp repo: one commit `ABC-123 fix login timeout after SSO` touching `src/auth.py`, plus an ingested `ABC-123.md`. Cross-encoder off for that tiny corpus (rank_bm25 IDF is 0 on 1–2 docs; ticket lookup + overlap still fire).

## Code-only A/B (sidecar ingested: ABC-123.md + roadmap.md)

| Arm | Recall@10 | nDCG@10 | citation-path hit | packed tokens (full / CCR-lite mean) |
|-----|-----------|---------|-------------------|--------------------------------------|
| External off (default) | 0.3125 | 0.3066 | 1.0 | 1334.0 / 704.8 |
| External on (`--external`, weight 0.15) | 0.375 | 0.3396 | 1.0 | 1272.0 / 676.5 |

Enabling the sidecar did **not** reduce code-only Recall@10 / nDCG@10 / cite. One hard query (`q-who-calls-build-context`) moved 0.5 → 1.0; the ingested `roadmap.md` literally names “context builder”, so that lift is a lexical collision, not a reason to default-on.

## Ticket-to-code (temp git, CE off)

| Arm | Recall@10 | nDCG@10 | citation-path hit | packed tokens (full / CCR-lite mean) |
|-----|-----------|---------|-------------------|--------------------------------------|
| with key (`ABC-123 fix login timeout after SSO`) | 1.0 | 0.6309 | 1.0 | 180 / 166 (suite mean 149.5 / 166.0) |
| no key (`fix login timeout after SSO`) | 1.0 | 0.6309 | 1.0 | 119 / 166 |
| Suite mean (n=2) | 1.0 | 0.6309 | 1.0 | 149.5 / 166.0 |

Both variants cited `src/auth.py`. nDCG is not 1.0 because the file-level chunk and the `refresh_session` chunk share the path; path-gold treats the second hit as extra.

## Defaults after this A/B

- **`external.enabled` stays false.** Ingest is CLI-only. The sidecar RRF channel is opt-in (`--external` / `CODEHARNESS_EXTERNAL=1`). A chatty export can shuffle top-k even when it “helps” one query.
- **`external.ticket_lookup` stays true.** No-op unless a query contains a ticket key *and* `external link` has written mentions (or a matching doc was ingested). Golden queries have no keys; ticket latency ~0.4 ms.
- MiniLM, Chroma, decision off, max_loops 0, verify off, R1 off, R2/R3/R5 on — unchanged.

## Caveats

- Code-only absolute numbers here (0.3125 / 0.3066) are below the R1–R5 shipped table (0.5 / 0.4646) on the same 4372-chunk index. Treat the paired off/on arms on this SHA as the X0–X1 evidence, not a regression verdict against that earlier table. Cross-encoder order can move on a small n=8 suite.
- This repo cannot produce a mined ticket suite. Do not treat the temp-repo n=2 as a general linker benchmark.
- Hyphenated keys are not BM25 tokens (`ABC-123` → `abc` + `123`). Ticket lookup is the reliable path.
- `eval-ab` / TurboVec was not re-run (extra not installed).
