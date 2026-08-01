# Headlines Reborn

Discord bot that pulls RSS headlines from multiple news sources, uses an LLM to deduplicate, categorize, and summarize them, then delivers stories via slash commands or automatic digests.

## How it works

1. Every 60 minutes the bot fetches new articles from configured RSS feeds
2. New headlines are sent to Claude Haiku in batches, which groups them into stories, assigns a category, and writes a short summary
3. Stories are stored in a Postgres database and surfaced via Discord slash commands or posted automatically on a schedule

---

## Commands

### `/digest`
Fetch the latest stories manually.

| Option | Description |
|--------|-------------|
| `category` | Filter by topic, e.g. `Economics` or `China` |
| `limit` | Number of stories to show (default 5, max 20) |

Examples:
```
/digest
/digest category:Technology limit:10
```

---

### `/setup`
Register the current channel to receive automatic digests. Requires **Manage Channels** permission.

---

### `/setinterval`
Set how often the bot auto-posts a digest to the registered channel. Default is 24 hours.

| Option | Description |
|--------|-------------|
| `hours` | Interval in hours (1–168) |

Example:
```
/setinterval hours:6
```

---

### `/categories`
List all available topic categories. Categories that are in your current focus are marked with ✓.

---

### `/focus`
Whitelist specific categories — only those topics will appear in digests.

| Option | Description |
|--------|-------------|
| `topics` | Comma-separated category names, or `all` to reset |

Examples:
```
/focus topics:Economics, Technology
/focus topics:all
/focus                     ← shows current focus
```

---

### `/blacklist`
Block specific categories from appearing in digests. Applies on top of focus.

| Option | Description |
|--------|-------------|
| `topics` | Comma-separated category names, or `clear` to remove all blocks |

Examples:
```
/blacklist topics:Sports, Other
/blacklist topics:clear     ← Clears blacklist filters
/blacklist                  ← shows current blacklist
```

---

### `/sources`
List all available news sources and which ones are active for this server.
NPR: [https://feeds.npr.org/1001/rss.xml](https://feeds.npr.org/1001/rss.xml)

AP News: [https://feedx.net/rss/ap.xml](https://feedx.net/rss/ap.xml)

ABC News: [https://abcnews.com/abcnews/topstories](https://abcnews.com/abcnews/topstories)

Google: [https://news.google.com/rss/search?q=site%3Areuters.com&hl=en-US&gl=US&ceid=US%3Aen](https://news.google.com/rss/search?q=site%3Areuters.com&hl=en-US&gl=US&ceid=US%3Aen)

AP News: [https://rsshub.app/apnews/topics/apf-topnews](https://rsshub.app/apnews/topics/apf-topnews)

Bloomberg - Tech: [https://www.bloomberg.com/feeds/technology/news.rss](https://www.bloomberg.com/feeds/technology/news.rss)

XinHua - World: [https://www.xinhuanet.com/english/rss/worldrss.xml](https://www.xinhuanet.com/english/rss/worldrss.xml)

XinHua - China: [https://www.xinhuanet.com/english/rss/chinarss.xml](https://www.xinhuanet.com/english/rss/chinarss.xml)

XinHua - Sci & Tech: [http://www.xinhuanet.com/english/rss/scirss.xml](http://www.xinhuanet.com/english/rss/scirss.xml)

---

### `/setsources`
Choose which news sources to include in digests. Default is all sources.

| Option | Description |
|--------|-------------|
| `names` | Comma-separated source names, or `all` to reset |

Examples:
```
/setsources names:BBC, NPR  ← Only BBC, NPR will show in digest
/setsources names:all       ← All Sources
/setsources                 ← shows current sources
```

---

### `/status`
Show bot status: last fetch time, next scheduled digest, current focus, blacklist, and sources.

---

## Categories

| Category | Description |
|----------|-------------|
| International Politics | Global geopolitics and diplomacy |
| US Politics | Domestic US political news |
| China | News related to China |
| Middle East | Regional news and conflicts |
| Economics | Markets, trade, and economic policy |
| Technology | Tech industry and innovation |
| Science | Research and scientific developments |
| Climate | Environment and climate news |
| Sports | Sports news |
| Other | Everything else |

---

## Filter logic

Filters are applied in this order:
1. **Blacklist** — always excluded, regardless of other settings
2. **Focus (whitelist)** — if set, only these categories are shown
3. **Sources** — if set, only stories from these outlets are shown

The automatic digest only posts stories that haven't been sent yet. `/digest` always shows the most recent stories regardless of whether they've been auto-posted.

---

## Self-hosting

### Requirements
- Python 3.10+
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- A Postgres database (e.g. Railway)

### Setup
```bash
git clone https://github.com/YinWahYip/Headlines-REBORN
cd Headlines-REBORN
pip install -r requirements.txt
cp .env.example .env
# Fill in your keys in .env
python main.py
```

### Environment variables
```
DISCORD_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
DATABASE_URL=postgresql://...
```

### Adding news sources
Edit `config.py` and add entries to the `FEEDS` list:
```python
FEEDS = [
    {"name": "BBC",  "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    {"name": "NPR",  "url": "https://feeds.npr.org/1001/rss.xml"},
]
```
Any valid RSS feed URL works.
