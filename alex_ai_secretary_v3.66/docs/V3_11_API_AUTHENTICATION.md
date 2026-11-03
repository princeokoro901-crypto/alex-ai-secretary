# Alex Core v3.11 — Optional API-key authentication

## Changes
- Added opt-in Bearer API-key authentication for `/api/v1/*` endpoints.
- `/health` remains public for basic liveness checks.
- Key configuration is read from `ALEX_API_KEYS_JSON`, a JSON array of entries with `key`, `key_id`, `actor_id`, and `role`.
- Supported role labels are `admin`, `operator`, and `reader`; each authenticated request receives actor metadata in `request.state.actor`. `reader` is restricted to GET/HEAD/OPTIONS requests; `operator` and `admin` may use mutating methods.
- Key matching uses constant-time comparisons and rejects duplicate key IDs or malformed configuration.
- Invalid configured authentication returns 503 (fail closed); missing/invalid bearer keys return 401.

## Configure
Set `ALEX_API_KEYS_JSON` in the deployment environment, for example as a JSON array containing one or more unique key IDs. Use a secret manager in deployed environments. Never commit actual API keys to source control.

## Important limitations
- If `ALEX_API_KEYS_JSON` is unset or empty, Alex stays in local-development mode and API endpoints are not authenticated. Do not expose that mode to an untrusted network.
- Role enforcement is method-level only: `reader` cannot use mutating HTTP methods, while `operator` and `admin` can. Fine-grained endpoint permissions and separating administrative operations remain future work.
- Keys are configured through environment JSON; rotation/revocation workflows, durable per-request audit attribution, rate limiting, and secret-manager integration remain future work.
- Authentication does not replace TLS, network controls, or least-privilege deployment.
