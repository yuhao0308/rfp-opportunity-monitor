# Deployment guide

This guide installs the RFP Opportunity Monitor on a new company Windows server. It does **not**
deploy anything automatically. Complete the manual checks before enabling email or a schedule.

## Recommended approach

Use native 64-bit Windows Server 2019 or newer, Python 3.12, a project-local virtual environment,
Playwright Chromium, the bundled SQLite state file, and Windows Task Scheduler. The application is
a small Python CLI; it does not need WSL, Docker, PostgreSQL, a web server, or a cloud service.

Platform status:

| Platform | Status | Recommendation |
|---|---|---|
| Windows Server 2019+ | Primary target; Playwright-supported | Use the PowerShell instructions below. A real server validation is still required before go-live. |
| Windows 11 | Playwright-supported | The same PowerShell instructions should work. |
| macOS | Existing development and validation platform | Use only for development or emergency manual operation. |
| Linux | Expected to work with additional Chromium system libraries | Use a systemd timer if the target changes to Linux. |

The repository requires Python 3.11 or newer and uses `pip` with `pyproject.toml`. Python 3.12 x64
is the recommended server version. A virtual environment is not enforced by the code, but this guide
requires one so dependencies and commands stay isolated. Playwright 1.61.0 is installed by `pip`;
its matching Chromium binary must be installed separately. Windows timezone data is included through
the conditional `tzdata` dependency.

> Release prerequisite: these deployment documents, the Windows portability update, and the Alabama
> adapter are currently local working-tree changes. This task intentionally does not commit or push
> them. Before the Zoom session, an authorized maintainer must review the changes and publish an
> approved commit/branch, or provide a verified secure copy.

## Architecture and file locations

- Windows executable: `.venv\Scripts\rfp-monitor.exe`.
- Python executable: `.venv\Scripts\python.exe`.
- Application configuration: `config.toml`.
- Keyword taxonomy: `config\keywords.json`.
- Persistent SQLite state: `var\rfp-monitor.sqlite3`. `scan` creates the folder/schema when needed.
- Validation-only files: `var\validation\`.
- Prototype JSON default: `var\prototype-output.json`.
- The CLI writes logs to stdout/stderr. The Task Scheduler action below appends those streams to
  `logs\rfp-monitor.log`.
- Playwright browsers normally live under `%USERPROFILE%\AppData\Local\ms-playwright`. Install and
  run the task as the same Windows account, or configure a company-approved shared browser path.
- Email is optional and is attempted only with `scan --send`, when relevant changes or source errors
  exist.
- Scheduling is external; the application does not install a Windows service or scheduled task.
- `monitor.profile_path` is optional and is not configured. Alabama needs no browser profile/login.

There is no database server, migration command, or `DATABASE_URL`. Relative paths in `config.toml`
resolve from that file's directory, but the CLI should still start in the repository root so it finds
the default `config.toml`.

## Pre-deployment checklist

### Required before the meeting

- [ ] Confirm the exact Windows edition/build with `winver`; it must be Windows Server 2019+ or Windows 11.
- [ ] Confirm the server is 64-bit and has at least 2 GB free for Python, the virtual environment,
      Chromium, logs, and working data.
- [ ] Arrange local administrator access for Git, Python, Chromium, firewall, and scheduled-task setup.
- [ ] Choose the permanent application account. Prefer a dedicated company service account rather
      than a personal administrator account.
- [ ] Choose the permanent folder, for example `C:\CompanyApps\rfp-opportunity-monitor`, and grant
      that account Modify access to the repository, `var`, and `logs`.
- [ ] Confirm the service account can log on as a batch job and that its profile may be loaded by
      Task Scheduler.
- [ ] Confirm internet access to GitHub, PyPI, Microsoft's Playwright browser CDN, North Dakota Buys,
      Maryland eMMA, and `https://rfp.alabama.gov/PublicView.aspx`.
- [ ] Obtain company proxy/root-certificate settings if HTTPS inspection is used. Do not disable TLS.
- [ ] Confirm endpoint protection permits Playwright's bundled Chromium and child processes.
- [ ] Confirm access to `https://github.com/yuhao0308/rfp-opportunity-monitor.git`, or arrange a
      secure repository copy if it is private.
