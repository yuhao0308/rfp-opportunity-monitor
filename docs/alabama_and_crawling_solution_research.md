# Alabama source and crawling solution research

Research and live verification date: August 2, 2026.

## Executive recommendation

Use the official Alabama professional-services RFP search at
[`https://rfp.alabama.gov/PublicView.aspx`](https://rfp.alabama.gov/PublicView.aspx), not
AlabamaBuys or BidNet Direct. Keep the project's existing local Playwright runtime and add a
deterministic Alabama adapter. It is the lowest-cost and smallest production change, and the live
prototype successfully collected 11 open records without a login, CAPTCHA, proxy, or paid service.

Use ordinary HTTP only where Alabama exposes a stable public response. The adapter does this for
the small STAARS solicitation-summary endpoint after it obtains identifiers from the official
search UI. If managing Chromium becomes an operational burden, Browserbase is the best managed
runtime to evaluate because it accepts the existing Playwright code. Do not use managed anti-bot,
proxy, or CAPTCHA-solving features to defeat a site's access controls.

## Existing repository assessment

The repository is a Python 3.11 CLI. Before this change it had one synchronous Playwright adapter
for North Dakota and Maryland JAGGAER listings, a normalized `Opportunity` model, SQLite
deduplication and change detection, keyword classification, text/HTML/JSON digests, and optional
SMTP delivery. It already supported a persistent Playwright profile, challenge detection, a silent
first baseline, and per-source failure isolation.

There was no separate HTTP client, configured proxy, credential/login flow, retry/backoff policy, or
managed-browser integration. Parsing read JAGGAER table headers and rows in the browser and mapped
them to title, external ID, status, due/published dates, category, agency, solicitation type, and
source/detail URLs. SMTP credentials come only from scheduler environment variables; browser state
can optionally live in a configured local profile path.

The main gaps were source-specific adapter selection, retries and pacing, structured operational
logs, an Alabama source, and a state-free way to save sample normalized output. The change keeps the
current CLI, schema, matcher, database, and email flow rather than introducing a second crawler
application.

## Alabama source assessment

### Sources checked

| Source | Finding | Decision |
|---|---|---|
| [Alabama Public RFP Search](https://rfp.alabama.gov/PublicView.aspx) | Official state search for professional-service RFPs. The `Open` filter returned current records from many agencies. No login or CAPTCHA appeared in repeated low-rate tests. | Integrated. |
| [STAARS Vendor Self-Service](https://procurement.staars.alabama.gov/webapp/PRDVSS1X1/AltSelfService) | The page says business opportunities and awards may be viewed without an account through **Public Access**. Alabama's [VSS explanation](https://procurement.staars.alabama.gov/LoginExternal/Pages/what_is_vss.htm) says the same. | Used only for public solicitation summaries linked from the official search. |
| [AlabamaBuys public browse](https://www.alabamabuys.gov/page.aspx/en/rfp/request_browse_public) | Redirected to a Google reCAPTCHA Enterprise browser check. Its [`robots.txt`](https://www.alabamabuys.gov/robots.txt) says `Disallow: /` for every user agent. | Not accessed by the adapter. No bypass attempted. |
| BidNet Direct | A commercial aggregator that mirrors some Alabama records and was probably the site seen in the meeting. Some identifying fields are paywalled and it is not the system of record. | Not integrated. |

The meeting transcript mentioned “BidDirect,” the U.S. Space & Rocket Center, and an executive
recruiting RFP. That most likely referred to a BidNet Direct result rather than an Alabama-owned
portal. The official state search includes the Space Science Exhibit Commission and Finance
Authority among its agencies, so it is the closer match to the project's state-system-of-record
approach. This identification is a reasoned conclusion, not a confirmed statement from the meeting
participants.

The official [VSS guide](https://procurement.staars.alabama.gov/LoginExternal/Forms/VSSGuide.pdf)
documents anonymous solicitation search and says details may include the agency, buyer information,
response options, attachments, and additional information. Vendor registration is still required
to submit bids; public viewing does not imply permission to submit or access vendor-only data.

### Technology and access behavior

`rfp.alabama.gov` is an ASP.NET Web Forms application. Search criteria are submitted with session
cookies plus `__VIEWSTATE` and `__EVENTVALIDATION`, so a direct stateless GET cannot reliably apply
the `Open` filter. A deterministic browser interaction is appropriate here: load the page, select
`Open`, submit the search, and read the two result tables. It is not an AI-driven navigation task.

Some records originate in STAARS. Their public summary URLs return small, server-rendered,
asterisk-delimited responses. The adapter reads at most 25 of these per run and waits between
requests. No usable public JSON API was found. Neither `rfp.alabama.gov` nor the STAARS host
published a `robots.txt` policy at the standard path during testing; absence of a robots file is not
blanket permission, so the adapter still uses conservative limits.

### Fields available

The public search exposes:

- title/description;
- state or STAARS RFP identifier;
- issuing agency;
- status;
- category and, for state-managed records, subcategory;
- agency or STAARS source link.

The public STAARS summary additionally exposes the response deadline and solicitation type. Dates
are normalized to ISO 8601, including the stated `CDT` or `CST` offset. The search results do not
consistently expose publication/opening date, contact, attachments, or amendments. Those fields are
left empty rather than guessed. Legacy agency records may only link to an agency homepage, so the
official Alabama search remains the `browse_url`.

### Compliance and operational limits

The [Alabama.gov privacy policy](https://www.alabama.gov/privacy-policy.html) says network traffic is
monitored and describes state public records, but it does not grant unrestricted automated access
to every agency system. This assessment is technical, not legal advice. Production use should:

- collect only public solicitation metadata;
- run at low frequency (the intended schedule is once daily);
- keep the configured one-second delay and bounded exponential retries;
- identify and review sustained errors instead of rotating identities or proxies;
- stop and report any CAPTCHA, login requirement, block page, or unexpected pagination failure;
- obtain written permission or a supported feed before automating any authenticated area.

## Tool comparison

Prices and features below are the public values observed on August 2, 2026 and can change.

| Tool | Dynamic JS | Persistent sessions / auth | Access-challenge posture | Cost at small scale | Integration and reliability |
|---|---|---|---|---|---|
| [Local Playwright](https://playwright.dev/docs/auth) | Excellent | Storage state and persistent contexts; sensitive state must stay out of Git | Detect and stop; no built-in CAPTCHA resolution | Open source; only host/runtime cost | Already installed and tested. Lowest effort and full deterministic control. The team owns browser updates and monitoring. |
| [Browserbase](https://www.browserbase.com/pricing) | Excellent; remote Playwright, Puppeteer, Selenium, and Stagehand | Managed browser sessions and observability | Paid plans advertise CAPTCHA/identity and proxy features; those should not be used to evade controls | Free: 1 browser hour/month; Developer: $20/month with 100 hours, then usage charges | Small code change because Playwright can connect remotely. Strong managed-runtime option when local Chromium operations become costly. |
| [Asteroid](https://asteroid.ai/pricing/) | Excellent; AI tasks can mix browser actions, Playwright, API calls, and graph steps | [Profiles](https://docs.asteroid.ai/fundamentals/profiles) support credentials and cookie persistence | Product supports proxy/CAPTCHA configuration and human oversight. Use only for authorized workflows and escalation, not bypass. | Individual is pay-as-you-go; browser runtime $0.12/hour plus $0.01/session, model usage, and optional proxy traffic. Startup is $300/month. | Good for complex, changing, authenticated workflows. More platform and AI complexity than a stable public search form needs. |
| [Crawlee for Python](https://crawlee.dev/python/docs/guides/session-management) | Good through `PlaywrightCrawler`; also supports HTTP-first crawlers | Session pools can persist cookies and bind requests to a session | Can detect blocked sessions and stop/retire them; proxy rotation exists but is not recommended here | Open source; host/runtime cost | Strong future framework if the project grows to many heterogeneous sources. Migrating now would add dependencies and rewrite working orchestration. |
| [Browserless](https://www.browserless.io/pricing) | Excellent; remote Playwright/Puppeteer and browser APIs | Session reconnects and persisted sessions/logs by plan | Product advertises automatic CAPTCHA handling; do not enable it for a site that denies automation | Free: 1,000 units/month and one-minute sessions; Prototyping starts at $25/month when billed annually | Easy remote-browser substitution and self-hosting is available at enterprise level. Unit/session limits are less natural than current daily local runs. |
| [Apify](https://apify.com/pricing) | Excellent through Actors/Crawlee/Playwright | Actor storage, request queues, datasets, and scheduled runs | Proxy features are available, but blocked public sources should be escalated rather than evaded | Free includes $5 monthly usage; Starter is $29/month plus usage | Mature scheduling, storage, and observability. Useful at larger scale, but duplicates the project's SQLite, scheduler, and email infrastructure today. |

### Operational fit details

- **Local Playwright:** supports downloads, screenshots, traces, request interception, multiple
  contexts, and explicit timeouts. Concurrency and rate limiting remain application responsibilities.
  It has no platform lock-in, but browser binaries and secrets/state files must be patched and
  protected by the operator.
- **Browserbase:** adds managed concurrency, recordings, logs, regional browsers, and remote session
  infrastructure while retaining Playwright compatibility. Downloads are supported through the
  browser workflow. Security review must cover sending page content and authentication state to a
  third party. Runtime portability is good, but recordings, identity features, and platform APIs
  create moderate vendor lock-in.
- **Asteroid:** provides API-triggered graph agents, mixed deterministic/AI nodes, profiles, file
  handling, execution traces, concurrency, and human review. Credentials and page data live in a
  managed platform, and agent graphs/model behavior create higher lock-in. Its adaptability is most
  useful for authorized, changing, multi-step portals—not an eleven-row public search.
- **Crawlee:** supplies request queues, retries, concurrency controls, rate limits, browser pools,
  sessions, logging, and HTTP/browser routing. Downloads can be handled in its Playwright layer.
  Self-hosting keeps security and lock-in risks low, but adopting it now would replace small,
  already-tested orchestration with a larger framework.
- **Browserless:** supports remote Playwright/Puppeteer, reconnectable sessions, downloads,
  recordings on paid plans, concurrency limits, and several deployment models. A remote endpoint is
  easy to substitute, but its unit accounting and proprietary BrowserQL/managed features introduce
  moderate operational and vendor coupling. Auth state and downloads must be protected.
- **Apify:** adds scheduled Actors, datasets, queues, run logs, retries, limits, proxy configuration,
  secrets, and file/key-value storage. It is well suited to a larger crawling program. Moving this
  small application into Actors would increase deployment complexity and platform lock-in without
  improving compliant access to Maryland or AlabamaBuys.

### Terms that should not be conflated

| Capability | Meaning in this project |
|---|---|
| Normal browser automation | Deterministic navigation, selection, clicks, and DOM reads that a public page requires. |
| Session persistence | Reusing cookies/storage so a permitted session survives across steps or runs. It does not grant access. |
| Authentication | Using an authorized account to access data the account owner may view. It requires owner approval and secure credential handling. |
| Proxy networking | Routing traffic through another network. It can be legitimate for infrastructure or geography, but must not be used to evade a block. |
| CAPTCHA handling | Detect, stop, and request authorized human action or a supported feed. Automated solving is outside this project. |
| Anti-bot evasion | Concealing automation, rotating residential identities, or defeating controls. This is neither implemented nor recommended. |

## Recommended architecture

1. Each configured source declares an adapter. The current `jaggaer` adapter remains the default;
   Alabama uses `alabama-rfp`.
2. The shared scanner starts one Playwright context, applies bounded retries and pacing, and routes
   the source to deterministic adapter code.
3. The Alabama adapter uses the browser only for the stateful ASP.NET search and pagination. It uses
   public HTTP responses only for a bounded number of STAARS summaries.
4. Both adapters return the existing `Opportunity` schema. SQLite, change detection, matching, email,
   and failure isolation remain unchanged.
5. The `prototype` command skips SQLite and email, collects a small sample, and atomically saves JSON
   only after a successful scan.
6. A future managed browser should sit behind the existing scanner boundary. Browserbase is the first
   candidate; switching runtimes should not change source parsers or normalized records.

This structure avoids a generalized autonomous crawler. Procurement portals are different enough
that small deterministic adapters are easier to test, audit, and repair.

## Prototype and verification

Run the live sample collector:

```bash
.venv/bin/rfp-monitor prototype \
  --source alabama \
  --limit 5 \
  --output examples/alabama_sample.json
```

The August 2 run returned 11 open Alabama records on the first attempt and saved five normalized
records. The checked-in sample includes identifiers, titles, agencies, categories, links, status,
and a normalized STAARS deadline where available. It does not update SQLite and cannot send email.

Unit tests cover representative Alabama rows, duplicate suppression, malformed rows and detail
responses, date normalization, existing JAGGAER behavior, and scanner limit validation. Live portal
access is deliberately not part of the test suite.

No Alabama-specific environment variables or credentials are required. The existing optional
`monitor.profile_path` applies to the shared browser, but the current Alabama route does not need a
saved session.

### Troubleshooting

- `Alabama open-RFP search did not load`: retry once manually with `--headed` to inspect the public
  page. If it shows verification or login, stop and contact the portal owner; do not automate the
  challenge.
- `Alabama results exceeded max_pages`: raise `monitor.max_pages` only after confirming the site is
  still returning the expected open-results grid and that the daily request count remains modest.
- `alabama_detail_failed` in logs: the listing record is still returned with its public title,
  agency, status, and category; only summary enrichment is missing.
- `Unknown or disabled source(s): alabama`: confirm the Alabama block is present and enabled in
  `config.toml`.
- Browser launch errors: rerun `playwright install chromium` in the same virtual environment.

## Remaining risks and next steps

- Ask Alabama procurement whether a supported export/feed exists and whether this low-frequency
  metadata collection is acceptable for production use.
- Validate agency links manually for the four non-STAARS records currently marked open; one record
  has an identifier from 2021 and may be stale even though the official status is `Open`.
- Add saved HTML fixtures if Alabama changes table markup, and alert on selector or pagination drift.
- Consider a Browserbase proof of concept only if scheduler hosts cannot reliably run Chromium or
  if source count/concurrency grows. It is not needed for the current Alabama source.
- Do not add AlabamaBuys automation unless its owner provides an approved feed or explicit written
  permission and an access path that does not require bypassing its CAPTCHA or robots policy.
