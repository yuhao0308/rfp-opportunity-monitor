from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote, urljoin, urlparse

from .models import Opportunity, SourceConfig

logger = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """A source could not be scanned safely."""


class AccessChallenge(SourceError):
    """The source requires a human browser check or authorized login."""


_ALIASES = {
    "title": {
        "title",
        "rfx name",
        "solicitation title",
        "request title",
        "sourcing project title",
        "project title",
        "bid title",
    },
    "external_id": {
        "id",
        "rfx id",
        "request id",
        "solicitation id",
        "solicitation number",
        "sourcing project id",
        "project id",
        "bid number",
        "document number",
    },
    "status": {
        "status",
        "project status",
        "request status",
        "solicitation status",
    },
    "due_date": {
        "due date",
        "due close date",
        "bid due date",
        "close date",
        "closing date",
        "publication end date",
        "response deadline",
        "submission deadline",
        "end",
    },
    "published_date": {
        "published date",
        "publication date",
        "publication begin date",
        "issue date",
        "issued date",
        "open date",
        "posting date",
        "begin",
    },
    "category": {
        "category",
        "main category",
        "commodity",
        "commodities",
        "commodity code",
        "link solicitation commodities",
        "unspsc",
    },
    "agency": {
        "agency",
        "issuing agency",
        "organization",
        "department",
        "buyer organization",
        "business unit",
    },
    "solicitation_type": {
        "type",
        "rfx type",
        "request type",
        "project type",
        "public notice type",
        "solicitation type",
    },
}

_DUE_DATE_PRIORITY = {
    "due date": 0,
    "due close date": 0,
    "bid due date": 0,
    "close date": 0,
    "closing date": 0,
    "response deadline": 0,
    "submission deadline": 0,
    "end": 1,
    "publication end date": 2,
}


def _clean(value: object) -> str:
    return " ".join(unescape(str(value or "")).replace("\xa0", " ").split())


def _header_key(value: object) -> str:
    text = re.sub(r"\([^)]*\)", " ", _clean(value).casefold())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _field_indexes(headers: Sequence[object]) -> dict[str, list[int]]:
    indexes: dict[str, list[int]] = {}
    for index, header in enumerate(headers):
        key = _header_key(header)
        for field, aliases in _ALIASES.items():
            if key in aliases:
                indexes.setdefault(field, []).append(index)
                break
    if "due_date" in indexes:
        indexes["due_date"].sort(
            key=lambda index: _DUE_DATE_PRIORITY.get(_header_key(headers[index]), 1)
        )
    if "title" not in indexes:
        visible = ", ".join(_clean(header) for header in headers if _clean(header))
        raise SourceError(f"JAGGAER table has no recognized title column: {visible}")
    return indexes


def _row_parts(row: object) -> tuple[Sequence[object], Sequence[object], str]:
    if isinstance(row, Mapping):
        cells = row.get("cells", ())
        links = row.get("links", ())
        explicit_url = _clean(row.get("detail_url") or row.get("href"))
    else:
        cells, links, explicit_url = row, (), ""
    if isinstance(cells, (str, bytes)) or not isinstance(cells, Sequence):
        raise SourceError("JAGGAER row cells must be a sequence")
    if isinstance(links, (str, bytes)) or not isinstance(links, Sequence):
        links = ()
    return cells, links, explicit_url


def _first_value(cells: Sequence[object], indexes: Sequence[int]) -> str:
    for index in indexes:
        if index < len(cells) and (value := _clean(cells[index])):
            return value
    return ""


def _detail_url(
    source: SourceConfig,
    explicit_url: str,
    links: Sequence[object],
    title_indexes: Sequence[int],
) -> str:
    candidates = [explicit_url]
    candidates.extend(_clean(links[index]) for index in title_indexes if index < len(links))
    candidates.extend(_clean(link) for link in links)
    for candidate in candidates:
        if not candidate or candidate.startswith(("#", "javascript:")):
            continue
        resolved = urljoin(source.url, candidate)
        if urlparse(resolved).scheme in {"http", "https"}:
            return resolved
    return ""