- [ ] Confirm the approved release contains the Alabama adapter, Windows portability change, and
      both deployment documents.
- [ ] Install Zoom on the operator's workstation and confirm remote control. A person at the server
      console may need to approve UAC prompts; secure-desktop prompts may not be remotely controllable.
- [ ] If email is required, ask the manager for recipients, SMTP host/port, STARTTLS requirement,
      sender, authentication method, and credentials through an approved secret channel.
- [ ] Agree on schedule time/timezone, missed-run behavior, reboot window, and whether the server is always on.
- [ ] Agree on log retention, SQLite backups, and an operational owner.
- [ ] Reserve 60–90 minutes, plus time for security/proxy approvals.
- [ ] Agree on rollback: disable the task, preserve SQLite/logs, remove secrets, then remove the app.

### Can be completed during the meeting

- [ ] Verify/install Git and Python 3.12 x64.
- [ ] Clone/copy the approved release and create `.venv`.
- [ ] Install dependencies and Playwright Chromium under the permanent task account.
- [ ] Configure recipients and optional SMTP environment variables.
- [ ] Run tests, Ruff, Chromium launch, and disposable source checks.
- [ ] Establish the production SQLite baseline without `--send`.
- [ ] Create—but do not enable—the scheduled task until manual validation is accepted.

### Optional after deployment

- [ ] Configure Windows log rotation or archival and scheduled SQLite backups.
- [ ] Monitor Task Scheduler failures and nonzero exit codes.
- [ ] Obtain approved feeds/access paths for North Dakota and Maryland.
- [ ] Obtain source-owner confirmation for long-term low-frequency public metadata collection.

## Exact installation steps: native Windows

Use **64-bit Windows PowerShell 5.1 or newer**. Run as the same account that will own the scheduled
task. Commands below assume the permanent parent folder is `C:\CompanyApps`; change it if IT chooses
another location.

### 1. Confirm Windows and tools

```powershell
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsArchitecture
[System.Environment]::OSVersion.VersionString
[System.Environment]::Is64BitOperatingSystem
git --version
py -0p
py -3.12 --version
```

Expected: a supported 64-bit Windows build, Git, and Python 3.12. If `git` or `py` is missing, use
the company software portal. If WinGet is approved and available, an administrator can use:

```powershell
winget install --id Git.Git -e --source winget
winget install --id Python.Python.3.12 -e --source winget
```

Close and reopen PowerShell, then repeat the version checks. Windows Server 2019 may not provide
WinGet; use the company-managed Git installer and Python 3.12 x64 MSI/full installer instead. Enable
the Windows long-path policy if company standards permit it.

### 2. Clone or copy the approved release

```powershell
$RepoParent = "C:\CompanyApps"
New-Item -ItemType Directory -Force -Path $RepoParent | Out-Null
Set-Location $RepoParent
git clone https://github.com/yuhao0308/rfp-opportunity-monitor.git
Set-Location .\rfp-opportunity-monitor
git status --short --branch
git log -1 --oneline
```

A clean clone should show the approved branch and no modifications. For an approved ZIP/secure copy,
extract it to `$RepoParent\rfp-opportunity-monitor`, enter the folder, and record its release identifier.

### 3. Create the virtual environment

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
```

`Set-ExecutionPolicy -Scope Process` affects only the current PowerShell window. Activation is useful
interactively but not required; later commands use the virtual-environment executables directly.

### 4. Install Python dependencies and Chromium

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m playwright --version
.\.venv\Scripts\python.exe -m playwright install --list
```

Expected: Playwright `1.61.0` and an installed Chromium entry. Windows does not use Playwright's
Linux `install-deps` step. Install Chromium as the permanent task account because Playwright's default
cache is per-user.

### 5. Create runtime folders and verify permissions

