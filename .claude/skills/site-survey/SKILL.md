---
name: site-survey
description: Investigate a client's website before crawling it for an assistant's content - run scripts/survey_site.py, check the sampled pages by hand, and report whether and how the site can be crawled (robots.txt, sitemap and lastmod, bot protection, script-rendered pages, the content selector) in the project's docs/site.md. Use whenever a project's content will come from a website, when asked to survey, scan, assess or check a site's crawlability, before writing the crawl permission request, or when a crawl was refused or cleaned badly.
---

# Site survey

Find out whether, and how, this project's crawler (`src/<package>/snapshot.py`) can read the
client's website, and report back to the developer in `docs/site.md`. The procedure and the
meaning of every check are in `docs/website-content.md`, section 1: read it first.

## Before any request

- The survey is the only request to the site allowed before the client's written permission.
  Confirm with the developer that the client contact knows it is coming. If they do not, stop
  and say so.
- Use only `scripts/survey_site.py` and, for the checks by hand, at most five further page
  fetches through the same polite fetcher (or the developer's browser). No other crawling, no
  parallel requests, no retries after a refusal, no changed user agent.
- If the site refuses the survey (exit 1), stop. Report the refusal and the bot protection seen;
  the next step is the client, not another attempt.

## Steps

1. Ask for, or find in PLAN.md, the site's address and the sections the assistant should answer
   from. Without sections, survey the whole site; the report's top-level paths help choose them.
2. Run the survey and read the whole report:

   ```bash
   .venv/bin/python scripts/survey_site.py https://www.client.example --sections /help,/faq
   ```

3. Check what the script cannot judge (docs/website-content.md, "Check by hand"):
   - For two or three sampled pages, compare what a visitor sees with the HTML: are the FAQ
     answers, tabs and tables in the source? Ask the developer to look in a browser if you
     cannot.
   - Does the suggested `SITE_CONTENT_SELECTOR` hold the article without menus? With the
     developer's agreement and only after permission, a trial crawl (`--max-pages 10`) and
     `assistant.clean` show the Markdown the selector gives.
   - Content that varies by region, customer type or sign-in, and how the site shows it.
   - Answers outside the HTML: PDFs, tools, sign-in pages.
4. Write `docs/site.md` (create it, or update the existing one), in plain Australian English:
   - **Checks so far**: a table of date, what, requests; add this survey's row.
   - **Verdict** and its reasons, from the report and your checks.
   - **Settings**: `SITE_URL`, `SITE_SECTIONS`, `SITE_CONTENT_SELECTOR`, and
     `SITE_OWN_403_HEADER` only if a site 403 page was seen.
   - **Findings**: robots.txt, sitemap (URL count, share with `lastmod`, sizes of the sections),
     platform and protection, page structure, content that varies, answers outside the HTML.
   - **Implications for the build**: cleaner changes needed (docs/website-content.md, "Adapting
     the cleaner"), chunk metadata for content that varies, what a refresh can and cannot catch.
   - **Questions for the client**: also add each to PLAN.md under "Decisions needed".
5. Report to the developer in a few lines: the verdict, what blocks or needs settling, the
   settings, and what to put in the permission request (`docs/templates/crawl-permission.md`).

## Reading the verdict

- **Script-rendered content** (little text in the HTML, an empty app root) cannot be crawled by
  this pipeline. Recommend asking for a CMS export or API; a headless browser would be a new
  architecture decision, never a quiet workaround.
- **Bot protection** that let the survey through may still stop a 500-page crawl. Put the user
  agent in the permission request and ask whether to allowlist it.
- **AI-bot rules in robots.txt** state the owner's position on AI use. Raise them with the
  client even when the crawler's own name is allowed.
- **A thin sitemap** (few `lastmod` dates) means each refresh is close to a full crawl: plan the
  refresh rate with the client.
