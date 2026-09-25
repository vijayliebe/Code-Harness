# Mini-deepen: Obsidian as memory / gloss overlay

**First-pass:** [../../obsidian.md](../../obsidian.md)  
**Live skim:** Obsidian product + Smart Connections (local embeddings) + OpenHuman’s Karpathy-style vault (adjacent)  
**Why this note:** gloss **nodes** shipped; the remaining steal is a **human-editable vault**, not an Obsidian plugin.

## What already landed (do not re-steal)

- Gloss markdown under `.code-harness/gloss/` or `knowledge/gloss/` with `entity:` frontmatter.
- `RelationshipType.GLOSS`, boost in `ContextBuilder._rerank`, beam prior 0.9.
- Example: `knowledge/gloss/context-builder.md`.

That is the Obsidian *note-on-a-node* idea without requiring the app.

## Remaining mechanisms

1. **Vault export** — dump KG entities + gloss + future wiki pages as a folder of markdown with `[[wikilinks]]` (or OKF relative links). Humans can open it in Obsidian *or* just git-diff it. Re-index on `watch`.
2. **Bidirectional visualize** — click entity → neighbors + gloss (visualizer already ignores unknown edge labels; it does not show gloss as first-class).
3. **Smart Connections pattern** — local embeddings over *notes*, not a second cloud index. We already embed documentation chunks; vault files should use the same embedder.
4. **Memory Tree vs gloss** — OpenHuman compresses life-data into scored markdown trees. For Code-Harness, **typed OKF decisions** (Memanto) are the project-level store; gloss stays **entity-attached**. Do not merge them.

## Fusion call

**partial** overall (gloss done).  
**P1** vault export if it is the same PR as wiki/OKF (one `knowledge/` tree).  
**P2** visualize gloss UX.  
Never require Obsidian; export is enough.
