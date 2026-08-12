from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from smtplib import SMTPException

from .config import load_config
from .digest import render_html, render_json, render_text
from .emma_email import SOURCE_ID, SOURCE_NAME, EmmaEmailError, parse_emma_email
from .mailbox import MailboxError, fetch_raw_messages, read_eml_directory
from .mailer import forward_notice, send_digest
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

    prototype = subparsers.add_parser(
        "prototype",
        help="Collect a small normalized sample without changing monitor state",
    )
    prototype.add_argument("--config", default="config.toml")
    prototype.add_argument("--source", default="alabama")
    prototype.add_argument("--output", default="var/prototype-output.json")
    prototype.add_argument("--limit", type=int, default=5)
    prototype.add_argument("--headed", action="store_true")

    email_scan = subparsers.add_parser(
        "email-scan",
        help="Read portal notification email, match it, and forward what qualifies",
    )
    email_scan.add_argument("--config", default="config.toml")
    email_scan.add_argument("--state", help="Override the SQLite state path")
    email_scan.add_argument("--folder", help="Override the IMAP folder")
    email_scan.add_argument("--since-days", type=int, help="Override the fetch window")
    email_scan.add_argument(
        "--from-dir",
        help="Read saved .eml files instead of connecting to IMAP",
    )
    email_scan.add_argument(
        "--forward-to",
        action="append",
        dest="forward_to",
        help="Override the forward recipient (repeatable)",
    )
    email_scan.add_argument(
        "--reprocess",
        action="store_true",
        help="Ignore the processed-message and seen-record history",
    )
    email_scan.add_argument("--json", action="store_true", dest="as_json")
    email_scan.add_argument(
        "--forward",
        action="store_true",
        help="Actually send the forwards (default is a preview)",
    )

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


def _scanner_options(monitor: object) -> dict[str, object]:
    return {
        "headless": monitor.headless,
        "max_pages": monitor.max_pages,
        "navigation_timeout_seconds": monitor.navigation_timeout_seconds,
        "profile_path": monitor.profile_path,
        "retry_attempts": monitor.retry_attempts,
        "retry_backoff_seconds": monitor.retry_backoff_seconds,
        "request_delay_seconds": monitor.request_delay_seconds,
    }


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
        **_scanner_options(monitor)
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


def _prototype(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    config = load_config(args.config)
    sources = _selected_sources(config, [args.source])
    monitor = config.monitor
    if args.headed:
        monitor = replace(monitor, headless=False)
    with JaggaerScanner(**_scanner_options(monitor)) as scanner:
        opportunities = scanner.scan(sources[0])[: args.limit]

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "collected_at": datetime.now(UTC).isoformat(),
        "source": sources[0].id,
        "count": len(opportunities),
        "records": [opportunity.to_dict() for opportunity in opportunities],
    }
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    print(f"Saved {len(opportunities)} normalized record(s) to {output_path}")
    return 0


