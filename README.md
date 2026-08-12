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

## Email ingestion (Maryland eMMA)

Once a vendor account is registered, eMMA emails a "New / Updated Solicitation" notice
for every invitation. `email-scan` reads that inbox, applies the same keyword framework,
and forwards only what qualifies.

```powershell
.\.venv\Scripts\rfp-monitor.exe email-scan
```

Configure it under `[email]` in [`config.toml`](config.toml) and add the IMAP variables
from [`.env.example`](.env.example) to the scheduler's secret store. Gmail requires an
app password, not the account password.

How it behaves:

- **Preview by default.** Nothing is sent and nothing is recorded until you pass
  `--forward`, so a dry run cannot silence the delivery run that follows it.
- **Read-only mailbox access.** The folder is opened `readonly` and fetched with
  `BODY.PEEK[]`, so messages you have not read stay unread and no flags change.
- **Forwards carry the reason.** Each forward states the classification, the score, and
  the matched signals, and attaches the untouched original as `.eml`.
- **No duplicates.** A message is forwarded once, tracked by `Message-ID`, and the
  solicitation is tracked by BPM ID so a later round is reported as `changed` rather
  than as a second `new`.
- **Nothing is persisted when a send fails**, so the next run retries that message.
- **Links are never fetched.** The solicitation URL is passed through for a human.

Exercise the whole pipeline against saved messages instead of a live mailbox:

```powershell
.\.venv\Scripts\rfp-monitor.exe email-scan --from-dir tests\fixtures\emails
```

Send for real, to a throwaway state database first:

```powershell
.\.venv\Scripts\rfp-monitor.exe email-scan --state .\var\validation\email-test.sqlite3 --forward
```

### Run it every morning

`email-scan` polls; nothing pushes to it. Gmail cannot call this machine, so the
delay between Maryland sending a notice and the forward arriving is however often
the job runs.

Set the time in [`config.toml`](config.toml) and install the job:

```bash
scripts/schedule_email_scan.py install
```

```toml
[schedule]
hour = 7
minute = 0
forward = true   # false previews each morning and sends nothing
```

The time lives in config, so changing it is an edit plus a re-install rather than
hand-editing a plist. `status` shows the configured time, whether the job is
loaded, and its last exit code; `uninstall` removes it.

`scripts/run_email_scan.py` is what the schedule actually calls. It loads `.env`,
runs the scan, and appends a timestamped entry to `logs/email-scan.log`. The CLI
itself still never reads `.env` — the wrapper is deployment glue, so credentials
stay out of the application and its state database. If `IMAP_PASSWORD` is empty
the wrapper logs that and exits 2 without contacting anything.

On Windows Server, point a Task Scheduler action at `run_email_scan.py` instead;
the launchd installer is macOS-only and says so.

### What the matcher can see

An eMMA notice carries only the RFx name, BPM ID, commodity, lot, round, end date, and
requester — far less text than a portal listing. Two deliberate exclusions:

- The **requester is a person**, so it is kept out of the matched text. A requester named
  "Dean" or "Chancellor" would otherwise inject a false leadership signal.
- The **per-round link** is kept out of the fingerprint, because a token that rotates on
  every send would otherwise look like a changed solicitation and re-alert.

`Main commodity` is `Other` on most notices, so in practice the RFx name carries the
decision. That is the narrow input the planned transformer pass is meant to widen.

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
