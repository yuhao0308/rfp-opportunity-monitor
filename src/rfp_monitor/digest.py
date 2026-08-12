from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .models import Alert

ORDER = ("Strong opportunity", "Possible opportunity", "Market intelligence")


def _format_timestamp(value: datetime) -> str:
    """Format a digest timestamp without platform-specific strftime flags."""

    hour = value.hour % 12 or 12
    return (
        f"{value.strftime('%B')} {value.day}, {value.year} at "
        f"{hour}:{value.strftime('%M %p %Z')}"
    )


def _group(alerts: list[Alert]) -> dict[str, list[Alert]]:
    grouped: dict[str, list[Alert]] = defaultdict(list)
    for alert in alerts:
        grouped[alert.match.classification or ""].append(alert)
    return grouped


def render_text(
    alerts: list[Alert], errors: dict[str, str], timezone_name: str
) -> str:
    stamp = _format_timestamp(datetime.now(ZoneInfo(timezone_name)))
    lines = [f"RFP opportunity digest — {stamp}", ""]
    grouped = _group(alerts)
    if not alerts:
        lines.append("No new or materially changed relevant opportunities.")
    for classification in ORDER:
        items = grouped.get(classification, [])
        if not items:
            continue
        lines.extend((classification, "-" * len(classification)))
        for alert in items:
            opportunity = alert.opportunity
            lines.append(f"• {opportunity.title} [{alert.change_kind}]")
            lines.append(f"  {opportunity.source_name} ({opportunity.state})")
            if opportunity.status:
                lines.append(f"  Status: {opportunity.status}")
            if opportunity.due_date:
                lines.append(f"  Due: {opportunity.due_date}")
            if opportunity.agency:
                lines.append(f"  Agency: {opportunity.agency}")
            lines.append(f"  Why: {alert.match.reason}")
            lines.append(f"  Link: {opportunity.detail_url or opportunity.browse_url}")
            lines.append("")
    if errors:
        lines.append("Source warnings")
        lines.append("---------------")
        lines.extend(f"• {name}: {message}" for name, message in errors.items())
    return "\n".join(lines).rstrip() + "\n"


def render_html(
    alerts: list[Alert], errors: dict[str, str], timezone_name: str
) -> str:
    stamp = _format_timestamp(datetime.now(ZoneInfo(timezone_name)))
    grouped = _group(alerts)
    sections: list[str] = [
        "<!doctype html><html><body style=\"font-family:Arial,sans-serif;color:#102a43\">",
        f"<h1 style=\"font-size:22px\">RFP opportunity digest</h1><p>{escape(stamp)}</p>",
    ]
    if not alerts:
        sections.append("<p>No new or materially changed relevant opportunities.</p>")
    for classification in ORDER:
        items = grouped.get(classification, [])
        if not items:
            continue
        sections.append(f"<h2 style=\"font-size:18px\">{escape(classification)}</h2>")
        for alert in items:
            item = alert.opportunity
            link = escape(item.detail_url or item.browse_url, quote=True)
            meta = " · ".join(
                escape(value)
                for value in (item.source_name, item.state, item.status, item.due_date)
                if value
            )
            sections.append(
                "<div style=\"margin:0 0 20px;padding:14px;border:1px solid #d9e2ec;"
                "border-radius:6px\">"
                f"<strong><a href=\"{link}\">{escape(item.title)}</a></strong>"
                f" <small>({escape(alert.change_kind)})</small>"
                f"<p style=\"margin:8px 0\">{meta}</p>"
                f"<p style=\"margin:8px 0\"><strong>Why:</strong> "
                f"{escape(alert.match.reason)}</p></div>"
            )
    if errors:
        sections.append("<h2 style=\"font-size:18px\">Source warnings</h2><ul>")
        sections.extend(
            f"<li><strong>{escape(name)}:</strong> {escape(message)}</li>"
            for name, message in errors.items()
        )
        sections.append("</ul>")
    sections.append("</body></html>")
    return "".join(sections)


def render_json(alerts: list[Alert], errors: dict[str, str]) -> str:
    payload = {
        "alerts": [
            {
                "change": alert.change_kind,
                "classification": alert.match.classification,
                "score": alert.match.score,
                "matched": alert.match.matched,
                "reason": alert.match.reason,
                "opportunity": alert.opportunity.to_dict(),
            }
            for alert in alerts
        ],
        "errors": errors,
    }
    return json.dumps(payload, indent=2, sort_keys=True)