def _dedupe(opportunities: Sequence[Opportunity]) -> list[Opportunity]:
    unique: dict[str, Opportunity] = {}
    for opportunity in opportunities:
        unique.setdefault(opportunity.record_key, opportunity)
    return list(unique.values())


_ALABAMA_TIMEZONES = {
    "CDT": timezone(timedelta(hours=-5), "CDT"),
    "CST": timezone(timedelta(hours=-6), "CST"),
}


def normalize_alabama_date(value: object) -> str:
    """Return an Alabama portal date as ISO 8601 when the format is known."""

    raw = _clean(value)
    if not raw or raw.casefold() in {"noab", "n/a", "none"}:
        return ""
    match = re.fullmatch(
        r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
        r"(?P<time>\d{1,2}:\d{2}\s*(?:am|pm))\s*(?P<zone>CDT|CST)?",
        raw,
        re.IGNORECASE,
    )
    if match:
        date_format = "%m/%d/%Y" if len(match.group("date").split("/")[-1]) == 4 else "%m/%d/%y"
        parsed = datetime.strptime(
            f'{match.group("date")} {match.group("time")}',
            f"{date_format} %I:%M%p",
        ).replace(tzinfo=UTC)
        zone_name = (match.group("zone") or "").upper()
        if zone_name:
            parsed = parsed.replace(tzinfo=_ALABAMA_TIMEZONES[zone_name])
        return parsed.isoformat()
    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            parsed_date = datetime.strptime(raw, date_format).replace(tzinfo=UTC)
            return parsed_date.date().isoformat()
        except ValueError:
            continue
    return raw


def parse_alabama_detail(payload: str) -> dict[str, str]:
    """Parse the public STAARS summary endpoint used by Alabama search results."""

    parts = [_clean(part) for part in payload.split("*")]
    if len(parts) < 10:
        return {}
    return {
        "title": parts[9],
        "due_date": normalize_alabama_date(parts[3]),
        "agency": parts[4],
        "category": parts[7],
        "solicitation_type": parts[8],
    }


def _alabama_detail_url(external_id: str) -> str:
    match = re.fullmatch(r"([A-Za-z]+)-(\d+)\s*-\s*(\S+)", external_id)
    if not match:
        return ""
    query = "@".join((*match.groups(), "1"))
    return (
        "https://procurement.staars.alabama.gov/PRDVSS1X1/advantage/AMSJS/"
        f"searchSolicitation.jsp?query={quote(query)}"
    )


def normalize_alabama_rows(
    source: SourceConfig,
    rows: Sequence[Mapping[str, object]],
) -> list[Opportunity]:
    """Normalize records extracted from Alabama's public professional-RFP search."""

    normalized: list[Opportunity] = []
    for row in rows:
        external_id = _clean(row.get("external_id"))
        title = re.sub(
            r"^description\s*:\s*",
            "",
            _clean(row.get("title")),
            flags=re.IGNORECASE,
        )
        if not title:
            title = external_id
        if not title or not external_id:
            continue
        category_parts = [
            value
            for value in (_clean(row.get("category")), _clean(row.get("subcategory")))
            if value
        ]
        detail_url = _clean(row.get("detail_url")) or _alabama_detail_url(external_id)
        normalized.append(
            Opportunity(
                source_id=source.id,
                source_name=source.name,
                state=source.state,
                browse_url=source.url,
                title=title,
                external_id=external_id,
                detail_url=detail_url,
                status=_clean(row.get("status")),
                due_date=normalize_alabama_date(row.get("due_date")),
                published_date=normalize_alabama_date(row.get("published_date")),
                category=" | ".join(category_parts),
                agency=_clean(row.get("agency")),
                solicitation_type=_clean(row.get("solicitation_type")) or "RFP",
            )
        )
    return _dedupe(normalized)