```powershell
New-Item -ItemType Directory -Force -Path .\logs, .\var\validation | Out-Null
$WriteTest = Join-Path (Resolve-Path .\var) ".write-test"
Set-Content -Path $WriteTest -Value "ok"
Remove-Item -LiteralPath $WriteTest
Get-Acl .\var | Format-List Owner,AccessToString
Get-Acl .\logs | Format-List Owner,AccessToString
```

The permanent task account needs Modify access. Use folder Properties → Security or company ACL
management to grant only the required account and administrators. Do not run the daily task as a
domain administrator.

### 6. Configure the application

Edit `config.toml` with Notepad or the approved editor:

```powershell
notepad .\config.toml
```

Keep the default state path and set recipients only when email is approved:

```toml
[monitor]
state_path = "var/rfp-monitor.sqlite3"

[notifications]
recipients = ["recipient@example.com"]
subject_prefix = "[RFP Monitor]"
timezone = "America/New_York"
```

Keep `recipients = []` until an intentional email test. No database initialization command is
needed; the first scan creates SQLite automatically.

If email will be used, create a local template copy:

```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
```

`.env` is ignored by Git, but the CLI does **not** load it automatically. It is a reference/template
only unless a wrapper explicitly loads it. For the production scheduled task, configure the SMTP
variables in the dedicated task account's Windows environment or company secret-injection system.
Restrict `.env` to the task account and administrators if it is retained.

### 7. Run offline validation

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3, playwright, rfp_monitor; print('imports: OK')"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

Expected: imports print `OK`, all tests pass, and Ruff prints `All checks passed!`.

Launch and close Chromium:

```powershell
@'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    print(f"Chromium launch: OK ({browser.version})")
    browser.close()
'@ | .\.venv\Scripts\python.exe -
```

Expected: `Chromium launch: OK (...)`, exit code 0, and no browser remains running.

### 8. Run the safe Alabama prototype

This saves JSON but does not touch SQLite and cannot send email:

```powershell
& .\.venv\Scripts\rfp-monitor.exe prototype `
  --source alabama `
  --limit 5 `
  --output .\var\validation\alabama-sample.json `
  2> .\logs\alabama-prototype.log
$LASTEXITCODE
Get-Content -Raw .\var\validation\alabama-sample.json | ConvertFrom-Json | Out-Null
```

Expected: exit `0`, 1–5 saved normalized records, and valid JSON. The current listing count changes.

### 9. Run Alabama with disposable SQLite state

```powershell
& .\.venv\Scripts\rfp-monitor.exe scan `
  --source alabama `
  --state .\var\validation\alabama.sqlite3 `
  --include-baseline `
  --json `
  1> .\var\validation\alabama-scan.json `
  2>> .\logs\alabama-prototype.log
$AlabamaExit = $LASTEXITCODE
"Alabama exit code: $AlabamaExit"
Get-Content -Raw .\var\validation\alabama-scan.json | ConvertFrom-Json | Out-Null
```

Required result: exit `0`, a `source_scan_success` log entry, and valid JSON. Alabama currently needs
no credentials, profile, CAPTCHA handling, or additional dependency.

### 10. Check North Dakota safely

```powershell
& .\.venv\Scripts\rfp-monitor.exe scan `
  --source north-dakota `
  --state .\var\validation\north-dakota.sqlite3 `
  --include-baseline `
  --json `
  1> .\var\validation\north-dakota-scan.json `
  2> .\logs\north-dakota-validation.log
$NorthDakotaExit = $LASTEXITCODE
"North Dakota exit code: $NorthDakotaExit"
```

Operational success requires exit `0` and a record count. On August 3, 2026, the official page
presented browser verification; the monitor failed safely. Exit `2` with that warning confirms safe
handling but means the source is blocked. Do not bypass the control; request an approved feed/access path.

### 11. Check Maryland safely

```powershell
& .\.venv\Scripts\rfp-monitor.exe scan `
  --source maryland `
  --state .\var\validation\maryland.sqlite3 `
  --json `
  1> .\var\validation\maryland-scan.json `
  2> .\logs\maryland-validation.log
