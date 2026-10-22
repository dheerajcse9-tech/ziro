import { expect, type Page, test } from "@playwright/test";

const SHOTS = process.env.SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@2026");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /^Hello/ })).toBeVisible();
}

test.describe.serial("PlacementOS end-to-end", () => {
  let approvalUrl = "";
  let supplementUrl = "";

  test.beforeAll(async ({ request }) => {
    // Start from the synthetic seed so the run is reproducible.
    await request.post("/api/auth/login", { data: { email: "admin@placementos.demo", password: "Demo@2026" } });
    const r = await request.post("/api/admin/demo-reset");
    expect(r.ok()).toBeTruthy();
  });

  test("officer delegates roster preparation to the agent", async ({ page }) => {
    await login(page, "officer@placementos.demo");
    await shot(page, "01-overview");
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Prepare the eligible roster for the upcoming drive, identify unresolved records, and show me the proposal for approval.");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText("Waiting for human approval").first()).toBeVisible({ timeout: 30_000 });
    await page.getByLabel("Show technical details").check();
    await expect(page.getByText("create_approval_request").first()).toBeVisible();
    await shot(page, "02-agent-run-awaiting-approval");
    // Officers cannot approve: the approval page shows the approver-only notice.
    await page.getByRole("link", { name: "Open approval" }).click();
    await expect(page.getByText("Approver action required").first()).toBeVisible();
    approvalUrl = page.url();
    await shot(page, "03-approval-officer-view");
  });

  test("approver approves exact proposal; upload is verified by read-back", async ({ page }) => {
    await login(page, "approver@placementos.demo");
    await page.goto(approvalUrl);
    await page.getByRole("button", { name: "Approve upload" }).click();
    await page.getByLabel("I reviewed the roster").check();
    await shot(page, "04-approval-confirm");
    await page.getByRole("button", { name: "Approve and send" }).click();
    await expect(page.getByText("Roster uploaded and independently verified").first()).toBeVisible({ timeout: 30_000 });
    await page.getByLabel("Show technical details").check();
    await expect(page.getByText("verify_uploaded_roster").first()).toBeVisible();
    await shot(page, "05-agent-run-verified");
    await page.getByRole("link", { name: /Receipt/ }).click();
    await expect(page.getByText(/no missing, unexpected or duplicate records/).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "CSV" })).toBeVisible();
    await shot(page, "06-verification-receipt");
  });

  test("late student with missing CGPA is sent later as a supplement", async ({ page }) => {
    await login(page, "officer@placementos.demo");
    await page.goto("/students?q=21CS008");
    await page.getByRole("link", { name: "21CS008" }).click();
    await page.getByRole("button", { name: "Edit record" }).click();
    await page.getByLabel("CGPA").fill("8.4");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText(/Updated cgpa/).first()).toBeVisible();
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Prepare the eligible roster for the Northwind drive");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText("Waiting for human approval").first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole("link", { name: "Open approval" }).click();
    await page.waitForURL(/\/approvals\/\d+$/);
    supplementUrl = page.url();
    await login(page, "approver@placementos.demo");
    await page.goto(supplementUrl);
    await expect(page.getByText(/Supplementary upload: only 1 new student/).first()).toBeVisible();
    await shot(page, "16-supplement-approval");
    await page.getByRole("button", { name: "Approve upload" }).click();
    await page.getByLabel("I reviewed the roster").check();
    await page.getByRole("button", { name: "Approve and send" }).click();
    await expect(page.getByText("Roster uploaded and independently verified").first()).toBeVisible({ timeout: 30_000 });
    await page.getByLabel("Show technical details").check();
    await expect(page.getByText(/verification VERIFIED \(15\/15\)/).first()).toBeVisible();
  });

  test("clash on overlapping drive is flagged before approval", async ({ page }) => {
    await login(page, "officer@placementos.demo");
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Prepare the roster for Helios Analytics and flag any schedule clashes.");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText("Waiting for human approval").first()).toBeVisible({ timeout: 30_000 });
    await page.goto("/conflicts");
    await expect(page.getByRole("heading", { name: /Open student clashes \([1-9]/ })).toBeVisible();
    await shot(page, "07-conflicts");
    await page.getByRole("button", { name: "Resolve" }).first().click();
    // Northwind's roster was already uploaded, so moving the student away from it is refused.
    await page.getByRole("button", { name: /Helios Analytics/ }).first().click();
    await page.getByRole("button", { name: "Assign to selected drive" }).click();
    await expect(page.getByText(/already confirmed on the other drive/).first()).toBeVisible();
    await page.getByRole("button", { name: /Northwind Systems/ }).first().click();
    await page.getByRole("button", { name: "Assign to selected drive" }).click();
    await expect(page.getByText(/Clash resolved/).first()).toBeVisible();
  });

  test("criteria mismatch stops the agent for an officer decision", async ({ page }) => {
    await login(page, "officer@placementos.demo");
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Prepare the Crestline roster");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText("Agent needs a decision").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Differs from configured criteria").first()).toBeVisible();
    await shot(page, "08-criteria-mismatch");
  });

  test("records pages render real data", async ({ page }) => {
    await login(page, "officer@placementos.demo");
    await page.goto("/students?q=Rahul");
    await expect(page.getByRole("cell", { name: "21CS002" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "21ME001" })).toBeVisible();
    await shot(page, "09-students-rahul");
    await page.goto("/drives/1");
    await page.getByRole("tab", { name: "Who qualifies" }).click();
    await expect(page.getByText(/criteria applied to all/).first()).toBeVisible();
    await shot(page, "10-drive-eligibility");
    await page.getByRole("tab", { name: "Criteria" }).click();
    await page.getByLabel("Minimum CGPA").fill("6.5");
    await page.getByRole("button", { name: "Simulate" }).click();
    await expect(page.getByText("Simulation only").first()).toBeVisible();
    await shot(page, "11-what-if");
    await page.goto("/exceptions");
    await shot(page, "12-exceptions");
    await page.goto("/audit");
    await page.getByRole("button", { name: "Verify chain integrity" }).click();
    await expect(page.getByText(/Hash chain intact/).first()).toBeVisible();
    await shot(page, "13-audit");
  });

  test("test lab runs official scenarios against real code", async ({ page }) => {
    await login(page, "officer@placementos.demo");
    await page.goto("/test-lab");
    await page.getByRole("button", { name: "Run all scenarios" }).click();
    await expect(page.getByText("6/6 scenarios passed").first()).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Details" }).first().click();
    await shot(page, "14-test-lab");
  });

  test("outreach: invitation accepted via emailed link, missing data supplied and verified, result recorded", async ({ page, context }) => {
    await login(page, "officer@placementos.demo");
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Invite eligible students for the Bluepeak drive.");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText(/Invitations emailed to \d+ eligible student/).first()).toBeVisible({ timeout: 30_000 });

    // Act as the student: open the invitation from the local mailbox and accept.
    await page.goto("/communications");
    await page.getByLabel("Category").selectOption("DRIVE_INVITATION");
    await page.getByRole("button", { name: "Open" }).first().click();
    const link = await page.getByRole("link", { name: /\/respond\// }).getAttribute("href");
    const student = await context.newPage();
    await student.goto(link!.replace(/^https?:\/\/[^/]+/, ""));
    await student.getByRole("button", { name: /Yes, register me/ }).click();
    await expect(student.getByText("You said YES")).toBeVisible();
    await shot(student, "17-student-invitation-accepted");
    await student.close();

    // Missing data: request by email, student answers, officer verifies.
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Email students with missing data for the Northwind drive.");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText(/Missing-data requests emailed to [1-9]/).first()).toBeVisible({ timeout: 30_000 });
    await page.goto("/communications");
    await page.getByLabel("Category").selectOption("DATA_REQUEST");
    await page.getByRole("button", { name: "Open" }).first().click();
    const dataLink = await page.getByRole("link", { name: /\/respond\// }).getAttribute("href");
    await shot(page, "18-communications");
    const student2 = await context.newPage();
    await student2.goto(dataLink!.replace(/^https?:\/\/[^/]+/, ""));
    await expect(student2.getByRole("button", { name: "Submit details" })).toBeVisible();
    const input = student2.getByRole("textbox").first();
    if (await input.count()) await input.fill("0");
    const select = student2.getByRole("combobox");
    if (await select.count()) await select.selectOption("CSE");
    await student2.getByRole("button", { name: "Submit details" }).click();
    await expect(student2.getByText(/your details were received/)).toBeVisible();
    // The Response Reader Agent applied the answer at once (marked self-reported).
    await expect(student2.getByText("Your placement record has been updated.")).toBeVisible();
    await student2.close();

    // Selection result on the verified Northwind roster.
    await page.goto("/drives/1");
    await page.getByRole("tab", { name: "Results" }).click();
    await page.getByLabel("Outcome for 21AD001").selectOption("SELECTED");
    await page.getByRole("button", { name: "Save results" }).click();
    await expect(page.getByText(/1 result\(s\) saved/).first()).toBeVisible();
    await page.goto("/students?q=21AD001");
    await expect(page.getByRole("table").getByText("Northwind Systems").first()).toBeVisible();
    await page.getByRole("link", { name: "21AD001" }).click();
    await expect(page.getByText("Placement history")).toBeVisible();
    await shot(page, "19-student-placement-history");
  });

  test("three stages: company email -> consent forms -> roster -> approver follow-up form -> re-proposal", async ({ page, context }) => {
    // Stage 1 — drive upload officer
    await login(page, "drives@placementos.demo");
    await page.goto("/inbox");
    await page.getByRole("button", { name: "Load sample emails" }).click();
    await expect(page.getByText(/3 sample email\(s\) added/).first()).toBeVisible();
    await page.getByRole("button", { name: "Check mailbox now" }).click();
    await expect(page.getByText("Consent forms sent").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Needs you").first()).toBeVisible();
    await shot(page, "20-company-inbox");
    await page.getByRole("row", { name: /Zentrix/ }).getByRole("button", { name: "Review" }).click();
    await expect(page.getByText("The agents need you")).toBeVisible();
    await expect(page.getByText(/instruction-like text, which was ignored/).first()).toBeVisible();
    await shot(page, "21-intake-review");
    await page.getByRole("button", { name: /Details are correct/ }).click();
    await expect(page.getByText(/Consent form sent to \d+ student/).first()).toBeVisible({ timeout: 30_000 });
    await page.keyboard.press("Escape");

    // A student answers the Orbitra consent form from the emailed link.
    // 3 drives x 34 students = 102 consent emails; find an Orbitra one through the outbox API.
    const outbox = await (await page.request.get("/api/emails?category=DRIVE_INVITATION&page_size=200")).json();
    // Aarav Sharma (21CS001, CSE, 8.72) meets Orbitra's criteria.
    const orbitra = outbox.items.find((m: { subject: string; to_address: string }) => m.subject.includes("Orbitra") && m.to_address.startsWith("21cs001"));
    const mail = await (await page.request.get(`/api/emails/${orbitra.id}`)).json();
    const link = mail.body_text.match(/https?:\/\/\S+\/respond\/\S+/)[0];
    const student = await context.newPage();
    await student.goto(link.replace(/^https?:\/\/[^/]+/, ""));
    await expect(student.getByText(/Consent form — Orbitra Technologies/)).toBeVisible();
    await shot(student, "22-consent-form");
    await student.getByRole("button", { name: /Yes, register me/ }).click();
    await expect(student.getByText("You said YES")).toBeVisible();
    await student.close();

    // Stage 2 — placement officer runs the drive; filters the registry for it
    await login(page, "officer@placementos.demo");
    await page.goto("/agent");
    await page.getByLabel("Objective").fill("Prepare the eligible roster for the Orbitra drive");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText("Waiting for human approval").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Consent Agent").first()).toBeVisible();
    await page.getByRole("link", { name: "Open approval" }).click();
    await page.waitForURL(/\/approvals\/\d+$/);
    const approval = page.url();

    // Stage 3 — approver: agent review, then personal forms for missing details / consent
    await login(page, "approver@placementos.demo");
    await page.goto(approval);
    await expect(page.getByText(/Approval Review Agent: ready/).first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: /Email \d+ student\(s\) a personal form/ }).click();
    await page.getByRole("button", { name: "Send personal forms" }).click();
    await expect(page.getByText(/Personal forms emailed to \d+ student/).first()).toBeVisible({ timeout: 30_000 });
    await shot(page, "23-approval-review-agent");
    await page.goto("/agents");
    await expect(page.getByText("Email Reader Agent")).toBeVisible();
    await shot(page, "24-agent-crew");
  });

  test("placement officer adds a drive with the New drive form", async ({ page }) => {
    const iso = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
    await login(page, "officer@placementos.demo");
    await page.goto("/drives");
    await page.getByRole("button", { name: "New drive" }).click();
    await page.getByLabel("Company", { exact: true }).fill("Acme Labs");
    await page.getByLabel(/Company email/).fill("hr@acme.example");
    await page.getByLabel("Role", { exact: true }).fill("Software Intern");
    await page.getByLabel("Drive date").fill(iso(30));
    await page.getByLabel("Students must answer by (date)").fill(iso(20));
    await page.getByLabel("Computer Science & Engineering").check();
    await page.getByRole("button", { name: "Create drive and send consent forms" }).click();
    await expect(page.getByRole("heading", { name: /Acme Labs — Software Intern/ })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/said YES/).first()).toBeVisible();
    await shot(page, "17-new-drive-by-officer");
  });

  test("viewer cannot act", async ({ page }) => {
    await login(page, "viewer@placementos.demo");
    await page.goto("/agent");
    await expect(page.getByText(/Running a drive is Step 2/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Run agent" })).toHaveCount(0);
    await page.goto("/settings");
    await shot(page, "15-settings-viewer");
  });
});
