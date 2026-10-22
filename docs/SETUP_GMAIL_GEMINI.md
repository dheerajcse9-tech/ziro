# Setup: Gmail (Google sign-in) and Gemini

This guide connects:

* **The placement mailbox** (for example `driveuploadtesting@gmail.com`). The Mailbox Agent reads company emails from it, and every email PlaceCrew sends goes out from it.
* **Gemini.** The Email Reader Agent and the Goal Planner Agent use it. Everything works without Gemini, but then emails are read by fixed rules only.

You need about 20 minutes. Everything happens in your browser, in `backend/.env`, and in two terminals.

> Google renames its console pages from time to time. If a button has a slightly different name, look for the closest match. The steps stay the same.

---

## Part 1: Gemini API key (5 minutes)

1. Open **https://aistudio.google.com/apikey** and sign in with any Google account.
2. Click **Create API key** (choose or create a project if asked) and copy the key.
3. Open `backend/.env` in VS Code and add:
   ```
   GEMINI_API_KEY=paste-your-key-here
   GEMINI_MODEL=gemini-3.8-flash
   ```
   Save with **Cmd + S**.
4. Restart the backend (Ctrl + C, then `python -m uvicorn app.main:app --reload --port 8000`).
5. Check: **Settings → Application health → AI model** shows `Gemini · gemini-3.8-flash`.
   The **Agent Crew** page shows "AI: Gemini".

**If emails show "AI failed → rules":** open the agent run. The red "LLM error" line has Google's message.

* "model … not found": in AI Studio, pick a model your key can use and put its exact name in `GEMINI_MODEL`.
* "API key not valid": copy the key again.

Nothing breaks while this is wrong: the email is still read by the rules and the run is labelled.

---

## Part 2: Google sign-in for the placement mailbox (15 minutes)

### 2.1 Create a Google Cloud project and turn on the Gmail API
1. Open **https://console.cloud.google.com** and sign in. You can use your own account; it does not have to be the mailbox account.
2. At the top, open the project picker and choose **New project**. Name it `PlaceCrew` and click **Create**, then select it.
3. Search for **Gmail API** in the top search bar, open it, and click **Enable**.

### 2.2 Configure the consent screen
1. Go to **APIs & Services → OAuth consent screen**. It may also be called **Google Auth Platform**.
2. Fill in **App name** `PlaceCrew`, a **support email**, and a **developer contact email**.
3. For **Audience / User type** choose **External**. Leave the publishing status as **Testing**.
4. Under **Test users**, add **`driveuploadtesting@gmail.com`**, the mailbox you will connect.
5. Save.

### 2.3 Create the OAuth client
1. Go to **APIs & Services → Credentials**, or **Google Auth Platform → Clients**.
2. Click **Create credentials → OAuth client ID** (or **Create client**).
3. **Application type: Web application.** Name: `PlaceCrew local`.
4. Under **Authorised redirect URIs**, add exactly this, with no trailing slash:
   ```
   http://localhost:3000/api/integrations/google/callback
   ```
5. Click **Create** and copy the **Client ID** and **Client secret**.

### 2.4 Put them in `backend/.env`
```
GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
INBOX_SUBJECT_FILTER=Campus Drive
```
Save, then restart the backend.

* You can delete the old `SMTP_...` lines. Once Gmail is connected, PlaceCrew sends through Gmail and does not use SMTP.
* `INBOX_SUBJECT_FILTER` means only emails whose subject contains "Campus Drive" are read. To read every email in a mailbox that only receives company emails, set it to empty: `INBOX_SUBJECT_FILTER=`.

### 2.5 Connect the mailbox
1. Run `git pull`, then `python -m alembic upgrade head` (once, for the new tables).
2. Go to **Settings → Demo reset** (as admin) so the new **Drive upload officer** account exists.
3. Sign in as **`drives@placementos.demo`** (password `Demo@2026`).
4. Go to **Settings → Placement mailbox (Gmail)** and click **Connect Gmail**.
5. Choose **driveuploadtesting@gmail.com**.
6. Google says **"Google hasn't verified this app"**. That is expected for an app in Testing mode with you as a test user. Click **Continue**.
7. Allow both permissions: **read email** and **send email**.
8. You return to Settings and it shows **Connected: driveuploadtesting@gmail.com**.

**What PlaceCrew can and cannot do with the mailbox:**

* It reads emails (read-only).
* It never marks emails as read, moves them or deletes them.
* It sends the emails you see in **Communications**.
* The refresh token is stored encrypted in the database.

**Every 7 days:** Google expires sign-ins for apps in "Testing" mode after 7 days. When that happens:

* Settings shows "Gmail needs to be reconnected".
* Emails fail with a reconnect message. You can resend them with **Communications → Retry failed emails**.

Click **Connect Gmail** again to fix it. Removing the 7-day limit requires publishing the app and passing Google's verification, which is not needed for testing.

---

## Part 3: Trusted company senders

PlaceCrew treats a company it doesn't know, writing from a personal Gmail address, as **"confirm before students are contacted"**. Anyone can send an email that claims to be a company.

Your test companies write from `companytestmailone@gmail.com`, `companytestmailtwo@gmail.com` and `companytestmailthree@gmail.com`. Add those addresses under **Settings → Trusted company senders** (as the drive upload officer or admin), one per line, and click **Save**. Their emails then go straight through. Every other check still applies: dates, deadline, criteria, duplicates and suspicious text.

If you don't add them, the Company Inbox shows **Needs you** with a **"Details are correct — create the drive and send consent forms"** button. After you confirm once, that address becomes the company's registered contact and later emails from it are recognised.

---

## Part 4: Test it

1. From `companytestmailone@gmail.com`, send an email to `driveuploadtesting@gmail.com` with a subject like **`Campus Drive – Acme Labs – Software Engineer`** and a body like:
   ```
   Company: Acme Labs
   Role: Software Engineer
   Drive date: 20 November 2026
   Time: 10:00 AM to 1:00 PM
   Venue: Main Auditorium
   Eligibility: Minimum CGPA 7.5, no active backlogs.
   Branches eligible: CSE, IT
   Registration deadline: 12 November 2026, 5:00 PM
   Please share the list of eligible students by 14 November 2026.
   ```
   With Gemini on, free-form wording also works.
2. Within 2 minutes (autopilot), or immediately with **Company Inbox → Check mailbox now**, the email appears. The agents read it, check it, create the drive, prepare its consent form and email it to every student.
3. To see a student's view, change one or two students' **Email** (Student Registry → student → Edit record) to inboxes you can open. Then use the drive's **Consent form** tab → **Send consent form** to reach them.

## Settings reference

| Setting | Where | Meaning |
|---|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | `backend/.env` | AI for reading emails and goals |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | `backend/.env` | Google sign-in for the mailbox |
| `INBOX_SUBJECT_FILTER` | `backend/.env` | Only matching subjects are read (default "Campus Drive") |
| `AUTOPILOT_INTERVAL_SECONDS` | `backend/.env` | How often the background agents run (default 120) |
| Trusted company senders | Settings | Skip the new-company confirmation |
| Response Reader applies answers automatically | Settings (admin) | When off, an officer verifies each student answer |
| Reminder timing | Settings (admin) | Hours before a form closes / before the drive |
