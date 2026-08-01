import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


FETCH_INTERVAL_MINUTES = 60

# Add or remove feeds freely — name is what shows up as the outlet label
FEEDS = [
    {"name": "NPR",                 "url": "https://feeds.npr.org/1001/rss.xml"},
    {"name": "AP News",             "url": "    https://feedx.net/rss/ap.xml"},
    {"name": "ABC News",            "url": "https://abcnews.com/abcnews/topstories"},
    {"name": "Google",              "url": "https://news.google.com/rss/search?q=site%3Areuters.com&hl=en-US&gl=US&ceid=US%3Aen"},
    {"name": "AP News",             "url": "https://rsshub.app/apnews/topics/apf-topnews"},
    
    {"name": "Bloomberg - Tech",    "url": "https://www.bloomberg.com/feeds/technology/news.rss"},
    
    {"name": "XinHua - World",      "url": "https://www.xinhuanet.com/english/rss/worldrss.xml"},
    {"name": "XinHua - China",      "url": "https://www.xinhuanet.com/english/rss/chinarss.xml"},
    {"name": "XinHua - Sci & Tech", "url": "http://www.xinhuanet.com/english/rss/scirss.xml"},

]

# Valid categories the LLM may assign.
# Expand this list as you add topic-filtering later.
CATEGORIES = [
    "International Politics",
    "US Politics",
    "China",
    "Middle East",
    "Economics",
    "Technology",
    "Science",
    "Climate",
    "Sports",
    "Other",
]
