"""Outlet feeds (trader/reel/feeds.py): read by our server, they count as sources for the trusted-news rule."""
from datetime import datetime
from types import SimpleNamespace

from trader.reel import feeds, market

NOW = datetime(2026, 10, 12, 16, 0)
ET_XML = b"""<rss><channel>
<item><title>RBI keeps repo rate unchanged</title><link>https://economictimes.indiatimes.com/a.cms</link>
<description><![CDATA[<p>The central bank held rates.</p>]]></description><pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Old story</title><link>https://economictimes.indiatimes.com/old.cms</link>
<pubDate>Wed, 07 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""
GN_XML = b"""<rss><channel>
<item><title>RBI keeps repo rate unchanged - Reuters</title><link>https://news.google.com/rss/articles/abc</link>
<source url="https://www.reuters.com">Reuters</source><pubDate>Mon, 12 Oct 2026 10:05:00 GMT</pubDate></item>
<item><title>India's RBI holds rates as inflation eases</title><link>https://news.google.com/rss/articles/def</link>
<source url="https://www.reuters.com">Reuters</source><pubDate>Mon, 12 Oct 2026 10:06:00 GMT</pubDate></item>
</channel></rss>"""


def get(url):
    if "fail" in url:
        raise OSError("down")
    return SimpleNamespace(status_code=200, content=GN_XML if "news.google" in url else ET_XML)


def test_collect_reads_feeds_keeps_recent_items_and_reports_which_feeds_answer():
    items, status = feeds.collect(NOW, 24, {"ET": "https://et/rss", "GN": "https://news.google.com/x",
                                            "Bad": "https://fail/rss"}, get)
    assert status == {"ET": True, "GN": True, "Bad": False}
    titles = [i["title"] for i in items]
    assert "Old story" not in titles and len(titles) == 3                     # same title de-duplicated
    assert {i["outlet"] for i in items} == {"ET", "Reuters"} and items[0]["time"] == datetime(2026, 10, 12, 15, 36)
    assert "[Reuters]" in feeds.material(items) and "https://news.google.com/rss/articles/def" in feeds.material(items)
    assert feeds.corroborated(items, datetime(2026, 10, 12, 15, 0), market_re()) == 2   # ET + Reuters


def market_re():
    import re
    return re.compile(r"repo rate|rbi", re.I)


def test_feed_items_count_as_two_outlets_for_the_trusted_rule(monkeypatch):
    items, _ = feeds.collect(NOW, 24, {"ET": "https://et/rss", "GN": "https://news.google.com/x"}, get)
    for it in items:
        if "news.google." in it["link"]:
            market.FEED_ALIAS[it["link"]] = it["site"]
    item = market.NewsItem(headline="RBI holds", facts="f", why_it_matters="w", sector="Bank", companies=[],
                           source_urls=["https://economictimes.indiatimes.com/a.cms",
                                        "https://news.google.com/rss/articles/def"])
    kept = market.trusted([item], {it["link"] for it in items})
    assert kept and not kept[0]["official"]                                   # ET + Reuters = two outlets
    content = market.with_feeds("prompt", items)
    assert content[0]["type"] == "document" and content[1]["text"].startswith("prompt")
