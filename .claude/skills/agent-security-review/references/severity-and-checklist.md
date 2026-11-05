# Severity and Security Checklist

## Severity

Derive severity as **Vulnerability × Reachability × Exposure × Action
Criticality** — four separate questions, not one impression:

- **Vulnerability**: is the defect pattern real (confirmed by a concrete
  source-to-sink or authority path, not just a code smell)?
- **Reachability**: is there a live caller today? A defect with zero live
  callers (confirmed dead code) is a `reliability_findings` entry, not a
  security finding at face-value severity — check for callers, don't assume.
- **Exposure**: public internet, private network/VPN-only, localhost-only,
  or gated behind an upstream layer (API gateway, reverse proxy, platform
  auth like Easy Auth) you haven't yet confirmed the absence/presence of?
  Ask/check for an upstream auth layer before treating "no auth in this
  code" as "no auth at all" — note the answer under `needs_verification` if
  it can't be confirmed statically.
- **Action Criticality**: what does reaching this path actually let someone
  do? Score a read/disclosure endpoint separately from a write/state-
  changing one even when they share one finding's code pattern — don't let
  a write endpoint's severity get averaged away by bundling it with a
  read-only sibling.

- **P0 Critical** — unauthenticated or broadly reachable path to major confidential-data disclosure, destructive/privileged action, tenant-wide compromise, credential theft with immediate high impact, or remote code execution in a privileged context.
- **P1 High** — practical path to sensitive data, privilege escalation, unauthorized writes/actions, or material prompt/tool abuse requiring limited preconditions.
- **P2 Medium** — meaningful weakness with constrained impact, stronger prerequisites, or compensating controls.
- **P3 Low** — hardening, defense-in-depth, audit-quality, or low-impact weakness.

When re-reviewing a previously-reported finding, re-derive its severity from
the four questions above using current evidence; do not carry the old
severity forward unexamined just because the finding already exists.

## Checklist

### Identity and access
- Unique service/agent identity where feasible
- Least privilege
- No authorization decision based only on model output
- Sensitive tools enforce authorization server-side
- Admin/write actions require explicit scope
- Tenant/user context cannot be forged through prompt text

### MCP and tools
- Tool allowlists are narrower than "all tools"
- Tool descriptions do not grant authority
- Arguments are validated independently of the LLM
- High-impact tools use confirmation or policy gates where appropriate
- Remote MCP servers are authenticated and trusted

### Prompt injection / untrusted content
- Retrieved pages/files/messages are treated as untrusted data
- External text cannot override system policy
- Tool calls derived from untrusted content are constrained
- Secrets are never placed in prompts unnecessarily
- Output destinations are validated

### Data protection
- PII/confidential data has defined storage/transmission boundaries
- Logs avoid raw secrets and sensitive payloads
- Egress destinations are constrained
- Data is not mixed across tenants/users
- Model/provider data handling matches policy

### Application security
- No hard-coded secrets
- Parameterized DB access
- Safe URL/file/path handling
- Safe deserialization and command execution
- TLS verification enabled
- CORS and debug modes restricted
- Dependencies reviewed with available ecosystem tools

### Auditability
- Sensitive actions include actor/agent identity, action, resource, result, and timestamp
- Logs are sufficient for incident reconstruction
- Audit records cannot be trivially spoofed by prompt text

## Justified exclusions (do not count against scope completeness)

These are legitimate, by-design reasons to skip something — record them
under `limitations` in the report (see SKILL.md Output Contract), not as a
reason to mark the review `partial`:

- Vendor/build/generated directories (`node_modules`, `dist`, `build`,
  `.next`, lockfiles) not reviewed line-by-line.
- Secret values (API keys, connection strings, tokens) never read or
  printed, per this skill's own safety rule — note existence/schema only.
- Confirmed-decommissioned/archived code excluded from git or unreachable
  from any live entrypoint, not deep-reviewed line by line (still worth a
  reachability confirmation, just not a full line audit).
