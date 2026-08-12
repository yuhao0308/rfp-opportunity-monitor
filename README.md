# RFP Opportunity Monitor

A small daily CLI that watches public procurement portals, detects new or materially
changed listings, applies the Myers McRae relevance framework, and can send an email
digest.

The configured sources are North Dakota Buys, Maryland eMMA, and Alabama's official
professional-services RFP search. North Dakota and Maryland share a JAGGAER adapter;
Alabama uses its own small adapter for the state search form.

There are two ways in: `scan` crawls the public portals, and `email-scan` reads the
vendor-registration inbox for portals that notify by email. Both feed the same keyword
framework and the same SQLite state.

## What it does

- Scans every result page once per run.
- Normalizes title, ID, status, dates, category, agency, type, and link.
- Uses all 1,141 signals from the supplied keyword framework.
- Classifies results as `Strong opportunity`, `Possible opportunity`, or
  `Market intelligence`.
- Suppresses only records explicitly marked closed.
- Treats passed-due and response/award-stage matches as market intelligence.
- Stores seen records in SQLite so unchanged listings are not repeated.
- Establishes a silent baseline on the first successful scan of each source.
- Keeps one source failure from affecting the other source or prior state.
- Prints a digest by default and sends it only when `--send` is supplied.

## Install on Windows Server

Use 64-bit Windows Server 2019 or newer and Python 3.11+. Python 3.12 is recommended.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Playwright's Chromium cache is per Windows user by default. Install it as the same account that
will run the scheduled task. macOS/Linux developers can use the equivalent `.venv/bin/` commands.

## Configure

Edit [`config.toml`](config.toml):

- Add or disable `[[sources]]`.
- Set `adapter = "alabama-rfp"` only for Alabama; other sources default to `jaggaer`.
- Set `notifications.recipients`.
- Change the state path, browser mode, page limit, or timezone if needed.

The full versioned taxonomy is in
[`config/keywords.json`](config/keywords.json). It is grouped by the framework's
direct, supporting, context, leadership, lifecycle, market, exploratory, noise, and
NAICS signals.

For email delivery, copy the SMTP variable names from [`.env.example`](.env.example)
into the company scheduler's secret store or process environment. The CLI does not
load `.env` files or put credentials in SQLite.

## Set up daily email

1. Add the recipient in [`config.toml`](config.toml):

   ```toml
   recipients = ["recipient@example.com"]
   ```

2. Give the scheduler these SMTP settings:

   ```text
   SMTP_HOST=smtp.example.com
   SMTP_PORT=587
   SMTP_USERNAME=...
   SMTP_PASSWORD=...
   SMTP_FROM=rfp-monitor@example.com
   SMTP_STARTTLS=true
   ```

3. Save the current listings without emailing them:

   ```powershell
   .\.venv\Scripts\rfp-monitor.exe scan
   ```

4. Test email with a temporary database:

   ```powershell
   .\.venv\Scripts\rfp-monitor.exe scan `
     --source alabama `
     --state .\var\validation\rfp-email-test.sqlite3 `
     --include-baseline `
     --send
   ```

5. Schedule this command to run each morning:

   ```powershell
   Set-Location C:\CompanyApps\rfp-opportunity-monitor
   .\.venv\Scripts\rfp-monitor.exe scan --source alabama --send
   ```

The Windows Task Scheduler account must receive the SMTP settings from step 2. The Alabama-only
filter avoids repeated warning email while North Dakota and Maryland show browser verification.

## How the Maryland email workflow works

Once you're registered as a vendor on eMMA, Maryland emails you every time a
solicitation opens. Those emails all look the same, so the monitor reads them
for you.

Here's the whole thing, start to finish:

1. Maryland sends a "New / Updated Solicitation" email to your inbox.
2. Every morning at 7:00, a scheduled job wakes up.
3. It logs into that inbox and pulls the eMMA emails from the last 7 days.
4. Out of each email it takes the useful bits: the RFx name, BPM ID, commodity,
   round, end date, and the link.
5. It checks those against the keyword list to decide whether it's the kind of
   work you want.
6. If it is, it forwards the email to you, with a short note explaining why it
   matched and the original attached.
7. If it isn't, it's skipped. Either way the run is written to a log.

Nothing happens the moment Maryland hits send. The job only looks once a day, so
you'll hear about a new solicitation the following morning.

### Setting it up

There are two files to fill in.

`.env` holds your mailbox login. Use a Gmail app password, not your normal
password, and keep the quotes — the password has spaces in it and things break
without them.

```
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USERNAME=you@gmail.com
IMAP_PASSWORD="xxxx xxxx xxxx xxxx"
```

`config.toml` says who gets the forwards and when the job runs.

