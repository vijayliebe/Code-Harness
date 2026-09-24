# Obsidian (Obsidian.md + AI / knowledge-graph plugins)

## What it is
**Obsidian.md** — local-first Markdown knowledge base with bidirectional links and graph view. Ecosystem includes AI plugins (Copilot, Smart Connections) and code-oriented graph plugins.

## Links
- Product: https://obsidian.md
- Smart Connections: https://github.com/brianpetro/obsidian-smart-connections
- Copilot plugin: https://community.obsidian.md/plugins/copilot
- Code Graph plugin example: https://github.com/mrjw717/obsidian-code-graph

## How it works
- Notes as Markdown files; `[[wikilinks]]` form a personal knowledge graph.
- Smart Connections: local embeddings + related-note retrieval + Smart Graph viz.
- Copilot: vault-aware chat/RAG with local or BYOK models.
- Code Graph plugins: tree-sitter extraction of imports/calls/inheritance into note↔code links.

## Relevance to Code-Harness
**Medium.** UX/mental model transfer: living notes + graph + semantic links. Code-Harness already has a code KG; Obsidian patterns help for *human-authored* overlays (Gloss/notes) and graph navigation UX.

## Concrete ideas to adopt
1. **Wiki/gloss nodes on the code graph** (accuracy): Allow agent/human notes attached to entities (like Code-Graph-RAG “Gloss” / Obsidian notes). Why: captures tribal knowledge not in source.
2. **Bidirectional link UX in visualize** (accuracy): Click entity → neighbors + linked notes (Obsidian graph feel).
3. **Export KG + notes as Markdown vault** (tokens/portability): Human-editable overlay; re-index notes into BM25/vector.
4. **Local-first embeddings for notes** (cost): Keep project notes offline like Smart Connections.

## Risks/caveats
- Obsidian itself isn’t a code RAG engine; plugins vary in quality.
- Don’t force users into Obsidian — optional export/import is enough.

## Open questions
- First-class `NOTES.md` / gloss store vs Obsidian vault sync?
