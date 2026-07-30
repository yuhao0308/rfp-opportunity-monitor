from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .digest import render_html, render_json, render_text
from .mailer import send_digest
from .matching import Matcher, load_keyword_library
from .models import Alert, Opportunity
from .source import JaggaerScanner, SourceError
from .state import StateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfp-monitor",
        description="Monitor public procurement portals for relevant RFPs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan enabled sources")
    scan.add_argument("--config", default="config.toml")
    scan.add_argument("--state", help="Override the SQLite state path")
    scan.add_argument("--source", action="append", dest="source_ids")
    scan.add_argument("--include-baseline", action="store_true")
    scan.add_argument("--headed", action="store_true")
    scan.add_argument("--max-pages", type=int)
    scan.add_argument("--json", action="store_true", dest="as_json")
    scan.add_argument("--send", action="store_true", help="Send the SMTP digest")

    classify = subparsers.add_parser("classify", help="Explain one candidate title")
    classify.add_argument("text")
    classify.add_argument("--status", default="")
    classify.add_argument("--due-date", default="")
    classify.add_argument("--config", default="config.toml")
    classify.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _selected_sources(config: object, source_ids: list[str] | None):
    sources = [source for source in config.sources if source.enabled]
    if source_ids:
        requested = set(source_ids)
        available = {source.id for source in sources}
        missing = requested - available
        if missing:
            raise ValueError(f"Unknown or disabled source(s): {', '.join(sorted(missing))}")
        sources = [source for source in sources if source.id in requested]
    return sources


def _scan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    monitor = config.monitor
    if args.state:
        monitor = replace(monitor, state_path=Path(args.state).resolve())
    if args.headed:
        monitor = replace(monitor, headless=False)
    if args.max_pages:
        monitor = replace(monitor, max_pages=args.max_pages)

    sources = _selected_sources(config, args.source_ids)
    matcher = Matcher(load_keyword_library(monitor.keyword_path))
    alerts: list[Alert] = []
    errors: dict[str, str] = {}

    with StateStore(monitor.state_path) as state, JaggaerScanner(
        headless=monitor.headless,
        max_pages=monitor.max_pages,
        navigation_timeout_seconds=monitor.navigation_timeout_seconds,
        profile_path=monitor.profile_path,
    ) as scanner:
        for source in sources:
            was_initialized = state.source_is_initialized(source.id)
            try:
                opportunities = scanner.scan(source)
            except SourceError as exc:
                errors[source.name] = str(exc)
                print(f"{source.name}: scan failed: {exc}", file=sys.stderr)
                continue

            changes = state.process_successful_scan(
                source, opportunities, include_baseline=args.include_baseline
            )
            mode = "updates" if was_initialized or args.include_baseline else "silent baseline"
            print(
                f"{source.name}: {len(opportunities)} records, "
                f"{len(changes)} changes ({mode})",
                file=sys.stderr,
            )
            for change in changes:
                match = matcher.match(change.opportunity)
                if match.is_relevant:
                    alerts.append(
                        Alert(
                            change_kind=change.kind,
                            opportunity=change.opportunity,
                            match=match,
                        )
                    )

    if args.as_json:
        print(render_json(alerts, errors))
    else:
        print(render_text(alerts, errors, config.notifications.timezone), end="")

    if args.send and (alerts or errors):
        text = render_text(alerts, errors, config.notifications.timezone)
        html = render_html(alerts, errors, config.notifications.timezone)
        subject = f"{len(alerts)} relevant update(s)"
        send_digest(config.notifications, subject, text, html)
        print("Digest sent.", file=sys.stderr)
    return 2 if errors else 0


def _classify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    matcher = Matcher(load_keyword_library(config.monitor.keyword_path))
    opportunity = Opportunity(
        source_id="manual",
        source_name="Manual check",
        state="",
        browse_url="",
        title=args.text,
        status=args.status,
        due_date=args.due_date,
    )
    match = matcher.match(opportunity)
    if args.as_json:
        print(render_json([Alert("manual", opportunity, match)], {}))
    else:
        label = match.classification or "Not relevant"
        print(f"{label} (score {match.score})")
        print(match.reason or "No qualifying signal combination was found.")
        if match.matched:
            for group, terms in match.matched.items():
                print(f"{group}: {', '.join(terms)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "scan":
            return _scan(args)
        return _classify(args)
    except (SourceError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