$MarylandExit = $LASTEXITCODE
"Maryland exit code: $MarylandExit"
```

Maryland may return records/exit `0`, but currently presents browser verification and exits `2`.
On failure, it does not replace that source's stored data. Do not automate CAPTCHA solving. Use
`--headed` only for authorized diagnosis; a manual pass is not an unattended solution.

### 12. Establish the production baseline without email

After disposable checks, run enabled sources without `--send`:

```powershell
& .\.venv\Scripts\rfp-monitor.exe scan *>&1 |
  Tee-Object -FilePath .\logs\rfp-monitor.log -Append
$BaselineExit = $LASTEXITCODE
"Baseline exit code: $BaselineExit"
Get-Item .\var\rfp-monitor.sqlite3, .\logs\rfp-monitor.log
```

The first successful scan for each source is a silent baseline and sends no email. The overall exit
can be `2` because North Dakota/Maryland are blocked, while successful Alabama data is still saved.

### 13. Configure and test SMTP only if approved

For the current PowerShell process, set non-secret values and enter the password without shell history:

```powershell
$env:SMTP_HOST = "smtp.example.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "your_username"
$env:SMTP_FROM = "rfp-monitor@example.com"
$env:SMTP_STARTTLS = "true"
$SecurePassword = Read-Host "SMTP password" -AsSecureString
$env:SMTP_PASSWORD = [Net.NetworkCredential]::new("", $SecurePassword).Password
```

There is no standalone test-email command. The smallest test sends a real message, so run it only
with manager approval and a disposable database:

```powershell
& .\.venv\Scripts\rfp-monitor.exe scan `
  --source alabama `
  --state .\var\validation\email-test.sqlite3 `
  --include-baseline `
  --send
