# Monday Windows deployment runbook

Use this during the Zoom session with the full [deployment guide](deployment_guide.md). Target:
native 64-bit Windows Server 2019 or newer. Do not enable email or Task Scheduler until manual
validation passes and the manager approves it. Expected time: 60–90 minutes plus security delays.

## A. Before the call — 10 minutes

- [ ] Confirm Windows Server version/build and 64-bit architecture.
- [ ] Confirm a local administrator and the permanent Task Scheduler/service account are available.
- [ ] Confirm Zoom remote control; a local operator may need to approve secure UAC prompts.
- [ ] Confirm GitHub/repository access and permanent folder, such as `C:\CompanyApps`.
- [ ] Confirm the approved release contains Alabama, Windows portability, and deployment docs.
- [ ] Confirm internet/proxy/root-CA access to GitHub, PyPI, Playwright CDN, and source sites.
- [ ] Confirm antivirus/AppLocker/WDAC approval for Playwright Chromium.
- [ ] Have SMTP details in an approved secret channel only if email is required.
- [ ] Agree on schedule/timezone, backup/log retention, reboot policy, and operational owner.
- [ ] Agree on Alabama-only pilot versus no schedule while North Dakota/Maryland are blocked.

## B. First 10 minutes — access and preflight

- [ ] Start Zoom remote control and open 64-bit PowerShell.
- [ ] Run:

  ```powershell
  Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsArchitecture
  [System.Environment]::OSVersion.VersionString
  [System.Environment]::Is64BitOperatingSystem
  git --version
  py -0p
  py -3.12 --version
  ```

- [ ] Confirm Windows Server 2019+, Git, and Python 3.12 x64.
- [ ] Confirm the permanent task account can write to the chosen folder and run batch tasks.

## C. Installation — 15–25 minutes

- [ ] Clone the approved release:

  ```powershell
  $RepoParent = "C:\CompanyApps"
  New-Item -ItemType Directory -Force -Path $RepoParent | Out-Null
  Set-Location $RepoParent
  git clone https://github.com/yuhao0308/rfp-opportunity-monitor.git
  Set-Location .\rfp-opportunity-monitor
  git status --short --branch
  git log -1 --oneline
  ```

- [ ] Create/install the environment under the permanent task account:

  ```powershell
  py -3.12 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
  .\.venv\Scripts\python.exe -m playwright install chromium
  .\.venv\Scripts\python.exe -m playwright install --list
  New-Item -ItemType Directory -Force -Path .\logs, .\var\validation | Out-Null
  ```

- [ ] Confirm the task account has Modify permission on the repository, `var`, and `logs`.

## D. Configuration — 5–10 minutes

- [ ] Review `config.toml`; keep `state_path = "var/rfp-monitor.sqlite3"`.
- [ ] Keep `recipients = []` until the email test is approved.
- [ ] Confirm no database server or `DATABASE_URL` is required.
- [ ] If email is approved, enter SMTP values in the task account's approved environment/secret
      system. Do not put passwords in Git, task arguments, shell history, Zoom chat, or this runbook.
- [ ] Remember: the CLI does not automatically load `.env`.

## E. Validation — 20–30 minutes

- [ ] Run offline checks:

  ```powershell
  .\.venv\Scripts\python.exe -c "import sqlite3, playwright, rfp_monitor; print('imports: OK')"
  .\.venv\Scripts\python.exe -m pytest
  .\.venv\Scripts\python.exe -m ruff check .
  ```

- [ ] Launch/close Chromium using the deployment-guide snippet.
- [ ] Run Alabama prototype to `var\validation\alabama-sample.json`; confirm valid JSON.
- [ ] Run Alabama with disposable SQLite; require exit 0 and records.
- [ ] Run North Dakota with disposable SQLite. Record verification/exit 2 as a blocker unless it works.
- [ ] Run Maryland with disposable SQLite. Accept records/exit 0 or safe verification/exit 2.
- [ ] Confirm logs are nonempty and no `rfp-monitor.exe` or Chromium process remains.
- [ ] Establish real state without email:

  ```powershell
  & .\.venv\Scripts\rfp-monitor.exe scan *>&1 |
    Tee-Object -FilePath .\logs\rfp-monitor.log -Append
  ```

- [ ] Record each source result and the production SQLite path.

## F. Scheduling — 10–15 minutes

- [ ] Use Windows Task Scheduler → **Create Task**.
- [ ] Select the dedicated account and **Run whether user is logged on or not**.
- [ ] Set the daily trigger and approved timezone/time.
- [ ] Use the exact PowerShell action from the deployment guide.
- [ ] Start with `--source alabama`; use `--send` only after an approved email test.
- [ ] Set **Do not start a new instance**, missed-run catch-up, one-hour limit, and task history.
- [ ] Run once under supervision; inspect `LastTaskResult` and `logs\rfp-monitor.log`.

## G. Final confirmation — 5–10 minutes

- [ ] Open a fresh PowerShell window and run:

  ```powershell
  Set-Location C:\CompanyApps\rfp-opportunity-monitor
  & .\.venv\Scripts\rfp-monitor.exe classify "Executive Search Firm" --status "Open for Bidding"
  ```

- [ ] During an approved window, reboot and confirm the task account, browser cache, task history,
      logs, SQLite, and restart command still work.
- [ ] Confirm successful commands exit 0 and source failures exit 2 with a useful warning.
- [ ] Confirm the operator knows how to inspect Task Scheduler history/logs and run a no-email scan.
- [ ] Reconfirm rollback, backups, credentials owner, and support owner.

## H. Record before ending the call — 5 minutes

- [ ] Windows edition/build: `_______________________________________`
- [ ] Server name: `_________________________________________________`
- [ ] Repository path: `_____________________________________________`
- [ ] Git commit/branch: `___________________________________________`
- [ ] Python version: `______________________________________________`
- [ ] Virtual environment path: `___________________________________`
- [ ] Task account (name only): `____________________________________`
- [ ] SMTP environment owner/location, no secret: `_________________`
- [ ] SQLite path: `________________________________________________`
- [ ] Log path: `___________________________________________________`
- [ ] Task name/time or “not enabled”: `____________________________`
- [ ] Last task result/time: `______________________________________`
- [ ] Last Alabama result/count: `_________________________________`
- [ ] North Dakota result/blocker: `_______________________________`
- [ ] Maryland result/blocker: `___________________________________`
- [ ] Known limitations: `_________________________________________`
- [ ] Credential owner (role only): `_______________________________`
- [ ] Support/escalation owner: `___________________________________`
- [ ] Restart command: `cd C:\CompanyApps\rfp-opportunity-monitor; .\.venv\Scripts\rfp-monitor.exe scan`
- [ ] Backup/rollback location: `___________________________________`

Do not record SMTP passwords, access tokens, cookies, or browser profile data here.
