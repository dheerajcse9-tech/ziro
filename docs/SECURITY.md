# Security and Reliability

| Threat / failure | Control | Where |
|---|---|---|
| Unauthenticated access | JWT (HS256) in httpOnly SameSite=Lax cookie (Secure in production); Bearer also accepted for API clients | `api/deps.py`, `core/security.py` |
| Privilege misuse | Role checks on every mutating route; approve/reject require approver/admin; settings, fault injection, reset require admin | `api/routes_*.py`, `services/approvals.py` |
| Agent self-approval | No approval tool exists; approval needs a human JWT + approver role; LLM may only call 4 READ tools (others return TOOL_DENIED) | `agent/tools.py` `LLM_ALLOWED_TOOLS` |
| Approving something different from what was reviewed | Approver must submit the hash they reviewed; backend recomputes the live hash and refuses on mismatch | `approvals.approve` |
| Change after approval | Criteria/schedule/destination/student/consent/settings changes re-validate open approvals and invalidate mismatches; execution recomputes again | `revalidate_open_approvals`, `uploads.execute` |
| Sharing without consent | Ineligible-for-upload unless latest consent is GRANTED; re-checked immediately before upload and at attendance generation | `services/roster.py`, `uploads.py`, `attendance.py` |
| Upload to an unapproved host | `ALLOWED_DESTINATION_HOSTS` allowlist enforced when configuring and when resolving the adapter | `integrations/registry.py`, `drives.upsert_destination` |
| Duplicate submissions on retry | Deterministic operation key sent as client reference; reconcile-before-resubmit; unique DB constraint | `uploads.py` |
| "HTTP 200 = success" fallacy | Independent read-back with exact multiset comparison; VERIFIED only if nothing missing/unexpected/duplicated and receipt count matches | `verification.py` |
| Prompt injection in retrieved documents | Retrieved text is labelled untrusted and parsed by a closed-pattern deterministic parser; instruction-like phrases flagged as exceptions and ignored; LLM system prompt treats tool output as data | `domain/criteria_parser.py`, `agent/graph.py` |
| LLM inventing drives | Planner output validated: unknown drive id → clarification question | `planner._plan_from_llm` |
| Secret exposure | Secrets only in server env; frontend proxies `/api`; `/api/integrations` reports mode, never keys; `.env*` git-ignored | `next.config.ts`, `core/config.py` |
| PII in logs/evidence | Emails, phones, passwords, tokens redacted in audit details and agent events; attendance sheets use configured minimal fields; LLM never receives student rows | `core/redaction.py` |
| Tampering with history | ORM flush guard blocks UPDATE/DELETE of audit rows; PostgreSQL trigger blocks them in SQL; hash chain detects out-of-band edits (`/api/audit/verify`) | `audit/ledger.py`, migration |
| Data retention | Attendance content purged after configurable days (metadata + hash retained) | `attendance.purge_expired` |
| Demo-only dangerous actions | Demo reset & fault injection refused unless `DEMO_MODE=true` and admin | `routes_ops.py` |
| Production misconfiguration | `APP_ENV=production` refuses to start without `JWT_SECRET` | `core/config.py` |
| OTP / CAPTCHA | Not bypassed; adapter only performs plain form submission. A destination that requires them must use a manual step | design |

## Known gaps (honest list)
- No rate limiting or account lockout on login.
- No CSRF token; mitigated by SameSite=Lax cookies and JSON-only mutating endpoints (except the multipart import upload, which also requires the session cookie).
- Single-institution; no per-tenant isolation.
- Demo accounts share a known password and are only shown when `DEMO_MODE=true`.
