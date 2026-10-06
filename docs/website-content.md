# Website content: survey, crawl, clean and refresh

For a project whose content is the client's public website (AD-24). The pipeline is
`snapshot.py` (fetch raw HTML), `clean.py` (HTML to Markdown), then the usual `ingest.py` or
`publish.py`; in the cloud, `refresh.py` keeps it current from the sitemap. It was proven on the
first project (ea-customer-bot), then made general here. Content from a CMS export or documents
skips all of this: put the Markdown in `CONTENT_DIR` (README, "Content").

**A project made before the crawler joined the starter** can bring it in:
[updating-from-the-starter.md](updating-from-the-starter.md).

**First time?** Work through [the tutorial](website-content-tutorial.md): every step below, on
Crystal Delta's own site, with the output you should see. This page is the reference.

The order:

1. [Survey the site](#1-survey-the-site): a quick scan to learn whether, and how, it can be crawled.
2. [Get written permission](#2-get-written-permission) for the scope the survey showed.
3. [Set up and crawl](#3-set-up-and-crawl): a trial, then the sections.
4. [Clean and check](#4-clean-and-check) the Markdown; adapt the cleaner if the site needs it.
5. [Ingest and answer](#5-ingest-and-answer) locally.
6. [Publish and refresh](#6-publish-and-refresh-in-the-cloud) in the cloud, on a schedule.

## Rules that do not change

- **No crawl without the client's written permission**, recorded in PLAN.md as a settled
  decision (date, who, scope).
- The crawler names itself in its user agent, obeys `robots.txt` (and a longer `Crawl-delay`),
  sends one request at a time and waits a random 0.25–1.75 s before each. Do not shorten the
  waits or run requests in parallel.
- **It stops at the first refusal**: a 429, or a 403 the site did not produce itself. Never retry
  around bot protection, change the user agent or rotate addresses: ask the client to allow the
  crawler.
- Full crawls are run by hand. Only the sitemap refresh is scheduled, and it asks only for pages
  whose `lastmod` changed.
- Raw HTML and cleaned Markdown stay out of git (`data/` is git-ignored), and out of
  `tests/fixtures/`. Tests use synthetic pages.
- Keep a log of the requests made to the site, in `docs/site.md` (below).

## 1. Survey the site

Before the permission request is written, and before any crawl, find out what the site is like.
The survey answers the questions that decide the approach: can a crawler read the content, what
is in front of the site, how big the sections are, whether the sitemap supports a cheap refresh,
and which element holds each page's content.

It makes about 15 requests (`robots.txt`, the sitemaps, the home page and 10 sampled pages; at
most 45 with a large sitemap index and `--sample 20`; each sampled page that redirects costs one
more), with the crawler's user agent, waits and
stop on refusal, and saves no page content.
That is less than one visitor reading a few pages. Let the client contact know before you run it
(an email is enough); it is the only request to the site before written permission.

```bash
.venv/bin/python scripts/survey_site.py https://www.client.example
.venv/bin/python scripts/survey_site.py https://www.client.example --sections /help,/faq --sample 12
```

Claude Code can do the whole investigation: the `site-survey` skill in `.claude/skills/` runs
the script, checks the pages by hand and writes `docs/site.md`. Ask it to "survey the client's
website".

### What it reports

| Area | What it checks | Why it matters |
| --- | --- | --- |
| `robots.txt` | Whether the crawler may read `/` and each section; `Crawl-delay`; rules for AI crawlers (GPTBot, ClaudeBot and others) | A disallow blocks the crawl. AI-bot rules show the owner's position on AI use: raise it in the permission request |
| Sitemap | `Sitemap:` lines in `robots.txt`, then `/sitemap.xml`; sitemap indexes (one level); URL count, share with `lastmod`, top-level paths | Without `lastmod`, every refresh is a full crawl. The paths show the sections and their sizes |
| Platform and protection | CMS or framework, CDN, and bot protection (Akamai, Cloudflare, Imperva, DataDome, HUMAN, Sucuri) from headers, cookies and markup | Bot protection may block a longer crawl; the platform hints at page structure (Drupal answers some archived pages with its own 403) |
| Each sampled page | Status, redirects, size, text in the HTML, title and `h1`, `noindex` | Little text plus an empty app root means the content is built in the browser, which the crawler cannot read |
| Page structure | Which candidate element (`main`, `article`, ...) holds most of the text; collapsed elements; tables; forms; frames; links to PDFs | The selector becomes `SITE_CONTENT_SELECTOR`; PDFs are not read |

It ends with a **verdict**:

| Verdict | Meaning | Next step |
| --- | --- | --- |
| Crawlable | Server-rendered content, a dated sitemap, a content element found | Ask for permission with the sections and page counts from the report |
| Crawlable, with points to settle | It works, but something needs a decision: bot protection, a thin sitemap, no common content element, PDFs, AI-bot rules | Settle each point (most go into the permission request), then ask for permission |
| Not crawlable without the client's help | The site refused the survey, `robots.txt` disallows the sections, or the content is built in the browser | Ask the client for an export, an API, or an allowlist. Rendering pages in a headless browser is a new architecture decision, not a setting |

The report is saved in `data/survey/<host>-<time>.md` and printed.

### Check by hand, then report

The script cannot judge everything. Before relying on it:

1. **Open two or three sampled pages** and compare what a visitor sees with the page source:
   FAQ answers, tabs and tables must be in the HTML. A section that loads its answers on demand
   is not in the crawl.
2. **Check the suggested selector** against the page source: it should hold the article and not
   the menus. If no selector was suggested, find the element that wraps the article and test it
   with a trial crawl (step 3).
3. **Look for content that varies**: by region, customer type or sign-in, and how the site
   shows it (separate pages, headed sections, blocks hidden by script). Each needs a decision
   on chunk metadata (AD-16).
4. **Note where answers live outside the crawl**: PDFs, tools, pages behind a login.

Then write `docs/site.md` for the project, starting from the report:

- a **Checks so far** table (date, what, requests), kept up to date with every crawl;
- the verdict, the settings, and the findings above;
- open questions for the client, which go into PLAN.md under "Decisions needed".

ea-customer-bot's `docs/ea-site.md` is a full example of what this file becomes.

## 2. Get written permission

Use `docs/templates/crawl-permission.md`, filled in from the survey: the sections, the page
count, the user agent (`snapshot.USER_AGENT`), the refresh rate, and any bot-protection or
AI-bot points. Record the reply in PLAN.md.

## 3. Set up and crawl

Settings (`.env`; every one is in `.env.example`):

| Setting | Example | Notes |
| --- | --- | --- |
| `SITE_URL` | `https://www.client.example` | Unset: the origin of `ALLOWED_LINK_PREFIX` |
| `SITE_SECTIONS` | `/help,/faq,/blog` | Path prefixes, in priority order: earlier sections are crawled first at each depth. Unset: the whole site |
| `SITE_CONTENT_SELECTOR` | `main, [role=main], article` | Comma-separated; the first that matches on a page is used, else the body without its header |
| `SITE_OWN_403_HEADER` | `x-drupal-` | Only when a trial crawl meets the site's own 403 pages (below) |
| `ALLOWED_LINK_PREFIX` | `https://www.client.example/` | Citations link only under it |

```bash
.venv/bin/python -m assistant.snapshot --max-pages 10      # a trial: about 15 requests
.venv/bin/python -m assistant.snapshot                     # the sections, up to 500 pages
.venv/bin/python -m assistant.snapshot --resume data/snapshots/.partial-<time>   # after a stop
```

The crawl takes the section pages and the sitemap's pages in the sections, then follows links
within the sections up to two hops. A snapshot lands in `data/snapshots/<id>/` and
`data/snapshots/LATEST` names it; a crawl that stops early stays in `.partial-<time>`.

**If it stops on a refusal**, read `refused.txt` in the partial folder:

- A CDN or bot-protection page (an "Access Denied" with a reference number, a challenge page):
  stop, and ask the client to allow the crawler. Do not retry.
- The site's own page (its usual template, saying "Page not found" or "Access denied" for one
  archived page): find a header only the site's own responses carry (for Drupal,
  `x-drupal-...`), set `SITE_OWN_403_HEADER` to its prefix, and `--resume`. Such pages are then
  skipped and kept in `refused/` for review.

Record every run in the requests table in `docs/site.md`.

## 4. Clean and check

```bash
.venv/bin/python -m assistant.clean          # the LATEST snapshot into data/content/<id>/
```

Read a sample of the Markdown against the live pages: headings, FAQ answers, tables, lists,
phone numbers. `clean.json` lists each page's size and the pages with no content (forms, apps,
or a selector that missed).

### Adapting the cleaner

`clean.py` handles what most sites use: a content element, headings, lists, tables, `<details>`
and hidden panels, and the usual noise. A site with its own structures needs a few lines in
`_Renderer.block`, each with a test in `tests/test_clean.py` written on a synthetic page shaped
like the site's. On the first project these were:

- **Accordions built from `div`s**: the item's title element becomes a heading one level down,
  and its panel is rendered under it (see `_Renderer.details` for the pattern).
- **Icons that carry meaning**: an empty tick `span` in a rate table rendered as "Yes".
- **Blocks shown only in some regions** (classes such as `feature-item--vic`): wrapped in
  `<!-- states: VIC -->` ... `<!-- /states -->`, which chunking turned into a payload field for
  a retrieval filter (AD-16).
- **Calls to action** peculiar to the site, added to `_CALLS_TO_ACTION`.

## 5. Ingest and answer

```bash
CONTENT_DIR=data/content .venv/bin/python -m assistant.ingest   # Qdrant on localhost:6333
```

`CONTENT_DIR=data/content` reads the folder `data/content/LATEST` names. For the Compose
`ingest` service, mount `./data/content` at `/app/content` in `docker-compose.yml` instead of
`./content`. Then follow README, "Content": refresh `evals/site_titles.txt`, write
`evals/retrieval.yaml`, re-measure the retrieval thresholds and write the scenarios.

## 6. Publish and refresh in the cloud

The first publish seeds the index store from the local snapshot, with no requests to the site:

```bash
INDEX_STORE=s3://<bucket>/content AWS_PROFILE=<profile> \
  .venv/bin/python -m assistant.refresh --seed data/snapshots/<id>
```

It cleans the snapshot, builds the index and publishes the snapshot, the Markdown, the index,
then `LATEST.json` (AD-22). From then on `python -m assistant.refresh` (the Dockerfile's
`refresh` target, run as a scheduled container task, twice a day on the first project):

- asks for `robots.txt`, the sitemaps and only the pages whose `lastmod` changed: an unchanged
  site costs a request for `robots.txt` and one per sitemap, and publishes nothing;
- holds back an index with under 90% of the published pages or points (exit 2), for a person to
  check and publish with `--force`;
- on a refusal publishes nothing, saves the response to `refused/` in the store and exits 1. Alarm
  on exit 1 and 2.

Pages the sitemap does not date, or only links reach, are refreshed only by a full crawl: run
one by hand from time to time (`--crawl`), never on a schedule. Whether `lastmod` moves when
shared content (a table used on many pages) changes is site-specific; a full crawl catches what
it misses.

The job's role needs `s3:GetObject`, `s3:PutObject` and `s3:ListBucket` on the store's prefix,
and the task needs outbound internet access to the site. If the client allowlists the crawler by
address, it needs a fixed address (a NAT gateway with an Elastic IP): include it in the DevOps
request (stage 3, "content job").
