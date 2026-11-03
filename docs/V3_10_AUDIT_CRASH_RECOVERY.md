# Alex Core v3.10 — Audit Tail Crash Recovery

## Change

The durable JSONL audit loader now distinguishes an interrupted final write from a damaged audit history. If and only if the final line is malformed and lacks a line terminator, Alex saves that exact tail to `<audit path>.partial`, truncates the live log to the preceding complete lines, and verifies the retained hash chain. Complete-looking final JSON records without a newline remain valid. Malformed newline-terminated records and hash-chain failures still trigger the existing `.corrupt` quarantine behavior.

The API health version and FastAPI title are updated to 3.10.

## Safety boundaries

- This is crash-tail recovery, not tamper recovery. It deliberately does not repair hash-chain mismatches or malformed complete records.
- Keep the audit path on reliable storage with restrictive filesystem permissions and backups.
- A single-process `RLock` does not protect concurrent writers in multiple processes.
- Hash chaining is not a keyed signature and cannot prevent a privileged attacker from rewriting the full log.

## Validation

Run `PYTHONPATH=. pytest -q` from the project root. Tests cover ordinary round trips, tamper quarantine, recursive secret redaction, failed durable writes, incomplete tail recovery, and strict handling of malformed terminated records.
