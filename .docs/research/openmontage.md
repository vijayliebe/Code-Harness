# OpenMontage

## What it is
Open-source **agentic video production system**: turns coding assistants (Claude Code, Cursor, etc.) into a full video studio — research, scripting, assets, narration, editing, Remotion/FFmpeg render. AGPL-3.0.

## Links
- GitHub: https://github.com/calesthio/OpenMontage (~50k+ stars)
- Site: https://www.openmontage.video/

## How it works
- 12 production pipelines, 100+ tools, 700+ agent skill/knowledge files.
- Agent-driven corpus of stock footage → retrieve clips → timeline edit → render.
- Local free path (Piper TTS, Remotion, FFmpeg) + optional cloud generators.
- Multi-point self-verification / scored selection with human approval gates.

## Relevance to Code-Harness
**Low.** Domain is video production, not code RAG. Still useful as a *pattern* for skill/knowledge packs and scored multi-stage pipelines.

## Concrete ideas to adopt
1. **Skill/knowledge file packs** (accuracy): Ship curated `skills/` for common code Q&A patterns (auth, data flow, tests) injected like ARCHITECTURE.md.
2. **Scored multi-stage verification** (accuracy): After retrieval, score candidates on N dimensions before LLM (OpenMontage-style 7-dim scoring).
3. **Human approval gates** (governance): Optional confirm before applying agent edits.

## Risks/caveats
- AGPL; don’t copy. Video-specific tooling irrelevant.

## Open questions
- None critical for Code-Harness core.
