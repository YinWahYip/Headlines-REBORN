import socket
import feedparser
from datetime import datetime
from config import FEEDS
import db

FEED_TIMEOUT_SEC = 15  # max seconds to wait for any single feed

MAX_PER_FEED = 2  # hard cap on new articles pulled per source per fetch

# Skip entries that are live streams or videos rather than readable articles
SKIP_URL_PATTERNS = ["live", "video", "stream", "watch", "broadcast"]
SKIP_TITLE_PATTERNS = ["live:", "watch live", "watch:", "live updates", "live stream"]


def _is_article(url: str, title: str) -> bool:
    url_lower = url.lower()
    title_lower = title.lower()
    if any(p in url_lower for p in SKIP_URL_PATTERNS):
        return False
    if any(p in title_lower for p in SKIP_TITLE_PATTERNS):
        return False
    return True


def fetch_new_articles() -> list[dict]:
    """
    Poll every configured RSS feed. Return only articles not yet in the DB.
    Each dict: {id, title, source, url}
    """
    new_articles = []

    for feed_cfg in FEEDS:
        try:
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(FEED_TIMEOUT_SEC)
            parsed = feedparser.parse(feed_cfg["url"])
            socket.setdefaulttimeout(old_timeout)
        except Exception as e:
            socket.setdefaulttimeout(old_timeout)
            print(f"[fetcher] Failed to fetch {feed_cfg['name']}: {e}")
            continue

        feed_count = 0
        for entry in parsed.entries:
            if feed_count >= MAX_PER_FEED:
                break
            url = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not url or not title:
                continue
            if not _is_article(url, title):
                continue
            if db.url_seen(url):
                continue

            published = entry.get("published", datetime.utcnow().isoformat())

            article_id = db.insert_article(url, title, feed_cfg["name"], str(published))
            new_articles.append({
                "id": article_id,
                "title": title,
                "source": feed_cfg["name"],
                "url": url,
            })
            feed_count += 1

    print(f"[fetcher] {len(new_articles)} new articles across {len(FEEDS)} feeds")
    return new_articles
