# Mini-deepen: Jitro (weak primary)

**First-pass:** [../../jitro.md](../../jitro.md)  
**Live skim (2026-09-25):** Jules API/SDK (primary); press on “Project Jitro” / Jules V2 (secondary)  
**Why this note:** no public Jitro repo still. Press now names I/O 2026 Jules V2 as the internal codename — still **not an implementation we can fuse**.

## What is actually public

- **Jules** (Google Labs): GitHub-connected coding agent, Jules Tools CLI, Jules API (2025-10), eval writeup “Measuring What Matters with Jules” (2026-06) on *goal* vs *task* benchmarks.
- **Jitro / Jules V2:** trade press (AI Weekly, Medium recaps, architecture blogs) describes KPI-driven persistent workspaces, MCP to CI/observability, plan→approve-direction→sandbox execute. No spec, no SDK named Jitro, no algorithms.

## Stealable? Almost nothing new

Themes already covered by shipped loop + deferred agent work:

- Persistent goal + verify → we have bounded retrieve loops + optional `--verify`, not a write agent.
- Repo-wide context → that *is* hybrid RAG.
- Insight / KPI loop → **reject** for this product (write/CI agent).

## Fusion call

**reject** — rumor/waitlist product; no mechanism to own in-process.  
Keep watching Jules API only as a *consumer* of `POST /v1/retrieve` if they ever allow local tools. Evidence quality: **weak-primary**.