def next_alabama_page(current: object, page_labels: Sequence[object]) -> int | None:
    """Choose the next sequential ASP.NET grid page without guessing skipped pages."""

    try:
        current_page = int(_clean(current))
    except ValueError:
        return None
    available = set()
    for label in page_labels:
        try:
            available.add(int(_clean(label)))
        except ValueError:
            continue
    return current_page + 1 if current_page + 1 in available else None


def normalize_table_rows(
    source: SourceConfig,
    headers: Sequence[object],
    rows: Sequence[object],
) -> list[Opportunity]:
    """Normalize extracted JAGGAER cells without requiring a browser."""

    indexes = _field_indexes(headers)
    normalized: list[Opportunity] = []
    for row in rows:
        cells, links, explicit_url = _row_parts(row)
        if not any(_clean(cell) for cell in cells):
            continue
        values = {
            field: _first_value(cells, field_indexes) for field, field_indexes in indexes.items()
        }
        title = values.get("title", "")
        if not title:
            continue
        normalized.append(
            Opportunity(
                source_id=source.id,
                source_name=source.name,
                state=source.state,
                browse_url=source.url,
                title=title,
                external_id=values.get("external_id", ""),
                detail_url=_detail_url(
                    source,
                    explicit_url,
                    links,
                    indexes["title"],
                ),
                status=values.get("status", ""),
                due_date=values.get("due_date", ""),
                published_date=values.get("published_date", ""),
                category=values.get("category", ""),
                agency=values.get("agency", ""),
                solicitation_type=values.get("solicitation_type", ""),
            )
        )
    return _dedupe(normalized)