def _email_scan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    monitor = config.monitor
    if args.state:
        monitor = replace(monitor, state_path=Path(args.state).resolve())

    settings = config.email
    if args.folder:
        settings = replace(settings, folder=args.folder)
    if args.since_days is not None:
        settings = replace(settings, since_days=args.since_days)
    if args.forward_to:
        settings = replace(
            settings,
            forward_to=tuple(address for address in args.forward_to if address.strip()),
        )
    if args.forward and not settings.forward_to:
        raise ValueError("--forward requires email.forward_to in config or --forward-to")

    if args.from_dir:
        raw_messages = read_eml_directory(args.from_dir)
        origin = args.from_dir
    else:
        raw_messages = fetch_raw_messages(
            folder=settings.folder,
            senders=settings.senders,
            since_days=settings.since_days,
            limit=settings.max_messages,
        )
        origin = f"{settings.folder} (last {settings.since_days} day(s))"

    matcher = Matcher(load_keyword_library(monitor.keyword_path))
    message_parser = BytesParser(policy=policy.default)
    decisions: list[dict[str, object]] = []
    errors: dict[str, str] = {}

    with StateStore(monitor.state_path) as state:
        for raw in raw_messages:
            message = message_parser.parsebytes(raw)
            message_id = str(message.get("Message-ID", "")).strip()
            subject = " ".join(str(message.get("Subject", "")).split())

            if not args.reprocess and state.email_is_processed(SOURCE_ID, message_id):
                decisions.append(
                    {"outcome": "already processed", "subject": subject, "forwarded": False}
                )
                continue

            try:
                notice = parse_emma_email(message, allowed_senders=settings.senders)
            except EmmaEmailError as exc:
                decisions.append(
                    {"outcome": f"not an eMMA notice ({exc})", "subject": subject,
                     "forwarded": False}
                )
                if args.forward:
                    state.mark_email_processed(
                        SOURCE_ID, message_id, subject=subject, outcome="unparsed"
                    )
                continue

            opportunity = notice.to_opportunity()
            change = state.record_opportunity(SOURCE_ID, opportunity, persist=False)
            match = matcher.match(opportunity)

            if change is None and not args.reprocess:
                outcome = "unchanged since the last alert"
            elif not match.is_relevant:
                outcome = "not relevant"
            else:
                outcome = "forward"

            entry: dict[str, object] = {
                "outcome": outcome,
                "subject": subject,
                "change": change.kind if change else "none",
                "classification": match.classification,
                "score": match.score,
                "matched": {group: list(terms) for group, terms in match.matched.items()},
                "reason": match.reason,
                "notice": notice.to_dict(),
                "forwarded": False,
            }

            if args.forward:
                try:
                    if outcome == "forward":
                        forward_notice(
                            settings.forward_to,
                            notice,
                            match,
                            change.kind if change else "new",
                            message,
                            subject_prefix=config.notifications.subject_prefix,
                        )
                        entry["forwarded"] = True
                except (OSError, SMTPException, ValueError) as exc:
                    # Leave state untouched so the next run retries this message.
                    errors[notice.rfx_name or subject] = f"forward failed: {exc}"
                    entry["outcome"] = f"forward failed: {exc}"
                    decisions.append(entry)
                    continue
                state.record_opportunity(SOURCE_ID, opportunity, persist=True)
                state.mark_email_processed(
                    SOURCE_ID, message_id, subject=subject, outcome=outcome
                )
            decisions.append(entry)

    if args.as_json:
        print(
            json.dumps(
                {"source": SOURCE_NAME, "origin": origin, "mode":
                 "forward" if args.forward else "preview",
                 "decisions": decisions, "errors": errors},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_render_email_report(origin, decisions, errors, forwarding=args.forward), end="")
    return 2 if errors else 0


def _render_email_report(
    origin: str,
    decisions: list[dict[str, object]],
    errors: dict[str, str],
    *,
    forwarding: bool,
) -> str:
    mode = "forwarding" if forwarding else "preview (no email sent)"
    lines = [
        f"{SOURCE_NAME} — {len(decisions)} message(s) from {origin} [{mode}]",
        "",
    ]
    if not decisions:
        lines.append("No messages matched the fetch window and sender filter.")
    for entry in decisions:
        notice = entry.get("notice") or {}
        title = notice.get("rfx_name") or entry.get("subject") or "(no subject)"
        outcome = str(entry["outcome"])
        marker = "FORWARD" if outcome == "forward" else "skip   "
        if entry.get("forwarded"):
            marker = "SENT   "
        label = entry.get("classification") or "Not relevant"
        lines.append(f"{marker} {title}")
        detail = f"        {label}"
        if entry.get("score") is not None:
            detail += f" · score {entry['score']}"
        if notice.get("bpm_id"):
            detail += f" · BPM {notice['bpm_id']}"
        if notice.get("end_date"):
            detail += f" · ends {notice['end_date']}"
        lines.append(detail)
        reason = "" if outcome.startswith("forward failed") else entry.get("reason", "")
        lines.append(f"        {outcome}: {reason}".rstrip(": "))
        lines.append("")
    if errors:
        lines.append("Warnings")
        lines.append("--------")
        lines.extend(f"• {name}: {message}" for name, message in errors.items())
    return "\n".join(lines).rstrip() + "\n"


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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s level=%(levelname)s event=%(message)s",
    )
    args = _parser().parse_args(argv)
    try:
        if args.command == "scan":
            return _scan(args)
        if args.command == "prototype":
            return _prototype(args)
        if args.command == "email-scan":
            return _email_scan(args)
        return _classify(args)
    except (SourceError, MailboxError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
