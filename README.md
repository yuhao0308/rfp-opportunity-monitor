# RFP Opportunity Monitor

A small daily CLI that watches public procurement portals, detects new or materially
changed listings, applies the Myers McRae relevance framework, and can send an email
digest.

The first configured sources are North Dakota Buys and Maryland eMMA. Both use a
JAGGAER-style public solicitation table, so one adapter handles their different
columns without category, agency, or solicitation-type filters.

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

## Install

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Configure

Edit [`config.toml`](config.toml):

- Add or disable `[[sources]]`.
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

   ```bash
   .venv/bin/rfp-monitor scan
   ```

4. Test email with a temporary database:

   ```bash
   .venv/bin/rfp-monitor scan \
     --state /tmp/rfp-email-test.sqlite3 \
     --include-baseline \
     --send
   ```

5. Schedule this command to run each morning:

   ```bash
   cd /path/to/rfp-opportunity-monitor && .venv/bin/rfp-monitor scan --send
   ```

The scheduler must receive the SMTP settings from step 2. If Maryland shows a CAPTCHA,
the run reports the problem and keeps data from the last successful scan.

## Run

Explain one title without opening a browser:

```bash
rfp-monitor classify "Executive Search Firm" --status "Open for Bidding"
```

Run the first scan as a silent baseline:

```bash
rfp-monitor scan
```

Preview all current relevant records in a disposable state database:

```bash
rfp-monitor scan --state /tmp/rfp-preview.sqlite3 --include-baseline
```

Scan one source or emit JSON:

```bash
rfp-monitor scan --source north-dakota
rfp-monitor scan --json
```

Send the digest through SMTP:

```bash
rfp-monitor scan --send
```

The command exits with status `2` when any source fails so a scheduler can surface the
problem. A failed scan never changes that source's stored records.

## Daily schedule

Keep scheduling outside the application. For example, run at 8:00 AM Eastern with
cron after configuring the absolute virtual-environment and repository paths:

```cron
CRON_TZ=America/New_York
0 8 * * * cd /absolute/path/to/rfp-opportunity-monitor && .venv/bin/rfp-monitor scan --send
```

## Portal access

North Dakota currently exposes its public listing table directly. Maryland may show a
browser/reCAPTCHA check. The monitor detects that challenge, reports the source as
unavailable, and preserves its state; it does not bypass CAPTCHA or authentication.

Where permitted, a persistent Playwright profile can be configured with
`monitor.profile_path` and an operator can run `--headed` to complete an interactive
browser check. That does not guarantee future unattended access. Authenticated detail
pages and attachments are deliberately outside this first version; alerts fall back
to the public listing or browse-page link.

## Verify

```bash
python -m unittest discover -s tests
ruff check .
```

The tests use saved row fixtures and temporary SQLite databases, so they do not call
the live procurement portals.
