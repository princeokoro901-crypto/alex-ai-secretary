# Alex v2.7 — Persistent Agent Memory & Scoped Context

Agents now receive persistent memory through an explicit scope filter. Memory is owned by the agent identity and filtered by authorized scope and maximum sensitivity.

Rules:
- Agents cannot read arbitrary Alex memory.
- Agents cannot request scopes outside their manifest.
- Agents cannot write outside their authorized memory scope.
- Restricted/sensitive memory requires an explicit sensitivity ceiling.
- Memory context is attached to structured AgentResult provenance.
- Memory access does not grant tools or action authority.

This is a development foundation; production requires encrypted durable storage, retention/deletion policies, tenant isolation, access logging, and stronger policy integration.
