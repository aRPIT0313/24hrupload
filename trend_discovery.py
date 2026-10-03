import re
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

DEFAULT_QUERIES = [
    "latest science discoveries animals nature environment health human body",
    "latest history archaeology ancient discoveries inventions culture",
    "latest space astronomy discovery explained science",
    "interesting everyday science why how facts",
]


def _google_news_rss(query):
    url = (
        "https://news.google.com/rss/search?q=" + quote_plus(query) +
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    response = requests.get(
        url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    return response.text


def fetch_trending_headlines(limit=40):
    """Fetch fresh headlines only; article bodies are never stored."""
    headlines, seen = [], set()
    for query in DEFAULT_QUERIES:
        try:
            root = ET.fromstring(_google_news_rss(query))
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
                key = " ".join(title.casefold().split())
                if key and key not in seen:
                    seen.add(key)
                    headlines.append(title)
                if len(headlines) >= limit:
                    return headlines
        except Exception as exc:
            print(f"⚠️ Trend feed unavailable: {exc}")
    return headlines
