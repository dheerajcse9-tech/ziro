# PlaceCrew: guide for hackathon evaluators

PlaceCrew runs a college's placement drives with a crew of AI agents, from a company's email to the verified student
list sent back to the company. People stay in charge: agents do the routine work, and only the Approver can approve.

## Fastest way: the guided walkthrough (about 5 minutes, no login, 11 steps)

1. Open the deployed link. On the login page, click **"Hackathon evaluators — start here: Guided walkthrough"**
   (or go to `/tour`).
2. Click **Start the walkthrough**, then keep clicking **Yes, continue**.

Each click runs the next step **on the real code** (the same agents, checks, approvals, upload and verification the
app uses) in a private sandbox created for that click. The sandbox is an in-memory database plus the LOCAL MOCK company
form. Nothing touches the institute's data and **no real email is sent**: every email the agents write is shown on
screen. Each step lists its **checks**, read back from the sandbox database, with ✓ or ✗.

| Step | What happens | Who acts |
|---|---|---|
| 1 | The institute: 16 test students (one per test case) and 4 staff roles | — |
| 2 | Nimbus Softech and Orion Analytics email the placement mailbox. The Stage 1 agents read the emails, create the drives and email every student a consent form. Orion overlaps Nimbus, and the agents flag it. | Stage 1 agents |
| 3 | Four problem emails are stopped: the same email twice, no deadline, deadline after the drive, and an unknown sender saying "skip the consent step" | Criteria Check Agent |
| 4 | Students answer YES or NO (some don't answer) | Students |
| 5 | The placement officer runs both drives. Agents apply CGPA, backlogs, branch and batch, keep only YES answers, hold the student booked on both drives, and prepare the list | Stage 2 agents |
| 6 | **Ask the crew:** the approver asks in plain language ("Why isn't Farah on the list?", "What if the CGPA were 6.5?", "Send personal forms"). The assistant agent picks its own read-only tools, answers with the real reasons, runs a what-if without changing anything, and only *proposes* the action | Ask PlaceCrew (Gemini + tools) |
| 7 | Before approving, the approver sends personal forms to the 3 students with missing details or no answer | Approver + Stage 3 agents |
| 8 | Two students reply. The old list is cancelled automatically and a new list adds them | Response Reader + Re-check agents |
| 9 | The admin and the officer are refused; the approver approves. The list is uploaded, read back and verified, then emailed to the company. Students get shortlisted / not-shortlisted emails with reasons | Approver → Upload & Employer Update agents |
| 10 | The schedule clash is settled by a person; Orion's list is sent | Officer → Approver |
| 11 | **All test scenarios (TC1–TC6) are run again** and their results shown | Proof |

## The test cases

- **TC1–TC4: the official hackathon scenarios**
  - TC1 CGPA threshold and backlogs
  - TC2 Duplicate names
  - TC3 Missing consent
  - TC4 Conflicting drives
- **TC5: company email cases**: valid, overlapping, duplicate, no deadline, deadline after the drive, unknown sender with instruction-like text.
- **TC6: one test student per case**:
  - says YES; says NO; no answer;
  - missing CGPA; missing backlogs;
  - CGPA 6.4; CGPA exactly 7.0; CGPA 6.99;
  - wrong branch; 2 backlogs; 1 backlog where 1 is allowed;
  - YES to two overlapping drives;
  - two students named Rahul Kumar;
  - wrong batch.

  The expected result for each is in [TEST_SCENARIOS.md](TEST_SCENARIOS.md).

The same scenarios can be re-run any time from **Test Lab** (admin login). Results are recorded as they come out, with
a downloadable evidence CSV.

## Exploring the app yourself (optional)

All staff passwords are `Demo@2026`. The login page explains each role, and you can click a role to fill in its login.

| Account | Role | Try |
|---|---|---|
| `driveuploadtesting@gmail.com` | Drive upload officer (step 1) | Company emails → **Load test emails (E1–E6)** → open each one |
| `officer@placementos.demo` | Placement officer (step 2) | Drives → open a drive → **Prepare the student list**; Drives → **New drive** |
| `approver@placementos.demo` | Approver (step 3) | Approvals → open the list → send personal forms → **Approve** |
| `admin@placementos.demo` | Admin (setup) | Test Lab → **Run all scenarios**; Agents; Audit log |

Every staff page has an **Ask PlaceCrew** button (bottom right): ask about any drive or student in your own words. With
a Gemini key it plans with function calling over read-only tools; actions it suggests need a click to confirm.

Student emails stay in **Emails sent**: open one and use its link to answer as that student. Every page where agents
worked shows the **agent chain**: one card per agent, arrows for each hand-over, and a person card where a human decides.

## What differs between the deployed link and a real installation

| On the deployed link | In a real installation |
|---|---|
| Emails are kept in the app (Emails sent), because students have made-up addresses | Connected to the placement Gmail (Google sign-in), emails really go out from the placement mailbox, and company emails are read from it automatically |
| The company's form is the clearly labelled **LOCAL MOCK** stand-in | The company's real form, or the list emailed to the company |
| AI (Gemini) is used when a key is configured; otherwise the built-in rules read emails and requests, labelled in each task | Same |

**Safety rules the walkthrough demonstrates:**
- Agents never approve. Only the Approver can approve; the admin and the officer are refused.
- An approval is bound to the exact list it shows. If anything changes, it is cancelled.
- Every upload is read back and checked student by student.
- Instruction-like text in a company email is treated as data, never obeyed.
- Every action is written to a hash-chained audit log.
