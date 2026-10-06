# Tutorial: from a website to answers, on your laptop

This walks you through the whole website pipeline once, on Crystal Delta's own site
(<https://crystaldelta.com>), so you can see each step work before you do it for a client. It
takes about 30 minutes. The reference for every step, and for client projects, is
[website-content.md](website-content.md).

You will:

1. **Survey** the site: can our crawler read it?
2. **Crawl** it: save a copy of its pages.
3. **Clean** the pages: turn the HTML into Markdown.
4. **Ingest** the Markdown: load it into the search index.
5. **Ask** the assistant questions about it.
6. **Refresh** it, as the scheduled job does in the cloud.

Why practise on our own site: crawling someone else's website needs their written permission
(step 2 in [website-content.md](website-content.md)). Crystal Delta's site is ours, so you can
practise freely. Be considerate all the same: each full crawl is about 60 requests, so run it
once or twice, not in a loop.

## Words you will meet

| Word | Meaning |
| --- | --- |
| **Crawler** | A program that downloads web pages and follows their links. Ours is `snapshot.py` |
| **`robots.txt`** | A file at the root of a site (`/robots.txt`) saying which pages crawlers may read. We always obey it |
| **Sitemap** | An XML file listing a site's pages, often with a **`lastmod`** date: when the page last changed |
| **Snapshot** | One saved copy of the site's pages (raw HTML), in `data/snapshots/<id>/` |
| **Selector** | A CSS rule that picks part of a page, such as `main` (the `<main>` element). The cleaner keeps only what it picks |
| **Chunk** | A piece of a page (one heading's section) that the search index stores and the assistant reads |
| **Bot protection** | A service in front of a site (Cloudflare, Akamai) that blocks traffic that looks automated |

## Before you start

New to the starter itself? Do [the starter tutorial](tutorial.md) first: it explains how the
assistant answers, which this tutorial builds on.

You need the starter running on your machine, as in the README's "Quick start":

```bash
cd ai-starter
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
.venv/bin/pytest -q          # all tests pass: your setup works
```

You also need Docker running, and Ollama with a model (`ollama pull gpt-oss:20b`).

Every command below is run from the repo's root folder (`ai-starter/`).

## Step 1: Survey the site

```bash
.venv/bin/python scripts/survey_site.py https://crystaldelta.com
```

It reads `robots.txt`, the sitemap and about ten pages (one at a time, with a pause of about a
second between them), then prints a report. You should see:

```
robots.txt: HTTP 200
sitemap: 26 URLs from 1 sitemap(s)
  200  https://crystaldelta.com/case-study-silk.html
  ...

## Verdict: Crawlable, with points to settle

- Bot protection (Cloudflare) did not stop the survey, but may stop a longer crawl. ...
- Every lastmod in the sitemap is the same day (2026-04-23): the dates are likely generated ...
- 10 of 11 sampled sitemap URLs redirect (for example /case-study-silk.html to /case-study-silk) ...
- Email addresses are hidden from crawlers (Cloudflare's email obfuscation) ...

## Suggested settings

SITE_URL=https://crystaldelta.com
SITE_CONTENT_SELECTOR=main
...
```

The report is also saved in `data/survey/`. **Read the verdict and each point.** For a client,
each point becomes a question for them, or a note in the permission request. Here, for example:
the hidden email addresses mean the assistant can never tell a customer our email address.

## Step 2: Settings

Copy the example settings file, then set the site in `.env` (it is git-ignored):

```bash
cp .env.example .env
```

Add these lines at the end of `.env`, from the survey's "Suggested settings":

```bash
SITE_URL=https://crystaldelta.com
ALLOWED_LINK_PREFIX=https://crystaldelta.com/
SITE_CONTENT_SELECTOR=main
```

`ALLOWED_LINK_PREFIX` matters: the assistant only links to pages under it. `SITE_SECTIONS` is
left out, so the whole site is crawled; on a big client site you would list the sections the
assistant should answer from, such as `SITE_SECTIONS=/help,/faq`.

## Step 3: Crawl, first a trial

Always start with a small trial:

```bash
.venv/bin/python -m assistant.snapshot --max-pages 10
```

```
Site https://crystaldelta.com, sections /
robots.txt read; the sitemap lists 26 URLs
    1  https://crystaldelta.com/
    2  https://crystaldelta.com/about
  ...
   10  https://crystaldelta.com/case-study-energyaustralia-platform

Snapshot 56428b740080: 10 pages saved, 1 not saved, 17 found but not visited (--max-pages), 23 requests.
```

Look at what it saved:

```bash
ls data/snapshots/$(cat data/snapshots/LATEST)        # manifest.json  pages/  sitemap.json
ls data/snapshots/$(cat data/snapshots/LATEST)/pages  # one .html file per page
```

`manifest.json` records every page: its address, when it was fetched, its `lastmod`, and the
pages that were skipped and why (a redirect to a page already saved, a 404).

When the trial looks right, crawl the whole site:

```bash
.venv/bin/python -m assistant.snapshot
```

```
Snapshot 2dfe2589fdb1: 26 pages saved, 3 not saved, 0 found but not visited (--max-pages), 57 requests.
```

**If it stops** with "Stopped: the site refused the crawler": do not run it again. Read the
`refused.txt` file it names, and follow [website-content.md](website-content.md), "If it stops on
a refusal". Never try to get around a refusal.

## Step 4: Clean the pages

```bash
.venv/bin/python -m assistant.clean
```

```
Snapshot 2dfe2589fdb1: 26 pages cleaned (101,087 characters), 0 with no content.
```

The Markdown is in `data/content/<id>/`, one `.md` file per page. **Open three or four and read
them next to the live pages.** This is the most important check in the whole process: the
assistant can only answer from what is in these files.

```bash
ls data/content/$(cat data/content/LATEST)
less data/content/$(cat data/content/LATEST)/about.md
```

Each file starts with front matter (`title`, `url`, `lastmod`, ...), then the page's text with
its headings. Things to look for:

- **Missing text**: an answer you can see on the live page is not in the file. Often it is
  loaded by a script after the page opens, which the crawler cannot see.
- **Noise**: menus, cookie banners or button labels ("Find out more") in the text. The
  selector may be too wide, or the cleaner may need a rule.
- **Wrong titles**: the title comes from the page's `<h1>`, or its `<title>` if it has none.

On our site you will find one of each kind of lesson. `/what-we-do` has no `<main>` element, so
the cleaner falls back to the whole page body; and it has no `<h1>`, and its `<title>` says
"What We Build", so that is its title. The cleaner did what it should: the page itself is
wrong. For a client, you would note it in `docs/site.md` and tell them.

## Step 5: Ingest and ask

Start the local stack (Qdrant, the search index; and the API):

```bash
docker compose up -d --build
```

Load the cleaned pages into the index. `CONTENT_DIR=data/content` tells ingest to read the
folder `data/content/LATEST` names:

```bash
CONTENT_DIR=data/content .venv/bin/python -m assistant.ingest
```

```
Embedding 186 chunks with BAAI/bge-base-en-v1.5 (first run downloads the model)...
Recreating existing collection 'content'
Done: 186 vectors in collection 'content' (768d, cosine)
```

The first run downloads the embedding model (about a minute). Check the API sees the content:

```bash
curl -s localhost:8000/api/health     # "ollama_ok": true, "qdrant_ok": true, "points": 186
```

Now ask it something:

```bash
.venv/bin/python scripts/chat.py
```

```
> What does Crystal Delta do for EnergyAustralia?
[searching]
[generating]
Crystal Delta upgraded EnergyAustralia's payment system in four tranches, ...
Sources:
  [1]  We engineer production systems. AI is how we build them.  https://crystaldelta.com/
  [2]  Payment Infrastructure Upgrade for Energy Australia.  https://crystaldelta.com/case-study-energyaustralia-platform
  ...
> Which other clients use your AI work?
```

The second question is a **follow-up**: "your AI work" only makes sense after the first one,
and the assistant rewrites it into a full question before searching. The sources are the pages
the answer came from, all on crystaldelta.com. Type `/quit` to leave.

The answers sound like a generic assistant because the starter's prompts are placeholders; a
client project rewrites them (`src/assistant/prompts/`).

## Step 6: Refresh, as the cloud job does

In the cloud, `refresh.py` runs on a schedule. It keeps the index current by asking only for
pages whose sitemap `lastmod` changed. You can run it locally against a folder that stands in
for the cloud storage (S3):

```bash
INDEX_STORE=data/store .venv/bin/python -m assistant.refresh --seed data/snapshots/$(cat data/snapshots/LATEST)
```

```
Published index 1c68217f3faf-20261006T222759Z: 26 pages, 186 points
```

`--seed` publishes your local snapshot without asking the site anything. Now run the scheduled
job itself:

```bash
INDEX_STORE=data/store .venv/bin/python -m assistant.refresh
```

```
Refresh: 0 pages fetched, 26 copied, 0 removed, 2 requests
No change: snapshot 2dfe2589fdb1 is still current.
```

Two requests: `robots.txt` and the sitemap. Nothing changed, so nothing was fetched or
published. When a page's `lastmod` changes, only that page is fetched. Look in `data/store/` to
see what the cloud storage holds: `LATEST.json` (which index the assistant should load), the
index, the Markdown and the raw snapshot.

## Clean up

```bash
docker compose down                       # stop the stack (add -v to delete the index and conversations)
rm -rf data/snapshots data/content data/store data/survey
```

Before a real project, put `content/` back in `.env`'s `CONTENT_DIR` (or remove the line), and
remove the `SITE_*` lines.

## When something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| `Set SITE_URL (or ALLOWED_LINK_PREFIX) to the client's site` | No site is set | Step 2: add `SITE_URL` to `.env` |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Another stack uses the port | Add `API_PORT=8100` and `QDRANT_PORT=6433` to `.env`, then use `localhost:8100` (`chat.py --api http://localhost:8100`) and `QDRANT_URL=http://localhost:6433` for ingest |
| `Qdrant at http://localhost:6333 not reachable` | The stack is not running | `docker compose up -d`, then try again |
| `"ollama_ok": false` | Ollama is not running, or has no model | Start Ollama; `ollama pull gpt-oss:20b` |
| `Stopped: the site refused the crawler` | Bot protection or rate limiting | Do not retry. Read `refused.txt`; see [website-content.md](website-content.md) |
| A page's Markdown is empty | The selector found nothing on it | Check the page source for the element holding the text; adjust `SITE_CONTENT_SELECTOR` |
| Answers about a fictional organisation, with `example.invalid` sources | The index still holds the synthetic sample from `content/` | Run the ingest in step 5 again with `CONTENT_DIR=data/content` |

## What changes for a client's site

- **Permission first.** The survey is the only request before the client agrees in writing
  ([website-content.md](website-content.md), step 2).
- **Sections.** Client sites are large: set `SITE_SECTIONS` to the parts the assistant answers
  from, in priority order.
- **Write it down.** Keep `docs/site.md`: what the survey found, every crawl and its request
  count, and the questions for the client. The `site-survey` skill helps Claude Code write it.
- **The cleaner may need rules** for the site's own layouts ([website-content.md](website-content.md),
  "Adapting the cleaner"), each with a test.
