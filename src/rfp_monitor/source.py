from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from html import unescape
from pathlib import Path
from typing import Any, Self
from urllib.parse import urljoin, urlparse

from .models import Opportunity, SourceConfig


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
    """Context-managed scanner for the public JAGGAER listing pages."""

    _table_selector = "table.iv-grid-view"

    def __init__(
        self,
        *,
        headless: bool = True,
        max_pages: int = 50,
        navigation_timeout_seconds: int = 45,
        profile_path: str | Path | None = None,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if navigation_timeout_seconds < 1:
            raise ValueError("navigation_timeout_seconds must be at least 1")
        self.headless = headless
        self.max_pages = max_pages
        self.timeout_ms = navigation_timeout_seconds * 1_000
        self.profile_path = Path(profile_path).expanduser() if profile_path else None
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

        page = self._context.new_page()
        try:
            page.goto(source.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._raise_for_access_challenge(page, source)
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
                next_button.click(timeout=self.timeout_ms)
                self._wait_for_new_page(page, source, previous)
        except AccessChallenge:
            raise
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"scan failed: {exc}") from exc
        finally:
            page.close()

        opportunities = _dedupe(opportunities)
        if not opportunities:
            raise SourceError("scan returned no opportunities")
        return opportunities

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
