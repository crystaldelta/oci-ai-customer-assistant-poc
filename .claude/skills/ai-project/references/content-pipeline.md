# Content pipeline (RAG)

Reference: ea-customer-bot `snapshot.py`, `clean.py`, `chunking.py`, `ingest.py`, `refresh.py`,
`index_store.py`, `retrieval.py`; AD-22, AD-23, AD-31, AD-33 to AD-35.

## Permission and politeness

- Written permission from the client to crawl, and the scope, before the first request.
- The fetcher names itself in its user agent, obeys `robots.txt`, waits a random 0.25–1.75 s
  before each request, runs requests one at a time, and **stops** on 429 or a 403 the site did not
  produce itself, saving the response for review. Never retry around bot protection: ask the
  client to allow the crawler.
- After the first crawl, refresh from the sitemap: only pages whose `lastmod` changed. An unchanged
  site costs two requests.

## Steps

1. **Snapshot:** raw HTML plus a manifest in `data/snapshots/<id>/`; the ID is a hash of URLs and
   content hashes; `--resume` continues a stopped crawl. Git-ignored.
2. **Clean:** HTML to Markdown in `data/content/<id>/`, keeping body, FAQs and tables; dropping
   navigation, forms and scripts. `LATEST` names the current one. Git-ignored.
3. **Chunk:** one chunk per heading section (split at paragraphs above about 1,000 characters),
   text starting with the heading path. Payload: `url`, `title`, `page`, filters (region,
   audience, date), from URL and headings, not body text. Drop exact repeats.
4. **Ingest:** embed with the same model as the query (dimension-locked; changing it means
   re-ingesting); a named collection; `--if-empty` for the Compose job.
5. **Check:** a new index with under 90% of the published pages or points, or another embedding
   model, is held back for a person (exit 2).
6. **Publish** to an index store (object storage, or a local folder with the same layout):
   snapshot, then index, then `LATEST.json` **last**, naming the index and embedding model.

The agent downloads the index `LATEST.json` names on its first search and checks for a newer one
periodically, swapping only after it opens with points and the same model. Content changes need
no redeploy. The job runs on a schedule as a container task (not a function: its run time grows
with the content).

## Retrieval tuning (measure on the real content)

- `top_k` 5; **one passage per page** (FAQ pages otherwise crowd out the answer).
- **Score floor** on the first question (and rewritten follow-ups) to decline off-topic questions;
  measure in-scope and off-topic scores on the snapshot and set the floor between them.
- **Gap cut:** drop passages more than ~0.10 below the best.
- **Filters** by user context (region, audience); a close passage for the user's region may take
  the last place within a small margin.
- Secondary content (blogs) searched apart with a score penalty, so the authoritative pages come
  first; mark its date for the model.
- `scripts/verify_retrieval.py`: expected pages for representative questions, both sides of the
  floor, with no model involved. Re-run after any retrieval or content change.

## Tests and evals

- Unit tests use a **synthetic** fixture corpus (`example.invalid` URLs) pinned in `conftest.py`;
  never real client pages in fixtures.
- Scenarios name real page titles, checked against a committed `evals/site_titles.txt`, refreshed
  after each snapshot.
