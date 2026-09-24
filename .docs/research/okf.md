# OKF — Open Knowledge Format

## What it is
**Portable memory/knowledge interchange format** promoted by Memanto: export/import agent memory as plain **Markdown (+ YAML frontmatter)** bundles — diffable, greppable, git-friendly — not a proprietary dump.

## Links
- Docs: https://docs.memanto.ai/integrations/okf
- Memanto RFC/issue: https://github.com/moorcheh-ai/memanto/issues/1409
- Commands: `memanto memory export --okf`, `migrate --okf`, `sync --okf`

## How it works
- Bundle grouped by memory type (decision, preference, …).
- Vendor extensions in `x_memanto` (ids, confidence, provenance, status) for lossless round-trip.
- Designed so competitors can implement the same format deliberately.
- Sync into project `MEMORY.md`-style files.

## Relevance to Code-Harness
**High (portability + accuracy).** Code-Harness already injects ARCHITECTURE.md/AGENTS.md/CLAUDE.md. OKF generalizes that into a typed, migratable knowledge pack.

## Concrete ideas to adopt
1. **Native OKF import/export** (accuracy/portability): Round-trip project decisions/notes + optional chunk metadata summaries.
2. **`MEMORY.md` / OKF as first-class context source** (tokens): Brief typed memories instead of huge chat logs.
3. **Git-diffable knowledge** (accuracy): Review memory changes in PRs.
4. **Migrate from Mem0/Memanto** (ecosystem): Meet users where they are.

## Risks/caveats
- Format may still evolve (RFC stage).
- Don’t put secrets into committed OKF bundles.

## Open questions
- Publish a minimal OKF subset schema for code projects?
