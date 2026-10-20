# Test Plan

| Level | Tooling | Location | Runs against |
|---|---|---|---|
| Unit | pytest | `backend/tests/unit/test_domain.py` | Pure domain functions |
| Reliability / integration | pytest + `Sandbox` | `backend/tests/integration/test_reliability.py` | Real services, agent graph and HTTP adapter → in-process LOCAL MOCK portal with its own DB |
| Official scenarios | pytest + Test Lab runner | `backend/tests/integration/test_official_scenarios.py`, `app/services/test_lab.py` | Same sandbox; full pipeline to destination read-back |
| Live API | pytest + uvicorn thread | `backend/tests/integration/test_api_live.py` | Real HTTP server, mounted mock portal, SQLite **and** PostgreSQL |
| End-to-end UI | Playwright (Chromium) | `frontend/e2e/workflow.spec.ts` | `next start` + uvicorn, after an API demo reset |
| Schema | `alembic check` | — | PostgreSQL 16 |

## Official scenarios
| ID | Fixture | Key checks |
|---|---|---|
| TC1 CGPA & backlogs | 8 students: 6.99/0, 7.00/0, 8.50/0, 8.10/1, 9.00/3, missing CGPA, missing backlogs, 6.50/2; criteria CGPA ≥ 7.0, 0 backlogs | per-student decisions; 7.00 passes (inclusive); reasons; UNKNOWN with missing evidence; uploaded IDs = {TC1-B, TC1-C}; destination read-back equal; run VERIFIED |
| TC2 Duplicate names | CSV import with three "Rahul Kumar" rows (distinct roll numbers), then a correction re-import for one of them | three distinct records; correction updates only that roll number; only 21CS102 eligible, uploaded, observed at destination and on the attendance sheet |
| TC3 Missing consent | eligible students with GRANTED, no record, REVOKED; phase B revokes consent after approval | non-consented students excluded from payload, destination and attendance; phase B upload blocked, approval INVALIDATED, destination empty |
| TC4 Conflicting drives | Drive A 10:00–13:00 IST, B 11:30–14:00 IST, C 13:00–15:00 IST, D 05:00–06:00 UTC; student eligible for A and B | agent prepares A then B: student HELD on B; SCHEDULE_CONFLICT exception exists while B awaits approval; overlapping pairs exactly {A-B, A-D, B-C} (half-open + timezone); B upload excludes held student |

## Reliability cases (all automated)
Invalid import rows · duplicate roll numbers in file · re-import idempotency · XLSX import · unauthorized approval
(officer, viewer) · no approve tool for agent · stale approval after criteria change (+ recovery by re-run) · revoked
consent invalidates pending approval · destination change after approval blocks upload · disallowed destination host ·
duplicate execution returns same operation · timeout after commit reconciled without duplicate · 503 retried safely ·
partial upload / extra record / duplicate record → FAILED_VERIFICATION and no attendance sheet · unreadable read-back →
PENDING then recovered · attendance contents & data minimisation · ambiguous goal → clarification · criteria mismatch
stop · prompt injection flagged not obeyed · what-if writes nothing · conflict resolution invalidates other proposal ·
LLM planner tool permissions (scripted client) · LLM failure fallback labelled · viewer cannot start runs · audit chain
valid, ORM edit blocked, out-of-band edit detected.

## What is *not* tested
- The live Anthropic API (no key available in the build environment).
- The organiser's official mock form (not provided).
- `docker compose` (no Docker daemon in the build environment).
- Cross-browser UI (Chromium only); formal accessibility audit.
