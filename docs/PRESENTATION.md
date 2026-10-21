# Presentation & Demo

## Slide outline (8 slides)
1. **The problem** — a placement drive is 11 manual steps with silent failure modes: the wrong Rahul Kumar, students
   shared without consent, clashes found on the day, uploads that half-succeed, nobody can prove who approved what.
2. **PlacementOS in one sentence** — delegate the drive workflow to an agent, inspect its evidence, approve the exact
   operation, verify the result.
3. **Live architecture** — one coordinator agent, durable graph, typed tools with permission tiers; deterministic rules
   decide eligibility; humans approve; an adapter executes; the system re-reads the destination. (ARCHITECTURE diagram)
4. **Agentic capability** — natural-language goal → plan → real tool calls with persisted inputs/outputs → conditional
   branches (clarify, criteria mismatch, retrieval failure, clash hold) → approval interrupt → execution → verification
   → recovery paths. Everything on screen is read from the event log.
5. **Trust by construction** — proposal hash binds approval to roster + criteria + schedule + destination; any change
   invalidates it; consent re-checked at execution; idempotent operation key + reconciliation; exact-ID verification;
   hash-chained audit ledger; prompt-injection text flagged and ignored.
6. **Official test cases** — TC1–TC4 run live in the Test Lab against real code (not hard-coded); show expected vs actual.
7. **Measured results** — 59 backend tests, 4/4 official scenarios (39 checks), 8 browser e2e tests, verified on SQLite
   and PostgreSQL. *No time-saved figure is claimed: no baseline was measured.*
8. **Limitations & next steps** — organiser form not available (LOCAL MOCK used; adapter swap planned), LLM planner not
   exercised live in this build, public deployment pending a database, no SSO.

## 2–3 minute demo script
| Time | Screen | Action & narration |
|---|---|---|
| 0:00 | Login → Overview | "Synthetic institute, 34 students, 4 upcoming drives. Every number here is computed from the database." |
| 0:15 | Agent Control Room | As **officer**, type: *Prepare the eligible roster for the upcoming drive, identify unresolved records, and show me the proposal for approval.* Run. |
| 0:30 | Run view | Walk the plan and timeline: the agent picked Northwind (next upcoming), retrieved the employer's form, cross-checked criteria, called `evaluate_eligibility` (14 in / 20 out, 3 unknown, 2 eligible but no consent), checked clashes, and **stopped** for approval. Expand a tool output. |
| 0:55 | Approval Center | Officer sees "Approver action required" — the agent and officers cannot approve. Show exclusion reasons (CGPA 6.99, consent revoked, missing CGPA) and the proposal hash. |
| 1:10 | Approval (approver) | Sign in as **approver**, tick "I reviewed…", Approve. The run resumes: upload → read-back → **VERIFIED 14/14** → attendance sheet. |
| 1:30 | Verification Center | Show approved vs observed IDs, receipt, attempt log, download attendance CSV. Click "View destination roster" to show the LOCAL MOCK portal page. |
| 1:45 | Agent → Helios | Run *Prepare the roster for Helios Analytics*. 10 students **held**: Helios overlaps Northwind 11:30–13:00. Open Schedule Conflicts: the clash was flagged before assignment. |
| 2:05 | Agent → Crestline | Run *Prepare the Crestline roster*: the employer's form says CGPA 6.5 but the drive is configured at 6.0 → the agent stops and asks. |
| 2:20 | Settings → fault injection (optional) | Arm "drop last candidate" and re-run a drive: verification shows **FAILED_VERIFICATION** with the missing roll number — a 201 response is not proof. |
| 2:35 | Test Lab + Audit | Run all four official scenarios (4/4) and verify the audit hash chain. Close on the promise. |

Reset before recording: Settings → Demo reset (admin).

## Feature list (implemented)
Dashboard · Student Registry (search, filters, pagination, details, consent history, edit) · CSV/XLSX import with
mapping, preview, issues CSV, safe re-import · Employers & drives with timezone-aware schedules · Versioned criteria ·
Destinations with allowlist · Deterministic eligibility · Consent enforcement · Clash detection, holds and resolution ·
Agent Control Room (NL commands, plan, live timeline, evidence, questions) · Approval Center · Idempotent upload with
reconciliation · Verification Center · Receipts · Attendance CSV/XLSX · Exception Inbox · What-if simulator · Test Lab ·
Audit Explorer with chain verification · Settings (health, integrations, roles, policy, fault injection, demo reset,
retention purge).

## Known limitations
See [ACCEPTANCE_CRITERIA](ACCEPTANCE_CRITERIA.md) A22–A24 and [SECURITY](SECURITY.md) "Known gaps".
