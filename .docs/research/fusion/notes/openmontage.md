# Mini-deepen: OpenMontage

**First-pass:** [../../openmontage.md](../../openmontage.md)  
**Live skim (2026-09-25):** [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) (~61k stars, AGPL)  
**Why this note:** confirm there is no hidden code-RAG core.

## Confirmed

Agentic **video** studio: 12 pipelines, 100+ tools, 700+ skill/knowledge files, Remotion/FFmpeg, Piper TTS, scored clip selection, human approval gates.

## Stealable overlap

- Skill/knowledge file packs → we already inject `ARCHITECTURE.md` / `AGENTS.md` / `CLAUDE.md`; OKF/wiki is the real next step (Memanto/Code Wiki), not a `skills/` video corpus.
- Multi-dimension scored selection → our heuristic grade + CE + MMR already score retrieve hits. Do not add a 7-dim video rubric.
- Approval gates → governance, only if writes exist.

## Fusion call

**reject** — domain noise. Any remaining “skill pack” energy goes to OKF `Playbook` / wiki pages. Evidence: first-pass + live README.