```toml
[email]
senders = ["no-reply.emma@maryland.gov"]
forward_to = ["you@gmail.com"]
since_days = 7

[schedule]
hour = 7
minute = 0
forward = true
```

Then turn the job on:

```bash
scripts/schedule_email_scan.py install
```

To change the time, edit `config.toml` and run `install` again.

### Everyday commands

Try it without sending anything:

```bash
scripts/run_email_scan.py --preview
```

Check whether the job is running, and when it last ran:

```bash
scripts/schedule_email_scan.py status
```

See what it has been doing:

```bash
tail logs/email-scan.log
```

Turn it off:

```bash
scripts/schedule_email_scan.py uninstall
```

### Good to know

- **You won't get the same thing twice.** Once an email has been forwarded it's
  remembered, so a re-send doesn't reach you again. A new round does come
  through, because that really is new.
- **Your inbox isn't touched.** It only reads. Nothing is marked read, moved, or
  deleted.
- **Nothing sends by accident.** Every command previews unless you ask it to
  send. Only the scheduled job and `--forward` actually mail anything.
- **A failed send is retried tomorrow** instead of being quietly dropped.
- **Links are never opened**, just passed along for you to click.
- **Most emails won't match, and that's fine.** The keyword list is tuned for
  executive search, so roofing and road work get skipped.

### What it has to work with

The email is short. It gives the RFx name, BPM ID, commodity, lot, round, end
date, and requester, which is a lot less than the portal page shows, so the name
of the solicitation ends up doing most of the work. `Main commodity` is "Other"
on most notices, so don't lean on it.

Two things are deliberately left out of the matching:

- The **requester's name**, because someone called "Dean" or "Chancellor" would
  read as a job title and cause a false match.
- The **link**, because Maryland builds a fresh one every time it sends, and we'd
  otherwise mistake that for the solicitation itself having changed.

### On Windows

The `install` command is macOS only. On Windows Server, point a Task Scheduler
action at `scripts/run_email_scan.py` and see
[`docs/deployment_guide.md`](docs/deployment_guide.md).

## Run

Explain one title without opening a browser:

```powershell
.\.venv\Scripts\rfp-monitor.exe classify "Executive Search Firm" --status "Open for Bidding"
```

Run the first scan as a silent baseline:

```powershell
.\.venv\Scripts\rfp-monitor.exe scan
```

Preview all current relevant records in a disposable state database:

```powershell
.\.venv\Scripts\rfp-monitor.exe scan `
  --state .\var\validation\rfp-preview.sqlite3 `
  --include-baseline
```

Scan one source or emit JSON:

```powershell
.\.venv\Scripts\rfp-monitor.exe scan --source north-dakota
.\.venv\Scripts\rfp-monitor.exe scan --json
```

Save five live normalized records without changing SQLite or sending email:

```powershell
.\.venv\Scripts\rfp-monitor.exe prototype `
  --source alabama `
  --limit 5 `
  --output examples/alabama_sample.json
```

Send the digest through SMTP:

```powershell
.\.venv\Scripts\rfp-monitor.exe scan --source alabama --send
```

The command exits with status `2` when any source fails so a scheduler can surface the
problem. A failed scan never changes that source's stored records.

## Daily schedule

Keep scheduling outside the application. On the company Windows server, use Task Scheduler with
the dedicated task account, repository as the **Start in** folder, no overlapping instances, missed-
run catch-up, and log redirection. The exact action and validation steps are in the deployment guide.

## Portal access

North Dakota and Maryland may show a browser/reCAPTCHA check; both presented browser verification
during the August 3, 2026 deployment-guide validation. Alabama uses the official public search at
`rfp.alabama.gov`; the separate AlabamaBuys site disallows crawling in `robots.txt`
and presents reCAPTCHA, so this project does not automate it. The monitor reports an
access challenge and preserves prior state; it does not bypass CAPTCHA or authentication.

Where permitted, a persistent Playwright profile can be configured with
`monitor.profile_path` and an operator can run `--headed` to complete an interactive
browser check. That does not guarantee future unattended access. Authenticated detail
pages and attachments are deliberately outside this first version; alerts fall back
to the public listing or browse-page link.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

The tests use saved row fixtures and temporary SQLite databases, so they do not call
the live procurement portals.

See [`docs/alabama_and_crawling_solution_research.md`](docs/alabama_and_crawling_solution_research.md)
for the Alabama source assessment, live prototype result, compliance notes, and browser-tool
comparison.

For a new company Windows server, use the full
[`deployment guide`](docs/deployment_guide.md) and the short
[`Monday Zoom runbook`](docs/monday_deployment_runbook.md). These use native Windows Server and
Task Scheduler as the primary target, with short macOS/Linux alternatives, validation, optional
SMTP, troubleshooting, and rollback.
