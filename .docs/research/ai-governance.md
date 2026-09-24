# AI Governance (practical OSS / patterns)

## What it is
Practical control planes for LLM/agent apps: policy before tool calls, budgets, approvals, kill switches, audit logs, PII redaction — not only board-level AI ethics docs.

## Links / exemplars
- Agent Patterns — governance overview: https://www.agentpatterns.tech/en/governance/governance-overview
- Agent Control Plane: https://github.com/ryanwi/agent-control-plane
- Kite Logik (OPA/Rego): https://github.com/kitelogik/kitelogik
- LLM Governance Gateway: https://github.com/citadelgrad/llm-governance-gateway
- OpenAI cookbook — Agentic Governance: https://developers.openai.com/cookbook/examples/partners/agentic_governance_guide/agentic_governance_cookbook

## How it works
Common pattern: **default-deny** policy check on every tool/LLM call → BudgetTracker / max_steps → ApprovalGate for sensitive actions → KillSwitch → append-only EventStore. Gateways add PII redaction, tenant policies, rate limits.

## Relevance to Code-Harness
**Medium.** Critical when Code-Harness becomes an agent that can write/run shell; lighter for read-only RAG. Still useful: cost budgets, audit of what context was sent, redaction of secrets in chunks.

## Concrete ideas to adopt
1. **Secret redaction in context assembly** (governance/tokens): Strip API keys/tokens from chunks before LLM.
2. **Max tokens / max $ budget per query** (cost): Hard stop + clear error.
3. **Audit log** (governance): Query, chunk_ids, model, token counts → JSONL.
4. **Write-path approval** if agent features added.
5. **Policy pack** as optional YAML (paths never sent, PII patterns).

## Risks/caveats
- Overbuilt governance slows a local CLI; keep optional/progressive.
- OPA may be heavy — start with simple hooks.

## Open questions
- Default-on redaction vs opt-in?
