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
        └── GitHub Actions runs every 30 min, 08:00–17:00 MYT, and commits changes
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

GitHub cron is UTC and Malaysia is a fixed UTC+8 (no daylight saving), so
08:00–17:00 MYT = 00:00–09:00 UTC. The two cron entries fire at every `:00`
and `:30` across that window (~19 runs/day).

## Caveats

* **Bot protection / datacenter IPs.** The portal may sit behind Cloudflare and
  can return `403` to non-browser or datacenter requests. The scraper already
  sends realistic browser headers and retries; if Actions runners are still
  blocked, set `MS_FALLBACK_READER` (e.g. to a reader proxy) as a workflow env
  var. A block surfaces as a failed Action, never a blank feed.
* **Best-effort scheduling.** GitHub may delay cron by a few minutes under load
  — fine for a 30-minute cadence.
* **Inactivity disable.** GitHub disables scheduled workflows after 60 days with
  *no repo activity*; the periodic commits keep this repo active.
* **Selector drift.** If the portal is redesigned, the URL-pattern backbone
  keeps working; secondary fields degrade gracefully and the safety guard
  prevents a blank feed.

## Licence / attribution

Content is © Media Selangor. This project only re-syndicates public headlines
and links as an RSS feed and is **not affiliated** with Media Selangor.
