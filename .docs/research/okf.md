# OKF — Open Knowledge Format (Google SPEC)

## What it is
**Google Cloud specification** for portable, vendor-neutral knowledge bundles: a directory of **Markdown files with YAML frontmatter**, one concept per file, identity = path. Formalizes the “LLM-wiki” pattern. **Not** a Memanto-owned format — Memanto is an *implementer* (export/import).

## Links
- SPEC (owner): https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md (v0.2)
- Repo: https://github.com/GoogleCloudPlatform/open-knowledge-format
- Announcement: https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing
- Implementer (Memanto): https://docs.memanto.ai/integrations/okf
- Memanto RFC/issue: https://github.com/moorcheh-ai/memanto/issues/1409

## How it works
- Minimal: `cat` / `git clone` are enough; no schema registry, no required SDK or cloud account.
- Every concept needs a non-empty `type`; types are not centrally registered; unknown types and extra fields must be preserved (this is why `x_memanto` is legal).
- v0.2 adds provenance / trust (`generated`, `verified`) / lifecycle and Attested Computation contracts.
- Memanto maps bundles to typed memories and stores vendor extras under `x_memanto` for round-trip.

## Relevance to Code-Harness
**High (portability + accuracy).** Code-Harness already injects ARCHITECTURE.md/AGENTS.md/CLAUDE.md. OKF is the interchange layer for generated wiki pages and typed project memory — implement the Google SPEC subset, do not treat Memanto as the source of truth.

## Concrete ideas to adopt
1. **Native OKF import/export** (accuracy/portability): Round-trip project decisions/notes + optional symbol-card summaries. Preserve unknown keys.
2. **`knowledge/` bundle as first-class context source** (tokens): Brief typed memories / wiki pages instead of huge chat logs.
3. **Git-diffable knowledge** (accuracy): Review memory/wiki changes in PRs.
4. **Import Memanto (or other) bundles** (ecosystem): Meet users where they are; keep `x_*` extensions.

## Risks/caveats
- SPEC is evolving (0.1 → 0.2). Pin `okf_version` on export.
- Don’t put secrets into committed OKF bundles.
- Name collision with Open Knowledge Foundation — say “Open Knowledge Format (Google SPEC).”

## Open questions
- Publish a minimal code-repo type set (`Module`, `Symbol`, `Decision`, `WikiPage`, …)?

## Deep dive
Design note: [deep/okf.md](deep/okf.md)
