# mediaselangor_latest_rss

A free, self-hosted **RSS feed for [Media Selangor](https://mediaselangor.com)**
(Bahasa Melayu / `/ms/latest`). The portal ships no native feed, so a small
scraper turns its server-rendered listing page into a valid RSS&nbsp;2.0 file,
GitHub Actions rebuilds it on a schedule, and GitHub Pages serves it at a stable
URL you can subscribe to in any reader.

**Subscribe URL (after setup):**

```
https://novinthen.github.io/mediaselangor_latest_rss/feed.xml
```

Landing page: <https://novinthen.github.io/mediaselangor_latest_rss/>

---

## How it works

```
mediaselangor.com/ms/latest  ──(scrape)──►  scripts/build_feed.py  ──►  docs/feed.xml
                                                     │                        │
                                              state/seen.json          GitHub Pages
                                          (stable pubDates)         (serves the feed)
        ▲
        └── GitHub Actions runs every 30 min, 07:00–22:00 MYT, and commits changes
```

* **Resilient selection.** Articles are located by their stable URL pattern
  `^/ms/\d{4}/\d{2}/\d+$` (e.g. `/ms/2026/06/379390`) rather than fragile CSS
  classes. The numeric id becomes the item's stable GUID; the title, category,
  date, summary and thumbnail are best-effort and degrade gracefully.
* **Stable, monotonic `pubDate`s.** The listing shows only a date, not a time,
  so the first time an article is seen (UTC) is recorded in `state/seen.json`
  and reused as its `pubDate` forever. Items first seen in the same run are
  offset by page position so readers preserve the site's newest-first order.
* **Never blanks a working feed.** If the fetch fails or fewer than 5 articles
  parse, the script exits non-zero and leaves `docs/feed.xml` untouched — a
  transient outage or a redesign shows up as a *failed Action* and a
  *stale-but-intact* feed, never an empty one.
* **Deterministic output.** `lastBuildDate` is derived from the newest item, so
  when nothing changes the file is byte-identical and produces no commit and no
  Pages rebuild.

## Repository layout

| Path | Purpose |
| --- | --- |
| `scripts/build_feed.py` | Scraper → RSS generator |
| `.github/workflows/build-feed.yml` | Scheduled build + commit |
| `.github/workflows/ci.yml` | Runs the offline parser tests on push/PR |
| `requirements.txt` | `requests`, `beautifulsoup4`, `feedgen`, `lxml` |
| `state/seen.json` | `{article_id: first_seen_iso}` — stable pubDates |
| `docs/feed.xml` | Generated feed, served by Pages |
| `docs/index.html` | Tiny landing page with the subscribe link |
| `tests/` | Offline fixture + parser tests |

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Offline dry run against the bundled fixture (no network needed):
MS_SOURCE_FILE=tests/fixtures/ms_latest_sample.html python scripts/build_feed.py

# Live run (writes docs/feed.xml + state/seen.json):
python scripts/build_feed.py

# Tests:
python tests/test_parser.py        # or: pytest tests/
```

### Configuration (environment variables, all optional)

| Variable | Effect |
| --- | --- |
| `MS_USER_AGENT` | Override the HTTP User-Agent. |
| `MS_FALLBACK_READER` | URL prefix of a read-only fetch proxy (e.g. `https://r.jina.ai/`) tried only if the direct request is blocked. |
| `MS_SOURCE_FILE` | Parse a local HTML file instead of fetching (offline runs / tests). |

## One-time setup on GitHub

1. **Merge this branch into `main`.** Scheduled workflows and the
   `workflow_dispatch` button only run from the default branch.
2. **Enable GitHub Pages:** *Settings → Pages → Source = Deploy from a branch →
   Branch `main`, folder `/docs`.*
3. **Generate the first feed now:** *Actions → “Build Media Selangor RSS feed” →
   Run workflow* (don't wait for the next cron slot).
4. **Subscribe** to `https://novinthen.github.io/mediaselangor_latest_rss/feed.xml`
   in your reader. Once happy, retire the old rss.app feed.

## Schedule

The 30-minute cadence (07:00–22:00 MYT) is driven by an **external scheduler**,
because GitHub's native `schedule:` cron is best-effort and routinely drops or
delays runs at the top of the hour. GitHub's cron is kept as an hourly safety
net across the same window (`13 0-14,23 * * *` → 07:13–22:13 MYT), matching the
[tamilnewsbot](https://github.com/novinthen/tamilnewsbot) digest schedule
(hourly, 07:00–22:00 MYT) that consumes this feed: each :13 fallback build lands
~45 minutes before the bot's next top-of-hour run.

### External scheduler (primary trigger)

A free [cron-job.org](https://cron-job.org) job calls the `workflow_dispatch` REST
API on a precise schedule, authenticated with a **fine-grained PAT** scoped to this
repo only.

1. **Fine-grained PAT** — GitHub → Settings → Developer settings → Personal access
   tokens → *Fine-grained tokens*:
   - Resource owner `novinthen`; Repository access → **Only** `mediaselangor_latest_rss`.
   - Permissions → **Actions: Read and write** (Metadata read-only is auto-added). Nothing else.
   - Set an expiration and a rotation reminder. Copy the token once.
2. **cron-job.org job:**
   - Method `POST`, URL
     `https://api.github.com/repos/novinthen/mediaselangor_latest_rss/actions/workflows/build-feed.yml/dispatches`
   - Headers:
     - `Authorization: Bearer <PAT>`
     - `Accept: application/vnd.github+json`
     - `X-GitHub-Api-Version: 2022-11-28`
   - Body: `{"ref":"main"}`
   - Schedule: timezone `Asia/Kuala_Lumpur`, every 30 min from 07:00 to 22:00.
   - A successful trigger returns **HTTP 204 No Content**.

The PAT lives in cron-job.org, **not** in the repo — no GitHub repo secret is
needed, and its minimal scope means a leaked token can at most trigger this one
workflow. More secure but heavier alternatives: a GitHub App (short-lived tokens),
or hosting the trigger on Cloudflare Workers Cron with the PAT as a Worker secret.

## Caveats

* **Bot protection / datacenter IPs.** The portal may sit behind Cloudflare and
  can return `403` to non-browser or datacenter requests. The scraper already
  sends realistic browser headers and retries; if Actions runners are still
  blocked, set `MS_FALLBACK_READER` (e.g. to a reader proxy) as a workflow env
  var. A block surfaces as a failed Action, never a blank feed.
* **Scheduling.** GitHub's native cron is unreliable (drops/delays at the top of
  the hour), which is why the primary trigger is the external scheduler above; the
  native cron remains only as a fallback. A missed run never loses data — the next
  run picks up any new articles via `state/seen.json`.
* **Inactivity disable.** GitHub disables scheduled workflows after 60 days with
  *no repo activity*; the external trigger plus periodic commits keep this repo active.
* **Selector drift.** If the portal is redesigned, the URL-pattern backbone
  keeps working; secondary fields degrade gracefully and the safety guard
  prevents a blank feed.

## Licence / attribution

Content is © Media Selangor. This project only re-syndicates public headlines
and links as an RSS feed and is **not affiliated** with Media Selangor.
