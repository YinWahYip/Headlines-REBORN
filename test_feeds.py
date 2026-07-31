import feedparser

FEEDS = [
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    {"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"},
]

for feed in FEEDS:
    parsed = feedparser.parse(feed["url"])
    print(f"{feed['name']}: status={parsed.get('status', 'N/A')} entries={len(parsed.entries)}")
    if parsed.entries:
        print(f"  First: {parsed.entries[0].get('title', 'no title')}")
    if parsed.bozo:
        print(f"  Parse error: {parsed.bozo_exception}")
