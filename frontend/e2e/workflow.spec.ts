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
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
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
    await page.getByRole("link", { name: "Agent Control Room" }).click();
    await page.getByLabel("Objective").fill("Prepare the eligible roster for the upcoming drive, identify unresolved records, and show me the proposal for approval.");
    await page.getByRole("button", { name: "Run agent" }).click();
    await expect(page.getByText("Waiting for human approval").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("create_approval_request").first()).toBeVisible();
    await shot(page, "02-agent-run-awaiting-approval");
    // Officers cannot approve: the Approval Center shows the approver-only notice.
    await page.getByRole("link", { name: "Open in Approval Center" }).click();
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
    await page.getByRole("button", { name: "Approve and execute" }).click();
    await expect(page.getByText("Roster uploaded and independently verified").first()).toBeVisible({ timeout: 30_000 });
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
    await page.getByRole("link", { name: "Open in Approval Center" }).click();
    await page.waitForURL(/\/approvals\/\d+$/);
    supplementUrl = page.url();
    await login(page, "approver@placementos.demo");
    await page.goto(supplementUrl);
    await expect(page.getByText(/Supplementary upload: only 1 new student/).first()).toBeVisible();
    await shot(page, "16-supplement-approval");
    await page.getByRole("button", { name: "Approve upload" }).click();
    await page.getByLabel("I reviewed the roster").check();
    await page.getByRole("button", { name: "Approve and execute" }).click();
    await expect(page.getByText("Roster uploaded and independently verified").first()).toBeVisible({ timeout: 30_000 });
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
    await expect(page.getByText(/Exception resolved/).first()).toBeVisible();
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
    await page.getByRole("tab", { name: /Eligibility/ }).click();
    await expect(page.getByText("Eligibility — live evaluation").first()).toBeVisible();
    await shot(page, "10-drive-eligibility");
    await page.getByRole("tab", { name: "What-if simulator" }).click();
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
    await expect(page.getByText("4/4 scenarios passed").first()).toBeVisible({ timeout: 60_000 });
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
    await student.getByRole("button", { name: /Accept/ }).click();
    await expect(student.getByText("You are registered")).toBeVisible();
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
    await student2.close();
    await page.goto("/exceptions?kind=DATA_SUBMITTED");
    await page.getByRole("button", { name: "Resolve" }).first().click();
    await page.getByRole("button", { name: /Verify & apply/ }).click();
    await expect(page.getByText(/Verified details applied/).first()).toBeVisible();

    // Selection result on the verified Northwind roster.
    await page.goto("/drives/1");
    await page.getByRole("tab", { name: "Results" }).click();
    await page.getByLabel("Outcome for 21AD001").selectOption("SELECTED");
    await page.getByRole("button", { name: "Save results" }).click();
    await expect(page.getByText(/1 result\(s\) saved/).first()).toBeVisible();
    await page.goto("/students?q=21AD001");
    await expect(page.getByText("Northwind Systems").first()).toBeVisible();
    await page.getByRole("link", { name: "21AD001" }).click();
    await expect(page.getByText("Placement history")).toBeVisible();
    await shot(page, "19-student-placement-history");
  });

  test("viewer cannot act", async ({ page }) => {
    await login(page, "viewer@placementos.demo");
    await page.goto("/agent");
    await expect(page.getByText("Your role can view runs but not start them.").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Run agent" })).toBeDisabled();
    await page.goto("/settings");
    await shot(page, "15-settings-viewer");
  });
});