class JaggaerScanner:
    """Context-managed scanner for the configured public procurement portals."""

    _table_selector = "table.iv-grid-view"

    def __init__(
        self,
        *,
        headless: bool = True,
        max_pages: int = 50,
        navigation_timeout_seconds: int = 45,
        profile_path: str | Path | None = None,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 2.0,
        request_delay_seconds: float = 1.0,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if navigation_timeout_seconds < 1:
            raise ValueError("navigation_timeout_seconds must be at least 1")
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be at least 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds cannot be negative")
        self.headless = headless
        self.max_pages = max_pages
        self.timeout_ms = navigation_timeout_seconds * 1_000
        self.profile_path = Path(profile_path).expanduser() if profile_path else None
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.request_delay_seconds = request_delay_seconds
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    def __enter__(self) -> Self:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SourceError("Playwright is required to scan JAGGAER sources") from exc

        try:
            self._playwright = sync_playwright().start()
            chromium = self._playwright.chromium
            if self.profile_path:
                self._context = chromium.launch_persistent_context(
                    str(self.profile_path),
                    headless=self.headless,
                )
            else:
                self._browser = chromium.launch(headless=self.headless)
                self._context = self._browser.new_context()
        except Exception as exc:
            self.close()
            summary = str(exc).splitlines()[0]
            raise SourceError(f"Could not start Chromium: {summary}") from exc
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        for resource in (self._context, self._browser, self._playwright):
            if resource is None:
                continue
            with suppress(Exception):
                resource.close() if resource is not self._playwright else resource.stop()
        self._context = self._browser = self._playwright = None

    def scan(self, source: SourceConfig) -> list[Opportunity]:
        if self._context is None:
            raise SourceError("JaggaerScanner must be used as a context manager")

        if source.adapter not in {"jaggaer", "alabama-rfp"}:
            raise SourceError(f"unknown source adapter: {source.adapter}")

        logger.info(
            "source_scan_start source_id=%s adapter=%s url=%s",
            source.id,
            source.adapter,
            source.url,
        )
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            page = self._context.new_page()
            try:
                page.goto(source.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._raise_for_access_challenge(page, source)
                if source.adapter == "alabama-rfp":
                    opportunities = self._scan_alabama(page, source)
                else:
                    opportunities = self._scan_jaggaer(page, source)
                opportunities = _dedupe(opportunities)
                if not opportunities:
                    raise SourceError("scan returned no opportunities")
                logger.info(
                    "source_scan_success source_id=%s records=%d attempt=%d",
                    source.id,
                    len(opportunities),
                    attempt,
                )
                return opportunities
            except AccessChallenge:
                logger.warning("source_scan_access_challenge source_id=%s", source.id)
                raise
            except Exception as exc:  # noqa: BLE001 - Playwright raises multiple error types
                last_error = exc
                if attempt == self.retry_attempts:
                    break
                delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "source_scan_retry source_id=%s attempt=%d delay_seconds=%.1f error=%s",
                    source.id,
                    attempt,
                    delay,
                    str(exc).splitlines()[0],
                )
                if delay:
                    time.sleep(delay)
            finally:
                page.close()

        assert last_error is not None
        if isinstance(last_error, SourceError):
            raise last_error
        raise SourceError(f"scan failed: {last_error}") from last_error

    def _scan_jaggaer(self, page: Any, source: SourceConfig) -> list[Opportunity]:
        self._wait_for_table(page, source)
        opportunities: list[Opportunity] = []
        for page_number in range(1, self.max_pages + 1):
            self._raise_for_access_challenge(page, source)
            payload = self._read_table(page)
            opportunities.extend(
                normalize_table_rows(source, payload["headers"], payload["rows"])
            )
            if page_number == self.max_pages:
                break
            next_button = self._next_button(page)
            if next_button is None:
                break
            previous = payload["signature"]
            self._respect_request_delay(page)
            next_button.click(timeout=self.timeout_ms)
            self._wait_for_new_page(page, source, previous)
        return opportunities

    def _scan_alabama(self, page: Any, source: SourceConfig) -> list[Opportunity]:
        status_selector = "#MyContent_ddlStatus"
        search_selector = "#MyContent_bttnSearch"
        try:
            page.wait_for_selector(status_selector, state="attached", timeout=self.timeout_ms)
            page.wait_for_selector(search_selector, state="attached", timeout=self.timeout_ms)
            page.select_option(status_selector, value="1")
            self._respect_request_delay(page)
            page.click(search_selector, timeout=self.timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_selector(
                "#MyContent_GridViewRFP, #MyContent_gvSTAARSRFP",
                state="attached",
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            self._raise_for_access_challenge(page, source)
            raise SourceError("Alabama open-RFP search did not load") from exc

        rows: list[Mapping[str, object]] = []
        for table_id in ("MyContent_GridViewRFP", "MyContent_gvSTAARSRFP"):
            if page.locator(f"#{table_id}").count():
                rows.extend(self._scan_alabama_table(page, source, table_id))
        opportunities = normalize_alabama_rows(source, rows)
        return self._enrich_alabama_details(page, source, opportunities)

    def _scan_alabama_table(
        self,
        page: Any,
        source: SourceConfig,
        table_id: str,
    ) -> list[Mapping[str, object]]:
        rows: list[Mapping[str, object]] = []
        for page_number in range(1, self.max_pages + 1):
            self._raise_for_access_challenge(page, source)
            table = page.locator(f"#{table_id}")
            if not table.count():
                break
            rows.extend(self._read_alabama_table(page, table_id))
            pagination = page.eval_on_selector(
                f"#{table_id}",
                r"""
                table => {
                  const pager = table.querySelector("tr.pgr");
                  if (!pager) return null;
                  const current = Number(pager.querySelector("span")?.textContent || "1");
                  const pages = [...pager.querySelectorAll("a")]
                    .map(link => link.href.match(/Page\$(\d+)/)?.[1] || link.textContent.trim());
                  return {current, pages};
                }
                """,
            )
            next_page = (
                next_alabama_page(pagination["current"], pagination["pages"])
                if pagination
                else None
            )
            if next_page is None:
                break
            if page_number == self.max_pages:
                raise SourceError(f"Alabama results exceeded max_pages={self.max_pages}")
            next_link = table.get_by_role("link", name=str(next_page), exact=True)
            if next_link.count() != 1:
                raise SourceError(f"Alabama page {next_page} link was ambiguous")
            previous = table.inner_text()
            self._respect_request_delay(page)
            next_link.click(timeout=self.timeout_ms)
            try:
                page.wait_for_function(
                    """
                    ([id, signature]) => {
                      const table = document.getElementById(id);
                      return table && table.innerText !== signature;
                    }
                    """,
                    arg=[table_id, previous],
                    timeout=self.timeout_ms,
                )
            except Exception as exc:
                self._raise_for_access_challenge(page, source)
                raise SourceError(f"Alabama page {next_page} did not load") from exc
        return rows

    @staticmethod
    def _read_alabama_table(page: Any, table_id: str) -> list[Mapping[str, object]]:
        rows = page.eval_on_selector(
            f"#{table_id}",
            r"""
            table => {
              const isStaars = table.id === "MyContent_gvSTAARSRFP";
              return [...table.querySelectorAll("tr")].map(row => {
                const cells = [...row.querySelectorAll(":scope > td")];
                if (!cells.length || row.classList.contains("pgr")) return null;
                const text = index => (cells[index]?.innerText || "").trim();
                const description = (row.querySelector("div[id='tooltip']")?.innerText || "")
                  .replace(/^Description:\s*/i, "").trim();
                if (isStaars) {
                  if (cells.length < 6) return null;
                  return {
                    external_id: text(0), title: description, agency: text(2),
                    status: text(3), category: text(4), solicitation_type: "RFP"
                  };
                }
                if (cells.length < 9) return null;
                const agencyLink = cells[3]?.querySelector("a[href^='http']");
                return {
                  external_id: text(0), title: description, agency: text(2),
                  detail_url: agencyLink?.href || "", status: text(5),
                  category: text(6), subcategory: text(7), solicitation_type: "RFP"
                };
              }).filter(Boolean);
            }
            """,
        )
        return rows or []

    def _enrich_alabama_details(
        self,
        page: Any,
        source: SourceConfig,
        opportunities: Sequence[Opportunity],
    ) -> list[Opportunity]:
        enriched: list[Opportunity] = []
        detail_candidates = [
            item for item in opportunities if "searchSolicitation.jsp" in item.detail_url
        ]
        detail_limit = 25
        if len(detail_candidates) > detail_limit:
            logger.warning(
                "alabama_detail_limit source_id=%s available=%d limit=%d",
                source.id,
                len(detail_candidates),
                detail_limit,
            )
        allowed_keys = {item.record_key for item in detail_candidates[:detail_limit]}
        for opportunity in opportunities:
            if opportunity.record_key not in allowed_keys:
                enriched.append(opportunity)
                continue
            self._respect_request_delay(page)
            try:
                response = self._context.request.get(
                    opportunity.detail_url,
                    timeout=self.timeout_ms,
                )
                if not response.ok:
                    raise SourceError(f"detail HTTP {response.status}")
                detail = parse_alabama_detail(response.text())
            except Exception as exc:  # noqa: BLE001 - detail failure must not fail the source
                logger.warning(
                    "alabama_detail_failed source_id=%s external_id=%s error=%s",
                    source.id,
                    opportunity.external_id,
                    str(exc).splitlines()[0],
                )
                enriched.append(opportunity)
                continue
            enriched.append(
                replace(
                    opportunity,
                    title=detail.get("title") or opportunity.title,
                    due_date=detail.get("due_date") or opportunity.due_date,
                    agency=detail.get("agency") or opportunity.agency,
                    category=opportunity.category or detail.get("category", ""),
                    solicitation_type=(
                        detail.get("solicitation_type") or opportunity.solicitation_type
                    ),
                )
            )
        return enriched

    def _respect_request_delay(self, page: Any) -> None:
        if self.request_delay_seconds:
            page.wait_for_timeout(self.request_delay_seconds * 1_000)

    def _wait_for_table(self, page: Any, source: SourceConfig) -> None:
        try:
            page.wait_for_selector(
                self._table_selector,
                state="attached",
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            self._raise_for_access_challenge(page, source)
            raise SourceError("opportunity table was not found") from exc

    def _raise_for_access_challenge(self, page: Any, source: SourceConfig) -> None:
        reason = self._access_challenge_reason(page)
        if reason:
            raise AccessChallenge(f"{reason}; complete it manually with authorized access")

    @staticmethod
    def _access_challenge_reason(page: Any) -> str:
        title = _clean(page.title()).casefold()
        body = _clean(page.locator("body").inner_text(timeout=2_000)).casefold()
        has_captcha = page.locator(
            "iframe[src*='recaptcha' i], iframe[title*='recaptcha' i], .g-recaptcha, [data-sitekey]"
        ).count()
        has_password = page.locator("input[type='password']").count()

        if has_captcha or any(
            marker in title or marker in body
            for marker in (
                "browser check",
                "verify that you are not a robot",
                "verify you are human",
                "i'm not a robot",
                "recaptcha",
            )
        ):
            return "browser verification is required"

        path = urlparse(getattr(page, "url", "")).path.casefold()
        login_path = re.search(r"/(?:login|sign-in|signin)(?:[/.]|$)", path)
        login_text = any(
            marker in body
            for marker in (
                "sign in to continue",
                "please log in to continue",
                "login required",
                "authentication required",
            )
        )
        if has_password or login_path or login_text:
            return "login is required"
        return ""

    @staticmethod
    def _read_table(page: Any) -> dict[str, object]:
        payload = page.eval_on_selector_all(
            "table.iv-grid-view",
            """
            tables => {
              const table = [...tables].sort(
                (a, b) => b.querySelectorAll("tbody tr").length
                        - a.querySelectorAll("tbody tr").length
              )[0];
              if (!table) return null;
              let headers = [...table.querySelectorAll("thead th")]
                .map(cell => cell.innerText.trim());
              if (!headers.length) {
                headers = [...table.querySelectorAll("tr th")]
                  .map(cell => cell.innerText.trim());
              }
              const rows = [...table.querySelectorAll("tbody tr")]
                .map(row => {
                  const cells = [...row.querySelectorAll(":scope > th, :scope > td")];
                  return {
                    cells: cells.map(cell => cell.innerText.trim()),
                    links: cells.map(cell => {
                      const link = cell.querySelector("a[href]");
                      return link ? link.getAttribute("href") : "";
                    }),
                  };
                })
                .filter(row => row.cells.length);
              return {headers, rows, signature: table.innerText.trim()};
            }
            """,
        )
        if not payload:
            raise SourceError("JAGGAER opportunity table disappeared")
        return payload

    @staticmethod
    def _next_button(page: Any) -> Any | None:
        candidates = (
            page.get_by_role(
                "button",
                name=re.compile(r"^next(?: page)?$", re.IGNORECASE),
            ),
            page.get_by_role(
                "link",
                name=re.compile(r"^next(?: page)?$", re.IGNORECASE),
            ),
            page.locator("[aria-label='Next page' i], [title='Next page' i], a[rel='next']"),
        )
        for candidate in candidates:
            for index in range(candidate.count()):
                button = candidate.nth(index)
                class_name = (button.get_attribute("class") or "").casefold()
                disabled = button.get_attribute("aria-disabled") == "true"
                if (
                    button.is_visible()
                    and button.is_enabled()
                    and not disabled
                    and "disabled" not in class_name
                ):
                    return button
        return None

    def _wait_for_new_page(
        self,
        page: Any,
        source: SourceConfig,
        previous_signature: str,
    ) -> None:
        try:
            page.wait_for_function(
                """
                previous => {
                  const tables = [...document.querySelectorAll("table.iv-grid-view")];
                  const table = tables.sort(
                    (a, b) => b.querySelectorAll("tbody tr").length
                            - a.querySelectorAll("tbody tr").length
                  )[0];
                  return table && table.innerText.trim() !== previous;
                }
                """,
                arg=previous_signature,
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            self._raise_for_access_challenge(page, source)
            raise SourceError("next page did not load") from exc
