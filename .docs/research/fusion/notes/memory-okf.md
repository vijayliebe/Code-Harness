# Typed memory ↔ OKF mapping (fusion PR #2)

OKF = [Open Knowledge Format (Google SPEC v0.2)](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md). Code-Harness implements the markdown + YAML frontmatter subset in `harness/okf.py` — the same module wiki uses for `WikiPage`. Memory does **not** fork a second schema.

## Store

| Location | Role |
|----------|------|
| `knowledge/memory/{kind}/{date}-{slug}.md` | Default committed vault (matches `knowledge/wiki/`) |
| `.code-harness/memory/` | Optional local overlay (read if present) |

Identity is the file path (SPEC). `id: mem/YYYYMMDD-slug` is an extension so `supersedes` can name a stable pointer.

## Kinds

CLI / store kinds are lowercase. OKF `type` is PascalCase, same convention as `WikiPage`.

| CLI `--type` | OKF `type` | Brief prior |
|--------------|------------|-------------|
| `decision` | `Decision` | 1.0 |
| `error` | `Error` | 0.9 |
| `preference` | `Preference` | 0.6 |
| `fact` | `Fact` | 0.5 |

Unknown types are tolerated on read (SPEC). Writes require one of the four kinds plus a non-empty `title` and `body`.

## Frontmatter

Shared SPEC fields (`type`, `title`, `description`, `generated`, `verified`, `timestamp`, `tags`, `okf_version`) plus extensions:

| Field | Where | Meaning |
|-------|-------|---------|
| `id` | top-level + `x_codeharness.id` | `mem/…` pointer |
| `status` | top-level + bag | `active` / `superseded` / `forgotten` |
| `supersedes` | top-level + bag | prior `id` |
| `x_codeharness.links` | vendor bag | `path:symbol` cites |
| `x_codeharness.kind` | vendor bag | `memory` |
| `x_*` foreign | extras | preserved on export → import (`x_memanto`, `x_other`, …) |

`generated: false`, `verified: human` on human memories. Wiki pages stay `generated: true` / `verified: false`.

## Brief

`memory brief` packs **active** (non-superseded, non-forgotten) entries. Ranking is deterministic: type prior + recency, plus token-overlap when `-q` is set. Hard cap **≤800 tokens** using the same `len(text) // 4` estimator as `ContextBuilder`. Also caps at 5 items. Query/CCR injection is **opt-in** (`--include-memory-brief` / `context.include_memory_brief`, default off).

## CLI

```
python main.py memory add . --type decision --title "..." --body "..." --link path:symbol
python main.py memory list . [--type decision] [--as-of 2026-06-01] [--all]
python main.py memory brief . -q "why chroma?"
python main.py memory export . ./okf-bundle
python main.py memory import . ./okf-bundle
```

No Memanto/Mem0 dependency. No auto-extract from chats (`observe_stub` only).

## Sample `path:symbol` entries (this repo)

- Decision (active): `knowledge/memory/decision/2026-09-24-chroma-default.md` → `harness/vector_store.py:VectorStore` (supersedes the FAISS decision)
- Decision (tombstone): `knowledge/memory/decision/2026-01-01-use-faiss.md`
- Error: `knowledge/memory/error/2026-09-24-faiss-oom.md` → `harness/vector_store.py:VectorStore.add_chunks`
