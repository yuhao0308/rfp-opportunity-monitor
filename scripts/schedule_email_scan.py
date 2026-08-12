#!/usr/bin/env python3
"""Install, remove, or inspect the daily email-scan job (macOS launchd).

The run time comes from [schedule] in config.toml, so changing the hour is a
config edit followed by a re-install rather than hand-editing a plist.

    scripts/schedule_email_scan.py install
    scripts/schedule_email_scan.py status
    scripts/schedule_email_scan.py uninstall

On Windows Server the equivalent is a Task Scheduler entry running
scripts/run_email_scan.py; see docs/deployment_guide.md.
"""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_email_scan import venv_python

from rfp_monitor.config import load_config

LABEL = "com.rfp-monitor.email-scan"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
RUNNER = ROOT / "scripts" / "run_email_scan.py"


def _domain() -> str:
    import os

    return f"gui/{os.getuid()}"


def build_plist(hour: int, minute: int) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [venv_python(), str(RUNNER)],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        # The wrapper writes its own timestamped log; these catch anything that
        # fails before Python starts, such as a missing interpreter.
        "StandardOutPath": str(ROOT / "logs" / "launchd.out.log"),
        "StandardErrorPath": str(ROOT / "logs" / "launchd.err.log"),
        "RunAtLoad": False,
        "ProcessType": "Background",
    }


def install(config_path: str) -> int:
    schedule = load_config(config_path).schedule
    if not RUNNER.exists():
        print(f"error: runner not found at {RUNNER}", file=sys.stderr)
        return 2

    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(build_plist(schedule.hour, schedule.minute)))

    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, check=False)
    result = subprocess.run(
        ["launchctl", "bootstrap", _domain(), str(PLIST)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"error: launchctl bootstrap failed: {result.stderr.strip()}", file=sys.stderr)
        return result.returncode

    mode = "forwarding matches" if schedule.forward else "preview only, sending nothing"
    print(f"Installed {LABEL}: daily at {schedule.clock} local, {mode}.")
    print(f"  plist: {PLIST}")
    print(f"  log:   {ROOT / 'logs' / 'email-scan.log'}")
    return 0


def uninstall() -> int:
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, check=False)
    if PLIST.exists():
        PLIST.unlink()
        print(f"Removed {LABEL} and its plist.")
    else:
        print(f"{LABEL} was not installed.")
    return 0


def status(config_path: str) -> int:
    schedule = load_config(config_path).schedule
    print(f"config.toml [schedule]: {schedule.clock} local, forward={schedule.forward}")
    print(f"plist present: {PLIST.exists()}  ({PLIST})")

    result = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print("launchd: not loaded")
        return 0
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("state =", "last exit code =", "runs =")):
            print(f"launchd: {stripped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print("error: this installer targets macOS launchd", file=sys.stderr)
        return 2
    if args.action == "install":
        return install(args.config)
    if args.action == "uninstall":
        return uninstall()
    return status(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
