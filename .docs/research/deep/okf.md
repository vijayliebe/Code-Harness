# Deep dive: OKF — Google Open Knowledge Format SPEC

**First-pass (corrected):** [../okf.md](../okf.md)  
**Plan slot:** [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) later #5 with memory; wiki #6 emits OKF pages  
**Seam:** `.code-harness/memory/`, future `wiki/` tree — interchange, not a retrieval engine

## 1. Ownership correction

**First-pass error:** treated OKF as “Memanto’s portable memory format,” citing only `docs.memanto.ai/integrations/okf`.

**Correction:** **Open Knowledge Format is a Google Cloud specification**, published as an open, vendor-neutral standard by the Google Cloud Data Cloud team.

| Role | Who |
|------|-----|
| **Owner / SPEC** | [GoogleCloudPlatform/open-knowledge-format](https://github.com/GoogleCloudPlatform/open-knowledge-format) — [`SPEC.md`](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md) (v0.2 as of this research) |
| **Announcement** | [Google Cloud Blog — How OKF can improve data sharing](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing) |
| **Sister ingest** | Google Cloud Knowledge Catalog can ingest OKF |
| **Implementer** | Memanto (export/import, `x_memanto` extension fields). Mem0 is unrelated storage. |

OKF formalizes the **LLM-wiki** pattern: a directory of markdown files with YAML frontmatter, **one concept per file**, identity = file path, plain markdown links, no schema registry, no required SDK. “If you can `cat` a file, you can read OKF; if you can `git clone` a repo, you can ship it.”

Memanto remains relevant as a *consumer* and as the typed-memory *agent* ([memanto.md](memanto.md)). It does not own the format.

## 2. SPEC v0.2 — what Code-Harness actually needs

Self-contained rules we should honor if we emit bundles:

- **Required:** every concept has a non-empty `type`. Types are **not** centrally registered. Consumers must tolerate unknown types (treat as generic).
- **Identity:** file path is the concept id. Do not invent a parallel UUID as the primary key (optional `id` in extensions is fine).
- **Links:** standard markdown links to other concept files.
- **Unknown fields:** preserve on round-trip (this is how `x_memanto` is legal).
- **Provenance / trust / lifecycle (v0.2):** `generated`, `verified`, timestamps — enough to mark wiki pages vs human decisions.
- **Attested Computation (v0.2):** sanctioned way to *compute* a value (runtime, parameters, computation, executor, attester). Useful later for eval metrics (“this Recall@k was produced by suite X”); **out of scope** for the first memory/wiki PR.
- **Conformance:** produce/consume the markdown+frontmatter subset; we do not need their visualizer or enrichment agent.

Reserved/example types from SPEC and blog: `Playbook`, `Reference`, `API Endpoint`, `Metric`, `Attested Computation`, catalog types (`BigQuery Table`, …). For a **code** repo we should mint obvious types, not pretend to be Dataplex:

```
Module | Symbol | Decision | Error | Preference | Gloss | Runbook | WikiPage | EvalSuite
```

Unknown to Memanto? Fine — they auto-classify; we keep our types.

## 3. Mapping onto Code-Harness artifacts

| Artifact today | OKF concept |
|----------------|-------------|
| `ARCHITECTURE.md` / `.docs/architecture.md` | `WikiPage` (human or generated) |
| `AGENTS.md` / `CLAUDE.md` | `Playbook` |
| Future gloss notes | `Gloss` linking to `Symbol` |
| Future typed memory | `Decision` / `Error` / `Preference` |
| `graph_{repo}.json` | **not** OKF (binary-ish graph). Export *views* (Mermaid, endpoint lists) as concept files |
| Chroma chunks | **not** OKF. Too many, too raw. Optional “symbol card” summaries only |

A bundle layout (committed or under `.code-harness/okf/`):

```
knowledge/
  wiki/architecture.md
  symbols/harness-context-builder.md
  decisions/2026-09-24-chroma-default.md
  eval/retrieval-suite.md
```

Each file: YAML frontmatter + prose + links to source `path:symbol` (Code Wiki style).

## 4. Producer / consumer

**Produce (wiki + memory PRs):** write SPEC-compliant files. `generated: true` on machine wiki pages; `verified: human` on decisions.

**Consume:** `ContextBuilder` already injects three filenames. Generalize to “load all `knowledge/**/*.md` with type ∈ brief-set, sort by recency, pack.” That is OKF-as-prefix, not a new database.

**Interchange:** `code-harness knowledge export <dir>` / `import <dir>`. Import must **preserve unknown keys**.

## 5. What not to do

- Do not implement Attested Computation runners.
- Do not require a Google Cloud account (SPEC is explicit: no proprietary account).
- Do not treat Memanto `x_memanto` as required. If we import a Memanto bundle, keep `x_memanto` untouched.
- Do not dump every chunk as a concept (bundle explosion).

## 6. File-level sketch (future)

| File | Change |
|------|--------|
| `harness/okf.py` (new) | parse/serialize frontmatter, walk bundle, preserve extras |
| `harness/memory.py` | persist as OKF concept files |
| wiki generator | emit `WikiPage` + `Symbol` concepts + mermaid fenced blocks |
| `main.py` | `knowledge export|import` |

## 7. Acceptance

- A 5-file sample bundle validates against a **checklist** copied from SPEC §conformance (type present, path identity, links resolve, unknown field round-trips).
- Import of a Memanto-exported bundle does not crash; `x_memanto` survives.
- README states: “OKF = Google Cloud SPEC; we implement a code-repo subset.”

## 8. Risks

- SPEC will move (0.1 → 0.2 already added provenance + attestation). Pin “we emit v0.2 subset” in export metadata (`okf_version: 0.2`).
- Name collision with Open Knowledge Foundation (also “OKF”). Always say **Open Knowledge Format (Google SPEC)**.

## 9. Recommended PR slice

Same PR as typed memory (later #5), or the wiki PR (#6) if memory slips. Do not block PRs 1–4 on OKF.

## 10. Frontmatter we will emit

```yaml
---
type: Decision          # required, SPEC
title: Default vector backend stays Chroma
description: Keep Chroma until TurboVec recall@k passes.
generated: false
verified: human
timestamp: 2026-09-24T00:00:00Z
tags: [vector, chroma]
okf_version: "0.2"
x_codeharness:
  supersedes: null
  repo: Code-Harness
---
```

`x_codeharness` is our extension bag (same rule as `x_memanto`). SPEC consumers ignore it; we must preserve foreign `x_*` on import.

## 11. Conformance checklist (export tests)

1. Every `.md` in the bundle has YAML frontmatter with non-empty `type`.
2. No two files share a path; path is the id.
3. Relative markdown links resolve inside the bundle or are explicitly external.
4. Re-parse after `export → import` keeps unknown keys and body bytes (modulo trailing newline).
5. `okf_version` is present on files we produced.
6. We do not require `Attested Computation` fields.

## 12. First-pass vs deep (changelog)

| First-pass | Deep |
|------------|------|
| “Promoted by Memanto” | Google Cloud SPEC; Memanto implements |
| Primary URL = Memanto docs | Primary URL = `SPEC.md` |
| `MEMORY.md`-style sync | `knowledge/` concept tree |
| RFC-stage caveat only | Pin v0.2 subset + name-collision warning |

## 13. Failure modes

| Symptom | Mitigation |
|---------|------------|
| Memanto import overwrites types | Prefer `x_memanto.type` if present; else keep our `type` |
| Contributor commits secrets | `.gitignore` optional `knowledge/local/`; review docs |
| Wiki generator emits 500 files | Cap: package-level pages, not per-chunk |

## 14. Implementation checklist

- [ ] `okf_version: "0.2"` on produced files
- [ ] Required `type`; unknown types tolerated on read
- [ ] Unknown key round-trip test (include a fake `x_other`)
- [ ] Import Memanto sample without crash (if we vendor one fixture)
- [ ] README names Google SPEC as owner
- [ ] No Google Cloud client libraries

## 15. Cross-links

- Memory files are OKF: [memanto.md](memanto.md)
- Wiki pages are OKF: [google-code-wiki.md](google-code-wiki.md)
- First-pass card corrected: [../okf.md](../okf.md)
