# Jitro (Project Jitro / Jules-related)

## What it is
**Ambiguous / not a public open-source repo.** Trade press in 2025–2026 describes **Google “Project Jitro”** as a rumored next-gen / evolution of **Jules** — a persistent, goal-driven coding agent with repository-wide context, MCP integrations, and iterative execution. Public surface today is closer to **Jules** (Google Labs coding agent) and `google-labs-code/jules-sdk`.

## Links / candidates
- Jules API docs: https://developers.google.com/jules/api
- Jules SDK: https://github.com/google-labs-code/jules-sdk
- Press roundups (non-primary): analyticsindiamag / branticles pieces on “Google Jitro AI”
- Related Google product (public): [Code Wiki](https://codewiki.google/) — see `google-code-wiki.md`

## How it works (as described publicly for Jules / rumors)
- GitHub-connected coding sessions via API/SDK.
- Persistent goals, iterative plan→act→verify.
- Broad repo context rather than single-file chat.
- MCP-style tool integrations (reported).

Exact “Jitro” internals are **not published** as of research date.

## Relevance to Code-Harness
**Low–Medium (inspirational only).** No transferable open implementation. Themes align: repo-wide context, persistent sessions, iterative verify — same problems Code-Harness context packing aims to solve.

## Concrete ideas to adopt
1. **Persistent goal + verify loop** around retrieval (accuracy): Query → retrieve → answer → self-check citations against chunks → re-retrieve if gaps.
2. **Repo-session state** (tokens): Cache last good retrieval set per “goal” thread.
3. **Watch Jules/Code Wiki APIs** for patterns when more docs land.

## Risks/caveats
- Rumor risk: “Jitro” may be mistyped Jules / internal codename.
- No code to adopt; avoid over-fitting to press claims.

## Open questions
- Is there an official Google page using the name Jitro? (Not found in primary sources during this research.)
