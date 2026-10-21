# Test Evidence

All results below were produced by actually running the commands on 2026-10-20 in the build environment
(Linux, Python 3.13, Node 22, PostgreSQL 16, Chromium 1194). Raw logs are in `docs/evidence/`.

## Summary
| Suite | Result | Log |
|---|---|---|
| Backend: unit + reliability + official scenarios + live HTTP (SQLite) | **64 passed, 0 failed** | [pytest-sqlite.txt](evidence/pytest-sqlite.txt) |
| Live HTTP API tests against **PostgreSQL 16** | **4 passed** | [pytest-postgres-live-api.txt](evidence/pytest-postgres-live-api.txt) |
| Official scenarios via CLI on PostgreSQL + `alembic check` | **TC1 9/9, TC2 10/10, TC3 10/10, TC4 10/10**; no schema drift | [testlab-postgres.txt](evidence/testlab-postgres.txt) |
| Browser end-to-end (Playwright, Chromium) | **9 passed** | [e2e-playwright.txt](evidence/e2e-playwright.txt) |
| Frontend `tsc --noEmit` + `next build` | clean, 19 routes | — |

Environment label for scenario runs (stored with every result): *Isolated sandbox: in-memory SQLite + in-process LOCAL
MOCK portal over the HTTP form adapter; deterministic planner; no external service contacted.* The Test Lab records with
expected/actual/checks/evidence are exported in [testlab-latest-runs.json](evidence/testlab-latest-runs.json).

## Official scenarios — expected vs actual
| Test | Expected | Actual (from latest recorded run) | Result |
|---|---|---|---|
| TC1 | eligible = {TC1-B (7.00), TC1-C (8.50)}; TC1-F, TC1-G UNKNOWN; others INELIGIBLE with reasons | identical decisions; uploaded and observed at destination = {TC1-B, TC1-C}; run VERIFIED | PASS |
| TC2 | only 21CS102 (CSE, 8.20, 0 backlogs) among three "Rahul Kumar" records | 3 distinct records; correction re-import changed only 21CS103; upload, destination and attendance = {21CS102} | PASS |
| TC3 | TC3-NONE and TC3-REV excluded everywhere; revocation after approval blocks upload | excluded from payload/destination/attendance with consent reasons; phase B: `Upload blocked: consent re-check failed`, approval INVALIDATED, destination empty | PASS |
| TC4 | clash flagged before approval; held student not uploaded; overlaps {A-B, A-D, B-C} | TC4-BOTH HELD on B with overlap window; SCHEDULE_CONFLICT exception while B awaited approval; pairs = {A-B, A-D, B-C}; B uploaded {TC4-BONLY} | PASS |

**Non-vacuity check:** changing `observed >= threshold` to `>` in the eligibility engine made TC1 fail 4 checks
(decisions, uploaded IDs, inclusive check, read-back) while TC2–TC4 still passed; the change was reverted.

## Bugs found by the tests and fixed
| Found by | Problem | Fix |
|---|---|---|
| Live API test on PostgreSQL | `agent_events.status` VARCHAR(20) too short for `INTERPRETING_CRITERIA` (SQLite ignores lengths) | widened to 40 |
| Test Lab on PostgreSQL | `test_runs.environment` VARCHAR(120) too short | changed to TEXT |
| Reliability test | expectation wrong, not code: after raising criteria, the employer's text still said 7.0, so the agent correctly stopped on a criteria mismatch | test updated to answer `use_configured` |
| E2E | resolving a clash in favour of the later drive was refused because the student was already *confirmed* on the uploaded roster — correct behaviour | test asserts the refusal, then resolves the other way |

## Database-level audit immutability (PostgreSQL)
```
placementos_test=# UPDATE audit_events SET action='FORGED' WHERE seq=1;
ERROR:  audit_events is append-only
CONTEXT:  PL/pgSQL function audit_events_append_only() line 3 at RAISE
```

## Real upload against the LOCAL MOCK portal (from the e2e run)
[upload-receipt-local-mock.json](evidence/upload-receipt-local-mock.json): drive *Northwind Systems — Software Engineer*,
14 uploaded / 20 excluded / 0 held, verification **VERIFIED** (14 observed / 14 approved, no missing, unexpected or
duplicate IDs), destination receipt number issued by the mock portal, approver and timestamps included.
This is a **local mock** destination served by the same backend — not an external service.

## Screenshots (captured by the e2e run)
| | |
|---|---|
| Overview | ![](evidence/screenshots/01-overview.png) |
| Agent run awaiting approval | ![](evidence/screenshots/02-agent-run-awaiting-approval.png) |
| Officer cannot approve | ![](evidence/screenshots/03-approval-officer-view.png) |
| Approver confirmation | ![](evidence/screenshots/04-approval-confirm.png) |
| Run verified, full tool timeline | ![](evidence/screenshots/05-agent-run-verified.png) |
| Verification & receipt | ![](evidence/screenshots/06-verification-receipt.png) |
| Clash flagged | ![](evidence/screenshots/07-conflicts.png) |
| Criteria mismatch stop | ![](evidence/screenshots/08-criteria-mismatch.png) |
| Duplicate names by roll number | ![](evidence/screenshots/09-students-rahul.png) |
| Eligibility with per-rule evidence | ![](evidence/screenshots/10-drive-eligibility.png) |
| What-if simulator | ![](evidence/screenshots/11-what-if.png) |
| Exception inbox | ![](evidence/screenshots/12-exceptions.png) |
| Audit chain verified | ![](evidence/screenshots/13-audit.png) |
| Test Lab | ![](evidence/screenshots/14-test-lab.png) |
| Viewer (read-only) | ![](evidence/screenshots/15-settings-viewer.png) |
| Supplementary upload (late student only) | ![](evidence/screenshots/16-supplement-approval.png) |
| Student accepts invitation (public link) | ![](evidence/screenshots/17-student-invitation-accepted.png) |
| Communications (local mailbox) | ![](evidence/screenshots/18-communications.png) |
| Student placement history | ![](evidence/screenshots/19-student-placement-history.png) |

## How to reproduce
```bash
cd backend && python -m pytest -o addopts="" -v
DATABASE_URL=postgresql://... alembic upgrade head && DATABASE_URL=postgresql://... python -m app.cli run-tests
# with backend on :8000 and `next start` on :3000:
cd frontend && CHROMIUM_PATH=/path/to/chrome SCREENSHOT_DIR=../docs/evidence/screenshots npx playwright test
```
