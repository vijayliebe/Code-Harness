# Google Code Wiki

## What it is
Google product (public preview ~Nov 13, 2025): AI-maintained **living wiki** for code repositories — always-updated docs, diagrams, and repo-aware Gemini chat. Site: https://codewiki.google/

## Links
- Announcement: https://developers.googleblog.com/en/introducing-code-wiki-accelerating-your-code-understanding/
- Product: https://codewiki.google/
- Gemini CLI extension for private repos: waitlist (announced)

## How it works
1. Ingest public GitHub repo.
2. Continuously regenerate structured wiki as code changes.
3. Architecture / class / dependency / sequence diagrams auto-generated and kept current.
4. Every section + chat answer hyperlinked to exact source files/defs.
5. Chat uses the wiki (not a generic model) as knowledge base.

## Relevance to Code-Harness
**High (product vision).** Code-Harness retrieval answers questions; Code Wiki shows the complementary artifact: **persistent, navigable, regenerated documentation** grounded in code — ideal context to *inject* into RAG (better than ad-hoc ARCHITECTURE.md drift).

## Concrete ideas to adopt
1. **`code-harness wiki generate`** (accuracy/tokens): LLM+graph pass producing ARCHITECTURE.md, module pages, sequence sketches — then index those pages as high-priority chunks.
2. **Deep links in answers** (accuracy): Cite `path:symbol` like Code Wiki hyperlinks.
3. **Regen-on-watch** (accuracy): `watch` already reindexes; also regenerate wiki stubs for changed modules only.
4. **Diagram artifacts** (accuracy): Export Mermaid from KG (inheritance/call) into wiki pages.
5. **Chat-over-wiki+code** (tokens): Prefer wiki summaries first; expand to source chunks on demand (CCR-style).

## Risks/caveats
- Closed Google service for public repos; private support TBD — must build OSS equivalent ideas, not depend on Google.
- Wiki generation is expensive — incremental + cache.

## Open questions
- Minimum viable wiki: single ARCHITECTURE.md vs multi-page tree?

## Deep dive
Design note: [deep/google-code-wiki.md](deep/google-code-wiki.md)