```

Never use `--send` during ordinary validation. For Task Scheduler, have IT set these variables for
the dedicated task account or inject them with the approved secret system. Do not put passwords in
task arguments, Git, this guide, or Zoom chat.

### 14. Verify restart and reboot behavior

Close PowerShell, open a fresh window, and run without activation:

```powershell
Set-Location C:\CompanyApps\rfp-opportunity-monitor
& .\.venv\Scripts\rfp-monitor.exe classify "Executive Search Firm" --status "Open for Bidding"
$LASTEXITCODE
```

Expected: a classification and exit `0`. After Task Scheduler is configured, reboot during an
approved window and verify the same command, task history, log access, and Chromium launch under the
permanent task account.

## Environment variables

All application environment variables are SMTP settings. They are required only for `scan --send`;
normal scans and prototypes require none.

| Variable | Requirement | Purpose | Safe example | Obtain from | Sensitive | If missing |
|---|---|---|---|---|---|---|
| `SMTP_HOST` | Required with `--send` | SMTP hostname | `smtp.example.com` | Mail/IT admin | No | A send attempt exits 2 with a configuration error. |
| `SMTP_PORT` | Optional | SMTP port | `587` | Mail/IT admin | No | Defaults to `587`. |
| `SMTP_USERNAME` | Optional; pair with password | SMTP login | `your_username` | Mail/IT admin | Usually | Login is skipped if either username/password is absent; relay may reject mail. |
| `SMTP_PASSWORD` | Optional; pair with username | SMTP/app password | `your_password` | Secret owner | Yes | Login is skipped if either half is absent; never commit it. |
| `SMTP_FROM` | Required with `--send` | Sender address | `rfp-monitor@example.com` | Mail/IT admin | No | A send attempt exits 2 with a configuration error. |
| `SMTP_STARTTLS` | Optional | Enable STARTTLS | `true` | Mail/IT admin | No | Defaults to `true`; use `false` only for an approved relay. |
| `DATABASE_URL` | Not used | Not applicable; storage is SQLite | Do not set | Not applicable | Not applicable | Ignored. |

Recipients are configured in `config.toml`, not an environment variable. Minimum send configuration:
`SMTP_HOST`, `SMTP_FROM`, and at least one recipient.

## Deployment validation checklist

| Check | Exact command/result |
|---|---|
| Python/imports | `.\.venv\Scripts\python.exe -c "import sqlite3, playwright, rfp_monitor; print('imports: OK')"` → `imports: OK`. |
| Unit tests | `.\.venv\Scripts\python.exe -m pytest` → all tests pass. |
| Ruff | `.\.venv\Scripts\python.exe -m ruff check .` → `All checks passed!`. |
| Chromium | Run the step 7 snippet → browser version and exit 0. |
| Alabama public source | Run step 8 → 1–5 normalized records saved. |
| JSON | `Get-Content -Raw .\var\validation\alabama-sample.json \| ConvertFrom-Json \| Out-Null` → no error. |
| Alabama state scan | Run step 9 → exit 0 and `source_scan_success`. |
| North Dakota | Run step 10 → operational sign-off needs exit 0; current verification/exit 2 is a blocker. |
| Maryland safe failure | Run step 11 → exit 0 with records or exit 2 with a clear warning and no partial update. |
| Duplicate detection | `.\.venv\Scripts\python.exe -m pytest tests\test_state.py -q` → all pass. |
| Date portability/normalization | `.\.venv\Scripts\python.exe -m pytest tests\test_digest.py tests\test_source.py -q` → all pass. |
| Logs | `Get-Item .\logs\alabama-prototype.log` → nonempty file. |
| No email | Run any command without `--send`; SMTP is never called. |
| Useful failure | Run `scan --source does-not-exist`; `$LASTEXITCODE` is `2` with a clear message. |
| No hanging monitor | `Get-CimInstance Win32_Process -Filter "Name = 'rfp-monitor.exe'"` → no result after completion. |
| Restart | Run `classify` from a fresh PowerShell window without activation → exit 0. |

Do not use production state with `--include-baseline` during validation. Live counts change, so do
not hard-code exact record totals.

## Windows Task Scheduler

Configure scheduling only after manual validation. North Dakota and Maryland are currently blocked;
if they remain enabled in an unfiltered `scan --send`, the task exits 2 and can email the same warning
daily. The initial task should therefore be an **Alabama-only pilot** using `--source alabama`, or
scheduling should remain disabled.

### Prepare the task account

1. Use the same dedicated account that installed Chromium, or reinstall Chromium while signed in as
   the final account.
2. Grant Log on as a batch job and Modify permission to the repository, `var`, and `logs`.
3. Configure SMTP variables in that account's Windows environment only if the task uses `--send`.
4. Sign out/in or reboot after persistent environment changes, then validate them without printing
   the password.

### Create the task in Task Scheduler

Open **Task Scheduler → Create Task** (not Basic Task):

- **General**
  - Name: `RFP Opportunity Monitor`
  - Account: the dedicated task account
  - Select **Run whether user is logged on or not**.
  - Use **Run with highest privileges** only if company policy requires it; normal execution should
    work without administrator rights after installation.
- **Triggers**
  - Daily at the approved time, for example 8:00 AM Eastern.
  - Enable the trigger.
- **Actions**
  - Program: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
  - Arguments (replace the repository path if different):

    ```text
    -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\CompanyApps\rfp-opportunity-monitor'; & '.\.venv\Scripts\rfp-monitor.exe' scan --source alabama --send *>> '.\logs\rfp-monitor.log'; exit $LASTEXITCODE"
    ```

  - Start in: `C:\CompanyApps\rfp-opportunity-monitor`
- **Conditions**
  - For a server, do not require AC power or idle state.
  - Enable **Wake the computer to run this task** only if approved and relevant.
- **Settings**
  - Enable **Run task as soon as possible after a scheduled start is missed**.
  - If the task is already running: **Do not start a new instance**.
  - Stop the task if it runs longer than 1 hour.
  - Enable task history for troubleshooting.

For storage-only mode, remove `--send`; SMTP variables are then unnecessary. Do not remove
`--source alabama` until North Dakota/Maryland pass collection or receive approved access paths.

Before enabling, use **Run** once under supervision. Confirm:

```powershell
Get-ScheduledTask -TaskName "RFP Opportunity Monitor" | Format-List TaskName,State
Get-ScheduledTaskInfo -TaskName "RFP Opportunity Monitor" |
  Format-List LastRunTime,LastTaskResult,NextRunTime
