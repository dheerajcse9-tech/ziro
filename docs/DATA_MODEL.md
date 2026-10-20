# Data Model

Source: `backend/app/models/entities.py`, migration `backend/migrations/versions/0001_initial_schema.py`.
PostgreSQL schema verified identical to the models with `alembic check`.

```mermaid
erDiagram
  users ||--o{ consent_records : records
  students ||--o{ consent_records : has
  roster_imports ||--o{ students : "last import"
  employers ||--o{ drives : runs
  destinations ||--o{ drives : "uploads to"
  drives ||--o{ drive_criteria : "versioned criteria"
  drives ||--o{ drive_assignments : ""
  students ||--o{ drive_assignments : ""
  drives ||--o{ agent_runs : ""
  agent_runs ||--o{ agent_events : logs
  agent_runs ||--o{ eligibility_decisions : produces
  drives ||--o{ roster_proposals : ""
  roster_proposals ||--o{ approval_requests : ""
  approval_requests ||--o| upload_operations : authorises
  upload_operations ||--o{ verification_results : ""
  upload_operations ||--o{ attendance_sheets : ""
  agent_runs ||--o{ source_evidence : retrieves
  drives ||--o{ exception_items : ""
```

| Table | Purpose | Integrity |
|---|---|---|
| `users` | Accounts and role (admin/officer/approver/viewer) | unique email; PBKDF2 password hash |
| `students` | Roster; **roll_number is the business key**, name is never used to match | unique roll_number; CHECK cgpa 0–10, backlogs ≥ 0; `record_version` increments on change |
| `consent_records` | Append-only consent history per scope; latest wins | FK student; index (student, scope, id) |
| `roster_imports` | File hash, mapping, normalised preview rows, per-row issues, outcome counts | commit applies exactly the previewed rows |
| `employers`, `destinations` | Employer and allowlisted upload targets | unique employer name; destination `config_version` |
| `drives` | Title, UTC start/end, IANA timezone, status, destination, `schedule_version` | CHECK ends_at > starts_at |
| `drive_criteria` | Immutable criteria versions (min CGPA inclusive, backlog policy, branches, batches, consent scope, source text) | unique (drive, version) |
| `drive_assignments` | PROPOSED / CONFIRMED / HELD_CONFLICT / WITHDRAWN per student per drive | unique (student, drive) |
| `agent_runs` | Objective, planner mode, intent, status, plan, durable state incl. `next_node`, pending question | |
| `agent_events` | Every node transition and tool call with input/output and duration | unique (run, seq) |
| `eligibility_decisions` | Per-student decision with rule results, reasons, missing evidence, consent, conflicts, **criteria version and student record version** | older rows marked `is_stale` |
| `roster_proposals` | Included roll numbers, excluded/held with reasons, criteria & schedule versions, destination fingerprint, **proposal_hash** | |
| `approval_requests` | Status, bound hash, requester (agent run + user), decider, note, invalidation reason | |
| `upload_operations` | **operation_key** (unique), attempts, attempt log, destination receipt | unique operation_key |
| `verification_results` | Expected/observed counts, missing/unexpected/duplicate IDs, read-back snapshot, receipt hash | |
| `attendance_sheets` | Generated CSV, roll numbers, consent exclusions at generation, SHA-256, purge timestamp | |
| `source_evidence` | Retrieved employer form definition and parse result with content hash | |
| `exception_items` | Inbox items with dedupe key, severity, resolution and resolver | unique dedupe_key |
| `audit_events` | Append-only ledger: seq, actor, action, entity, operation id, run, drive, redacted details, prev_hash, hash | unique seq; PG trigger blocks UPDATE/DELETE |
| `test_runs` | Test Lab results: fixture, expected, actual, checks, evidence | |
| `app_settings` | Configurable institutional policy | |
| `mock_portal_*` | Owned by the LOCAL MOCK portal only; core code never touches them | |

## Traceability chain
`upload_operation.operation_key` → `approval_request` (approver, time, bound hash) → `roster_proposal`
(criteria version, schedule version, destination fingerprint, included IDs + record versions) →
`eligibility_decisions` (per-rule results for that criteria version) → `drive_criteria` row (with employer source
text) and `students`/`consent_records` → `roster_import` (file SHA-256). Every step is also an `audit_events` entry.

## Proposal hash
`sha256(canonical_json({drive_id, criteria_version, schedule_version, destination_fingerprint,
included: [[roll_number, record_version], ...]}))`. Any change to criteria, schedule, destination configuration, a
student's record, consent, or clash state that changes the included set or versions produces a different hash.
