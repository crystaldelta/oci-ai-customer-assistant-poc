# Bringing starter changes into your project

A project made from the starter has its own Git history and its own names (`new_project.py`
renamed the package, the project and the client). So it cannot simply `git pull` from the
starter. This page shows how to bring a later starter change into a project anyway, such as the
website crawler (PR #2, AD-24).

The idea: make two throwaway copies of the starter, one at the commit your project started from
and one at the commit with the change. Rename both exactly as your project was renamed. The
difference between them is the change, in your project's names. Git then applies it to your
project and merges it with your own edits.

It takes about 15 minutes. You need Git, Python 3 and `rsync` (all on macOS by default).

## 1. Find your project's options and starting point

The options you gave `new_project.py`. If you don't have them, read them from the project:

| Option | Where to find it |
| --- | --- |
| `--project` | `name` in `pyproject.toml` |
| `--package` | The folder under `src/` |
| `--client` | `name` in `frontend/src/brand.ts`, unless you have changed it since |
| `--link-prefix` | `allowed_link_prefix` in `src/<package>/config.py`, unless changed since |
| `--safety-rules` | `yes` if `safety_rules: bool = True` in `config.py`, else `no` |

The starter commit your project started from (`BEFORE` below):

| Your project has | `BEFORE` |
| --- | --- |
| `.claude/skills/ai-project/` | `df0b1a2` (starter `main` from 6 Oct 2026) |
| No `.claude/skills/` folder | `57b362c` (the first starter, 5 Oct 2026) |

For a later starter change, pick the last starter commit before your project was made
(`git log --format='%h %ad %s' --date=short` in the starter).

## 2. Make the change as a patch, in your project's names

Set the variables, then run the rest as it is. The example is a project for La Trobe University:

```bash
OPTS=(--project latrobe-assistant --package latrobe_assistant --client "La Trobe University"
      --link-prefix https://www.latrobe.edu.au/ --safety-rules yes)
BEFORE=df0b1a2      # the starter commit your project started from (step 1)
AFTER=main          # the starter commit with the change

mkdir -p ~/starter-upgrade && cd ~/starter-upgrade
git clone -q https://github.com/crystaldelta/ai-customer-assistant-starter.git starter
for v in before after; do
  rm -rf $v && cp -R starter $v
  rev=$BEFORE; [ $v = after ] && rev=$AFTER
  (cd $v && git checkout -q $rev && rm -rf .git && python3 scripts/new_project.py "${OPTS[@]}" > /dev/null)
done
cd before && git init -q && git add -A && git commit -qm before
rsync -a --delete --exclude .git ../after/ ./
git add -A && git diff --cached --binary > ../starter-changes.patch
git diff --cached --stat | tail -1
```

The last line prints the size of the change; for the crawler, about `37 files changed`.

## 3. Apply it to your project

Start from a clean working tree (commit or stash your work), on a new branch:

```bash
cd ~/Projects/latrobe-assistant             # your project
git switch -c add-website-crawler
git fetch -q ~/starter-upgrade/before HEAD   # the starting files, so Git can merge your edits
git apply --3way ~/starter-upgrade/starter-changes.patch
git status
```

`git apply --3way` merges the change with your own edits, file by file:

- **Files you have not touched** take the change as it is.
- **Files you changed elsewhere** keep your edits and take the change.
- **Lines you and the starter both changed** become conflicts. The command ends with an error,
  and `git status` lists the files under "Unmerged paths". Open each one, find the
  `<<<<<<< ours` … `=======` … `>>>>>>> theirs` blocks (yours first, the starter's second),
  keep what is right, delete the markers and `git add` the file.

The patch carries everything in the change, not only code. Keep what helps your project and
drop the rest: `git checkout HEAD -- <file>` undoes a changed file, `git rm -f <file>` removes
an added one. For the crawler, the docs and tutorials are worth keeping; `content/ways-to-pay.md`
is the starter's sample page, so drop it if you have replaced the sample content.

## 4. Check it, then commit

```bash
.venv/bin/pip install -r requirements.txt    # the change may add dependencies (the crawler adds beautifulsoup4)
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
git commit -m "Bring in the website crawler from the starter (PR #2)"
```

Then delete `~/starter-upgrade`.

## After bringing in the website crawler

The crawler is in your project but does nothing until you set it up:

1. Read [website-content-tutorial.md](website-content-tutorial.md) and do it once: it runs the
   whole pipeline on Crystal Delta's own site.
2. In `docs/ARCHITECTURE.md`, AD-24 arrives marked "Inherited from the starter": confirm it.
3. Survey the client's site: `.venv/bin/python scripts/survey_site.py https://www.latrobe.edu.au`
   (about 15 requests; let the client contact know first). Or ask Claude Code to "survey the
   client's website", which uses the `site-survey` skill.
4. **Get the client's written permission before any crawl**
   (`docs/templates/crawl-permission.md`), then follow
   [website-content.md](website-content.md) from step 3.