Get-Content .\logs\rfp-monitor.log -Tail 100
```

`LastTaskResult = 0` is successful. The task's working directory, account, environment, and browser
cache must match the manually validated setup. Task Scheduler continues after reboot and can catch a
missed start when that setting is enabled.

## Short alternatives

### macOS

Use `python3.12 -m venv .venv`, install with `.venv/bin/python -m pip install -e ".[dev]"`, install
Chromium with `.venv/bin/python -m playwright install chromium`, and schedule the absolute
`.venv/bin/rfp-monitor` command through `launchd` with stdout/stderr paths. `launchd` naturally avoids
two instances of one job.

### Linux

Use Python 3.11+, then install browser/OS dependencies with
`.venv/bin/python -m playwright install --with-deps chromium`. Prefer a systemd oneshot service and
timer with `WorkingDirectory`, `EnvironmentFile`, `Persistent=true`, journal/file logging, and no
overlapping service instance. Linux is not the current deployment target.

## Source behavior and access controls

- **Alabama:** official public ASP.NET search; currently returns records. It needs Chromium but no
  login, session profile, or extra environment variable. An empty result fails instead of replacing state.
- **North Dakota:** public JAGGAER listing. On August 3, 2026 it presented browser verification. The
  monitor stopped, reported the challenge, and preserved state.
- **Maryland:** public JAGGAER listing that currently presents reCAPTCHA/browser verification. The
  monitor stops that source, preserves state, reports the warning, and exits 2 after other sources run.

Do not solve CAPTCHA automatically, conceal automation, rotate identities/proxies, or use credentials
without written authorization. Use `--headed` only for authorized diagnosis. Ask the portal owner for
a supported feed or explicit access method.

## Troubleshooting

### `py`, `python`, or Git not found / wrong Python version

Diagnose with `Get-Command py,python,git -ErrorAction SilentlyContinue`, `py -0p`, and
`py -3.12 --version`. Install company-approved Python 3.12 x64 and Git, reopen PowerShell, and recreate
the virtual environment. Do not copy `.venv` from another computer.

### PowerShell activation is blocked

Use `Set-ExecutionPolicy -Scope Process Bypass`, or skip activation and invoke
`.\.venv\Scripts\python.exe`/`rfp-monitor.exe` directly. Do not weaken machine-wide execution policy.

### Dependency installation fails

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -v -e ".[dev]"
```

Give the verbose error to IT. Configure approved proxy/CA values; never use `--trusted-host` to bypass TLS.

### Git authentication fails

Run `git ls-remote https://github.com/yuhao0308/rfp-opportunity-monitor.git`. Sign in through the
company credential manager, use an approved PAT/SSH key, or obtain a secure archive. Never place a
token in a clone URL.

### Proxy blocks pip or Chromium downloads

Use approved process variables:

```powershell
$env:HTTPS_PROXY = "http://proxy.example.com:8080"
$env:NODE_EXTRA_CA_CERTS = "C:\CompanyCertificates\root-ca.crt"
$env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT = "120000"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Ask IT for exact values. Do not commit them or disable certificate validation.

### Playwright browser missing / Chromium launch fails

Run `python -m playwright install --list`, reinstall Chromium with the same account that runs the
task, and rerun the launch snippet. Check `%USERPROFILE%\AppData\Local\ms-playwright`, Windows Event
Viewer, endpoint-protection quarantine, and AppLocker/WDAC policy. Ask IT for a narrow allow-list;
do not disable antivirus globally.

### Permission denied / SQLite inaccessible

Confirm the Task Scheduler account and inspect `Get-Acl .\var, .\logs`. Give that account Modify
permission and ensure the repository is not in a read-only/network location. Do not run daily scans
as an administrator merely to bypass folder permissions.

### `.env` is not loading

This is expected: the CLI does not load dotenv files. Set variables in the current PowerShell process
for manual tests and in the permanent task account environment/approved secret system for scheduled
runs. Never assume Task Scheduler reads `.env`.

### Database locked

An overlapping scan or SQLite viewer is holding the file. Diagnose with:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'rfp-monitor' } |
  Select-Object ProcessId,CommandLine
```

