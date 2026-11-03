# v3.55 — Live role-based access matrix

This release makes the read permission for `GET /api/v1/missions/recovery-queue` explicit in the endpoint permission table. The queue is a read-only view and uses `missions:read`; the pending recovery-audit queue and recovery-decision operations remain protected by `missions:recover`. This documents the intended least-privilege boundary instead of relying on the generic mission-detail route pattern to happen to match the queue path.

Added `scripts/live_role_access_matrix_test.py`, which starts an isolated loopback-only server with disposable stores and test-only admin, operator, and reader keys. It checks missing/invalid-key rejection, common read-only inventories, mission creation denial for readers, operator draft creation, operational recovery queue boundaries, and admin-only readiness/approval/provider views. No mission is started and no external provider is contacted.

Run with:

```bash
python scripts/live_role_access_matrix_test.py
```

This validates API authorization against a real local HTTP server. It does not validate browser-to-server direct networking, TLS/reverse-proxy configuration, production deployment, or external integrations.
