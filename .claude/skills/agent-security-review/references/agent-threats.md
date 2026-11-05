# Agent Threat Patterns

Use these as hypotheses to test, not as automatic findings.

- Prompt injection causes a tool call the user did not authorize.
- Retrieved document or web content changes the agent's operating instructions.
- A low-trust agent can call a high-trust tool because both share credentials.
- One service account is reused by multiple agents, destroying attribution and widening privileges.
- Tool output containing instructions is re-consumed as trusted context.
- MCP/tool metadata is poisoned to influence selection or arguments.
- An agent can send data to arbitrary URLs or user-controlled destinations.
- Database/read tools expose more fields/rows than the task requires.
- A "read-only" agent can reach write endpoints indirectly through another agent.
- Secrets appear in prompts, traces, exception logs, or model telemetry.
- A model is used to make the final authorization decision without deterministic enforcement.
- Long-running agents retain stale privileges or sensitive session context.
