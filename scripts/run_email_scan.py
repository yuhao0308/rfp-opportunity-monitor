#!/usr/bin/env python3
"""Run one unattended email-scan, with credentials from .env and output to a log.

The CLI deliberately does not read .env, so that credentials stay out of the
application and its state database. This wrapper is the deployment glue: it
loads the file, runs the scan, and appends a timestamped record of the run.

    scripts/run_email_scan.py            # honours [schedule] forward setting
    scripts/run_email_scan.py --preview  # force a preview, send nothing
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp_monitor.config import load_config

LOG = ROOT / "logs" / "email-scan.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
REQUIRED = ("IMAP_HOST", "IMAP_USERNAME", "IMAP_PASSWORD")


def venv_python() -> str:
    """The interpreter that has rfp_monitor installed.

    Never sys.executable: run through its shebang this script starts under
    whichever python3 is first on PATH, which is not the virtualenv and cannot
    import the package.
    """

    for candidate in (
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def load_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, honouring quotes around values that contain spaces."""

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > MAX_LOG_BYTES:
        LOG.replace(LOG.with_suffix(".log.1"))
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--preview", action="store_true", help="never send, whatever the config says"
    )
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    args = parser.parse_args(argv)

    started = datetime.now().astimezone()
    config = load_config(args.config)
    forward = config.schedule.forward and not args.preview

    settings = load_env(ROOT / ".env")
    missing = [name for name in REQUIRED if not settings.get(name)]
    if missing:
        write_log(
            f"\n=== {started:%Y-%m-%d %H:%M:%S %Z} — not run ===\n"
            f"Missing in .env: {', '.join(missing)}. "
            "Add a Gmail app password to IMAP_PASSWORD and this will start working.\n"
        )
        print(f"error: missing in .env: {', '.join(missing)}", file=sys.stderr)
        return 2

    command = [venv_python(), "-m", "rfp_monitor", "email-scan", "--config", args.config]
    if forward:
        command.append("--forward")

    # src on the path keeps this working in a checkout where the package was
    # never installed into the virtualenv.
    existing = os.environ.get("PYTHONPATH", "")
    child_path = str(ROOT / "src") + (os.pathsep + existing if existing else "")

    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **settings, "PYTHONPATH": child_path},
        capture_output=True,
        text=True,
        check=False,
    )
    mode = "forward" if forward else "preview"
    write_log(
        f"\n=== {started:%Y-%m-%d %H:%M:%S %Z} — {mode} — exit {result.returncode} ===\n"
        f"{result.stdout}{result.stderr}"
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
