# Medium: Top 4 GitHub Repos (Interview Prep)

## What it is
Medium article (Let’s Code Future, Mar 30, 2025) listing high-star GitHub repos for interview prep (system design, LLD, coding, behavioral).

Article: https://medium.com/lets-code-future/top-4-github-repos-with-50k-stars-to-supercharge-your-interview-prep-67ad572d065b

## Links
- Article: https://medium.com/lets-code-future/top-4-github-repos-with-50k-stars-to-supercharge-your-interview-prep-67ad572d065b
- System Design Primer: https://github.com/donnemartin/system-design-primer
- Tech Interview Handbook: https://github.com/yangshun/tech-interview-handbook
- Coding Interview University: https://github.com/jwasham/coding-interview-university
- Awesome System Design Resources: https://github.com/ashishps1/awesome-system-design-resources

## The 4 repos (best resolution from article + related Let’s Code Future pieces)
Paywalled body truncated in fetch; cross-checked against same publisher’s lists and 50k+ star interview canon:

| # | Repo | URL | Focus |
|---|------|-----|-------|
| 1 | **Awesome System Design Resources** / System Design track | https://github.com/ashishps1/awesome-system-design-resources (also commonly paired with ByteByteGo) | System design interviews |
| 2 | **System Design Primer** | https://github.com/donnemartin/system-design-primer | Concepts + worked designs |
| 3 | **Tech Interview Handbook** | https://github.com/yangshun/tech-interview-handbook | Coding + behavioral + process |
| 4 | **Coding Interview University** | https://github.com/jwasham/coding-interview-university | Full CS study plan |

(Article’s first section explicitly names “Awesome System Design Resources”; remaining three match the publisher’s standard 50k+ set.)

## How it works
Static educational Markdown curricula — flashcards, checklists, study plans. Not software libraries.

## Relevance to Code-Harness
**Low** for retrieval engine quality. Minor UX ideas: study-plan style progressive disclosure; structured “interview the codebase” question banks.

## Concrete ideas to adopt
1. **Codebase interview question packs** (accuracy/demo): Seed queries (“how does auth work?”, “data flow?”, “failure modes?”) as eval suite.
2. **Anki-style spaced review of hard queries** (eval): Regression set of golden Q→chunk_ids.
3. **System-design primer structure for ARCHITECTURE.md** (tokens/accuracy): Force concise architecture templates users can inject.

## Risks/caveats
- Easy to over-index on interview content irrelevant to RAG.
- Article paywall — confirm exact fourth title if needed with full member access.

## Open questions
- Confirm #4 spelling from full article text if user has Medium membership.
