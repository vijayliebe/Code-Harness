# WorkOS

## What it is
Commercial **enterprise identity platform**: SSO (SAML/OIDC), Directory Sync (SCIM), MFA, Admin Portal — plus newer **Agent Auth / Agent Registration** (auth.md, OAuth discovery, service_auth, scoped tokens for AI agents).

## Links
- https://workos.com/
- Agent Registration: https://workos.com/docs/authkit/agent-registration
- Agent Auth / blueprints: https://workos.com/docs/authkit/agent-blueprints
- auth.md: https://workos.com/auth-md/docs/auth-md

## How it works
- Humans: AuthKit SSO/MFA/SCIM.
- Agents: register via auth.md + OAuth; anonymous or service_auth bound to a user via claim ceremony; exchange for short-lived scoped tokens; revocable.

## Relevance to Code-Harness
**Low** for current local CLI RAG. Becomes relevant if Code-Harness ships multi-tenant SaaS / team server with agent access to private repos.

## Concrete ideas to adopt
1. **Defer SSO** until SaaS; keep local-first.
2. **If SaaS:** scoped tokens per repo; agent identity ≠ user identity; audit agent actions.
3. **auth.md** convention if exposing remote MCP — document agent auth expectations.

## Risks/caveats
- Vendor lock-in; overkill for open-source CLI.
- Don’t block OSS users behind enterprise auth.

## Open questions
- Any plan for hosted Code-Harness cloud?
