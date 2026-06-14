#!/usr/bin/env python3
"""Build an RSS 2.0 feed for Media Selangor (Bahasa Melayu) from the
``/ms/latest`` listing page.

The source portal (https://mediaselangor.com) ships no native feed, so this
scraper turns the server-rendered listing into ``docs/feed.xml`` which is then
served by GitHub Pages.

Design goals
------------
* **Resilient selection.** Articles are found by their stable URL pattern
  (``/ms/YYYY/MM/<numeric-id>``) rather than fragile CSS class names. Every
  other field (title, category, date, summary, thumbnail) is best-effort and
  degrades gracefully when the markup drifts.
* **Stable, monotonic pubDates.** The listing only exposes a date, never a
  time, so the first moment we *see* an article (UTC) is recorded in
  ``state/seen.json`` and reused forever as its ``pubDate``. New items in a
  single run are offset by page position so reader ordering matches the site.
* **Never blank a working feed.** If the fetch fails or fewer than
  ``MIN_ARTICLES`` parse, we exit non-zero and leave the previous ``feed.xml``
  untouched — a transient outage or a redesign surfaces as a failed run and a
  stale-but-intact feed, never an empty one.
* **Deterministic output.** ``lastBuildDate`` is derived from content (the
  newest item's timestamp), so when nothing changes the file is byte-identical
  across runs and produces no commit / no Pages rebuild.

Configuration (all optional, via environment variables)
--------------------------------------------------------
* ``MS_USER_AGENT``       – override the HTTP User-Agent.
* ``MS_FALLBACK_READER``  – URL prefix of a read-only fetch proxy (e.g.
  ``https://r.jina.ai/``) tried only if the direct request is blocked. Handy
  if the portal's bot protection rejects datacenter IPs.
* ``MS_SOURCE_FILE``      – parse this local HTML file instead of fetching
  (used by the test suite and for offline dry runs).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

REPO_OWNER = "novinthen"
REPO_NAME = "mediaselangor_latest_rss"

SITE_ROOT = "https://mediaselangor.com"
SOURCE_URL = f"{SITE_ROOT}/ms/latest"

PAGES_BASE = f"https://{REPO_OWNER}.github.io/{REPO_NAME}"
FEED_SELF_URL = f"{PAGES_BASE}/feed.xml"

# Project paths (resolved relative to the repo root, i.e. this file's parent's
# parent) so the script works regardless of the current working directory.
REPO_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_DIR / "state" / "seen.json"
OUTPUT_FILE = REPO_DIR / "docs" / "feed.xml"

# An article anchor is anything pointing at /ms/<year>/<month>/<numeric id>.
ARTICLE_PATH_RE = re.compile(r"^/ms/\d{4}/\d{2}/\d+/?$")

MAX_ITEMS = 50          # cap the feed at the newest N articles
MIN_ARTICLES = 5        # safety guard: refuse to publish fewer than this
HTTP_TIMEOUT = 25       # seconds
HTTP_RETRIES = 3        # total attempts for the direct fetch

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

GENERATOR = f"mediaselangor-rss (+github.com/{REPO_OWNER}/{REPO_NAME})"


def log(msg: str) -> None:
    print(f"[build_feed] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": os.environ.get("MS_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ms,en-US;q=0.9,en;q=0.8",
        "Referer": f"{SITE_ROOT}/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


def fetch_html() -> str:
    """Return the listing page HTML.

    Order of preference: a local file (``MS_SOURCE_FILE``), a direct HTTP GET
    with browser-like headers and retries, then an optional reader-proxy
    fallback. Raises ``RuntimeError`` if none succeed.
    """
    local = os.environ.get("MS_SOURCE_FILE")
    if local:
        log(f"reading local source file: {local}")
        return Path(local).read_text(encoding="utf-8")

    session = requests.Session()
    headers = _browser_headers()
    last_error = "unknown error"

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            resp = session.get(SOURCE_URL, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200 and resp.text.strip():
                log(f"fetched {SOURCE_URL} ({len(resp.text)} bytes)")
                return resp.text
            last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        log(f"attempt {attempt}/{HTTP_RETRIES} failed ({last_error})")
        if attempt < HTTP_RETRIES:
            time.sleep(2 ** attempt)  # 2s, 4s backoff

    # Optional fallback through a read-only fetch proxy for sites whose bot
    # protection rejects datacenter IPs.
    reader = os.environ.get("MS_FALLBACK_READER")
    if reader:
        proxied = reader.rstrip("/") + "/" + SOURCE_URL
        log(f"trying fallback reader proxy: {proxied}")
        try:
            resp = session.get(proxied, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
            last_error = f"reader HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_error = f"reader {type(exc).__name__}: {exc}"

    raise RuntimeError(f"could not fetch {SOURCE_URL}: {last_error}")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _normalize_path(href: str) -> str | None:
    """Return the site-relative path for an article href, or ``None``."""
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.netloc and parsed.netloc not in (
        "mediaselangor.com",
        "www.mediaselangor.com",
    ):
        return None
    path = parsed.path
    return path if ARTICLE_PATH_RE.match(path) else None


def _find_card(anchor):
    """Walk up from an article anchor to the smallest ancestor that looks like
    a self-contained card (one that holds a heading or an image)."""
    node = anchor
    for _ in range(5):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "[document]"):
            break
        node = parent
        if node.find(["h1", "h2", "h3", "h4"]) or node.find("img"):
            return node
    return anchor


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text).strip() if text else ""


def _extract_title(card, anchor) -> str:
    heading = card.find(["h1", "h2", "h3", "h4"])
    if heading:
        title = _clean(heading.get_text())
        if title:
            return title
    title = _clean(anchor.get_text())
    if title:
        return title
    img = card.find("img")
    if img and img.get("alt"):
        return _clean(img["alt"])
    return ""


def _extract_thumbnail(card) -> str:
    img = card.find("img")
    if not img:
        return ""
    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
        val = img.get(attr)
        if val and not val.startswith("data:"):
            return urljoin(SITE_ROOT, val)
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first:
            return urljoin(SITE_ROOT, first)
    return ""


def _extract_category(card) -> str:
    # Prefer an explicit category/tag link.
    for a in card.find_all("a", href=True):
        path = urlparse(a["href"]).path.lower()
        if any(seg in path for seg in ("/category/", "/kategori/", "/tag/")):
            text = _clean(a.get_text())
            if text:
                return text
    # Fall back to an element whose class hints at a category.
    node = card.find(
        attrs={"class": re.compile(r"categor|kategori|tag|label", re.I)}
    )
    return _clean(node.get_text()) if node else ""


def _extract_date(card) -> str:
    time_tag = card.find("time")
    if time_tag:
        return _clean(time_tag.get("datetime") or time_tag.get_text())
    node = card.find(
        attrs={"class": re.compile(r"date|time|tarikh|posted|meta", re.I)}
    )
    return _clean(node.get_text()) if node else ""


def _extract_summary(card, title: str) -> str:
    for p in card.find_all("p"):
        text = _clean(p.get_text())
        if text and text != title and len(text) > 30:
            return text
    return ""


def parse_articles(html: str) -> list[dict]:
    """Parse the listing HTML into an ordered, de-duplicated list of articles
    (newest first, matching page order)."""
    soup = BeautifulSoup(html, "lxml")
    seen_ids: set[str] = set()
    articles: list[dict] = []

    for anchor in soup.find_all("a", href=True):
        path = _normalize_path(anchor["href"])
        if not path:
            continue
        article_id = path.rstrip("/").split("/")[-1]
        if article_id in seen_ids:
            continue

        try:
            card = _find_card(anchor)
            title = _extract_title(card, anchor)
            if not title:
                continue  # an article with no usable title is not worth listing
            article = {
                "id": article_id,
                "url": urljoin(SITE_ROOT, path),
                "title": title,
                "category": _extract_category(card),
                "date": _extract_date(card),
                "summary": _extract_summary(card, title),
                "thumbnail": _extract_thumbnail(card),
            }
        except Exception as exc:  # never let one bad card kill the run
            log(f"skipping malformed card for /{article_id}: {exc}")
            continue

        seen_ids.add(article_id)
        articles.append(article)

    return articles


# --------------------------------------------------------------------------- #
# State (stable pubDates)
# --------------------------------------------------------------------------- #

def load_seen() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            log(f"could not read state file, starting fresh: {exc}")
    return {}


def update_seen(seen: dict[str, str], articles: list[dict], now: datetime) -> None:
    """Assign a first-seen UTC timestamp to any newly discovered article.

    Items first seen in the same run are offset by their page position (one
    second per slot) so the topmost / newest article carries the latest
    timestamp and readers preserve the site's ordering.
    """
    for index, article in enumerate(articles):
        if article["id"] not in seen:
            ts = now - timedelta(seconds=index)
            seen[article["id"]] = ts.replace(microsecond=0).isoformat()


def prune_seen(seen: dict[str, str], keep: int = 1000) -> dict[str, str]:
    """Cap the state file so it cannot grow without bound, keeping the most
    recently seen ids (which are the only ones that can reappear in the feed)."""
    if len(seen) <= keep:
        return seen
    ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
    return dict(ordered[:keep])


def save_seen(seen: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(seen, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parse_iso(value: str, fallback: datetime) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return fallback
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Feed generation
# --------------------------------------------------------------------------- #

def build_feed_xml(articles: list[dict], seen: dict[str, str]) -> bytes:
    # Newest first, ordered by the stable first-seen timestamp.
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ordered = sorted(
        articles,
        key=lambda a: _parse_iso(seen.get(a["id"], ""), epoch),
        reverse=True,
    )[:MAX_ITEMS]

    newest = _parse_iso(seen.get(ordered[0]["id"], ""), epoch) if ordered else epoch

    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("Media Selangor — Terkini (BM)")
    # feedgen uses the *last* link() call for the RSS channel <link>, so set the
    # atom self-link first and the human-facing website link last.
    fg.link(href=FEED_SELF_URL, rel="self")
    fg.link(href=SOURCE_URL, rel="alternate")
    fg.description(
        "Berita terkini Media Selangor (Bahasa Melayu), dijana automatik "
        "daripada mediaselangor.com/ms/latest."
    )
    fg.language("ms")
    fg.generator(GENERATOR)
    fg.lastBuildDate(newest)
    fg.docs("https://www.rssboard.org/rss-specification")

    for article in ordered:
        pub = _parse_iso(seen.get(article["id"], ""), newest)
        fe = fg.add_entry(order="append")
        fe.id(article["url"])
        fe.guid(article["url"], permalink=True)
        fe.title(article["title"])
        fe.link(href=article["url"])
        fe.pubDate(pub)
        if article["category"]:
            fe.category(term=article["category"])
        fe.content(content=_description_html(article), type="CDATA")
        if article["thumbnail"]:
            fe.media.content(url=article["thumbnail"], medium="image")
            fe.media.thumbnail(url=article["thumbnail"])

    return fg.rss_str(pretty=True)


def _description_html(article: dict) -> str:
    parts: list[str] = []
    if article["thumbnail"]:
        parts.append(
            f'<p><img src="{article["thumbnail"]}" '
            f'alt="{_escape(article["title"])}" /></p>'
        )
    body = article["summary"] or article["title"]
    parts.append(f"<p>{_escape(body)}</p>")
    meta: list[str] = []
    if article["category"]:
        meta.append(f"Kategori: {_escape(article['category'])}")
    if article["date"]:
        meta.append(f"Tarikh: {_escape(article['date'])}")
    if meta:
        parts.append(f"<p><small>{' · '.join(meta)}</small></p>")
    return "".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)

    try:
        html = fetch_html()
    except Exception as exc:
        log(f"FATAL: fetch failed: {exc}")
        log("leaving existing feed.xml untouched")
        return 1

    articles = parse_articles(html)
    log(f"parsed {len(articles)} article(s)")

    if len(articles) < MIN_ARTICLES:
        log(
            f"FATAL: only {len(articles)} article(s) parsed "
            f"(minimum {MIN_ARTICLES}) — likely a block or redesign"
        )
        log("leaving existing feed.xml untouched")
        return 1

    seen = load_seen()
    update_seen(seen, articles, now)
    seen = prune_seen(seen)
    save_seen(seen)

    xml = build_feed_xml(articles, seen)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    previous = OUTPUT_FILE.read_bytes() if OUTPUT_FILE.exists() else b""
    if xml == previous:
        log("feed unchanged — not rewriting feed.xml")
    else:
        OUTPUT_FILE.write_bytes(xml)
        log(f"wrote {OUTPUT_FILE} ({len(xml)} bytes, {min(len(articles), MAX_ITEMS)} items)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
