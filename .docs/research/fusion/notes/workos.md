# Mini-deepen: WorkOS Agent Auth

**First-pass:** [../../workos.md](../../workos.md)  
**Live skim (2026-09-25):** [Agent Registration](https://workos.com/docs/authkit/agent-registration)  
**Why this note:** confirm whether `auth.md` / agent tokens matter for a local CLI.

## Confirmed mechanism

WorkOS Agent Registration: discover via OAuth well-known + `auth.md` skill, register `anonymous` | `service_auth` | `refresh`, optional claim ceremony, exchange JWT for scoped access token / API key, `act` delegation claim (RFC 8693). Dashboard-gated; contact account team.

This is **enterprise identity for remote agents calling a hosted API**.

## Fusion call

**reject** for Code-Harness while the product is a local-first CLI.  
If `mcp serve` ever binds a *remote* interface, document a **local token file** (not WorkOS) and optionally mention `auth.md` as a convention. No WorkOS SDK. Evidence: first-pass + live docs.