Stop the duplicate exact process, close database tools, and set Task Scheduler to **Do not start a
new instance**. Do not delete SQLite to clear a lock.

### CAPTCHA/login detected

Read the source warning. For one authorized diagnostic use `--headed` with disposable state. Do not
bypass the challenge; preserve state and contact the source/manager.

### Alabama returns zero records

Run the prototype with `--headed`, inspect `logs\alabama-prototype.log`, and open the official page
manually. Treat zero results as a source failure until explained; do not reset the baseline.

### Missing/malformed dates

Run `.\.venv\Scripts\python.exe -m pytest tests\test_digest.py tests\test_source.py -q`, inspect the
saved JSON, and preserve source evidence. Missing dates remain blank rather than guessed.

### SMTP authentication fails

Confirm host, port, STARTTLS, username/password pair, allowed sender, and relay policy with IT. The
code supports SMTP/STARTTLS but not OAuth. Re-enter secrets without console echo and use only the
approved disposable email test.

### Duplicate/repeated emails

Check for multiple tasks, different SQLite paths, `--include-baseline`, or lost state. Every production
run must use `var\rfp-monitor.sqlite3`. Remove `--include-baseline` from scheduling. Source-error
digests repeat while an enabled source remains blocked; use the Alabama filter until resolved.

### Task works manually but not automatically

Compare task account, Start in folder, environment, ACLs, browser cache, and Task History. Use absolute
paths and the venv executable. A browser installed under the Zoom operator's profile is not available
automatically to a different task account.

### Task does not run after reboot / logs missing

Enable **Run whether user is logged on or not** and **Run as soon as possible after a missed start**.
Confirm the account password/service rights and `LastTaskResult`. Logs only exist because the PowerShell
task action redirects output; verify `logs` is writable and the action contains `*>>`.

## Rollback and cleanup

1. Disable and stop the exact scheduled task:

   ```powershell
   Disable-ScheduledTask -TaskName "RFP Opportunity Monitor"
   Stop-ScheduledTask -TaskName "RFP Opportunity Monitor"
   ```

2. Find the exact monitor process before stopping anything:

   ```powershell
   Get-CimInstance Win32_Process |
     Where-Object { $_.CommandLine -match 'rfp-monitor' } |
     Select-Object ProcessId,CommandLine
   ```

   Stop only the confirmed PID with `Stop-Process -Id <confirmed-pid>`.
3. Preserve state/logs in an approved backup location:

   ```powershell
   Copy-Item .\var\rfp-monitor.sqlite3 C:\ApprovedBackup\rfp-monitor.sqlite3
   Copy-Item .\logs\rfp-monitor.log C:\ApprovedBackup\rfp-monitor.log
   ```

4. Remove the task after backup/approval:
   `Unregister-ScheduledTask -TaskName "RFP Opportunity Monitor" -Confirm`.
5. Optionally uninstall this Playwright Chromium before deleting `.venv`:
   `.\.venv\Scripts\python.exe -m playwright uninstall chromium`. Confirm no other project uses it.
6. Remove the confirmed project `.venv`, validation files, logs, and SQLite only after backup approval.
7. Delete SMTP variables from the task account environment/secret system and delete `.env`.
8. Remove the repository folder only after checking for uncommitted work and verified backups.
9. Confirm no task/process remains with `Get-ScheduledTask -TaskName "RFP Opportunity Monitor"` and
   the process query above.

The application has no external database, Windows service, container, or cloud resource to remove.

## Information still required from the manager/IT

- Exact Windows edition/build and server architecture.
- Permanent path and dedicated Task Scheduler account.
- Approved Git release/branch and private-repository access method.
- Recipient addresses and whether email is required at initial launch.
- SMTP host/port/TLS/auth/sender and approved secret-delivery method.
- Schedule time/timezone and reboot/missed-run requirements.
- Proxy, root CA, firewall, AppLocker/WDAC, and antivirus requirements.
- Log retention, SQLite backup location, and support/escalation owner.
- Decision: Alabama-only pilot or no schedule while North Dakota/Maryland are blocked.
