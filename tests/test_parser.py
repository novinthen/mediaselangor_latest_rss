#!/usr/bin/env python3
"""Offline tests for scripts/build_feed.py.

These exercise the parsing, state, and feed-generation logic against a static
HTML fixture so the pipeline can be verified without network access. Run with::

    python tests/test_parser.py        # zero extra dependencies
    pytest tests/                      # also works under pytest
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR / "scripts"))

import build_feed as bf  # noqa: E402

FIXTURE = (REPO_DIR / "tests" / "fixtures" / "ms_latest_sample.html").read_text(
    encoding="utf-8"
)
NOW = datetime(2026, 6, 14, 9, 0, 0, tzinfo=timezone.utc)


def _articles():
    return bf.parse_articles(FIXTURE)


def test_parses_expected_articles():
    arts = _articles()
    ids = [a["id"] for a in arts]
    # Five distinct articles; the "trending" duplicate of 379390 is collapsed
    # and the /ms/2026/06/ archive link, category links and externals ignored.
    assert ids == ["379390", "379388", "379377", "379350", "379300"], ids


def test_urls_are_absolute_permalinks():
    for a in _articles():
        assert a["url"] == f"https://mediaselangor.com/ms/2026/06/{a['id']}"


def test_title_fallbacks():
    by_id = {a["id"]: a for a in _articles()}
    # h3 title
    assert "Banjir kilat" in by_id["379390"]["title"]
    # title only present as anchor text (no heading in the card)
    assert by_id["379350"]["title"] == "Majlis perasmian pusat komuniti di Klang"


def test_thumbnail_resolution():
    by_id = {a["id"]: a for a in _articles()}
    # relative src -> absolute
    assert by_id["379390"]["thumbnail"] == (
        "https://mediaselangor.com/uploads/2026/06/banjir-kilat.jpg"
    )
    # protocol-relative data-src -> https
    assert by_id["379388"]["thumbnail"] == (
        "https://cdn.mediaselangor.com/uploads/2026/06/mbi.webp"
    )
    # no image in the card
    assert by_id["379377"]["thumbnail"] == ""


def test_category_and_summary():
    by_id = {a["id"]: a for a in _articles()}
    assert by_id["379390"]["category"] == "Berita"
    assert by_id["379388"]["category"] == "Ekonomi"
    assert by_id["379350"]["category"] == "Komuniti"
    assert "Hujan lebat" in by_id["379390"]["summary"]
    assert by_id["379388"]["summary"] == ""  # no excerpt present


def test_seen_state_is_stable_and_ordered():
    arts = _articles()
    seen = {}
    bf.update_seen(seen, arts, NOW)
    # newest (page-top) gets the latest timestamp; ordering preserved via offset
    assert seen["379390"] > seen["379388"] > seen["379300"]
    # A later run with a pre-existing id must not change its first-seen value.
    snapshot = dict(seen)
    later = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    bf.update_seen(seen, arts, later)
    assert seen == snapshot


def test_new_item_sorts_to_top_across_runs():
    arts = _articles()
    seen = {}
    bf.update_seen(seen, arts[1:], NOW)  # first run misses the top article
    later = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
    bf.update_seen(seen, arts, later)    # second run, 379390 is brand new
    assert seen["379390"] > seen["379388"]


def test_prune_caps_state():
    seen = {str(i): f"2026-06-{(i % 28) + 1:02d}T00:00:00+00:00" for i in range(1500)}
    pruned = bf.prune_seen(seen, keep=1000)
    assert len(pruned) == 1000


def test_feed_is_valid_rss():
    arts = _articles()
    seen = {}
    bf.update_seen(seen, arts, NOW)
    xml = bf.build_feed_xml(arts, seen)

    root = ET.fromstring(xml)
    assert root.tag == "rss"
    channel = root.find("channel")
    # channel <link> must point at the website, not the feed itself
    assert channel.find("link").text == bf.SOURCE_URL
    assert channel.find("language").text == "ms"

    items = channel.findall("item")
    assert len(items) == 5
    first = items[0]
    assert first.find("guid").get("isPermaLink") == "true"
    assert first.find("link").text.endswith("/379390")
    # items are newest-first by pubDate
    assert first.find("title").text.startswith("Banjir kilat")


def test_output_is_deterministic():
    arts = _articles()
    seen = {}
    bf.update_seen(seen, arts, NOW)
    assert bf.build_feed_xml(arts, seen) == bf.build_feed_xml(arts, dict(seen))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
