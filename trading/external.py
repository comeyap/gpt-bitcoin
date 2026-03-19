"""External data sources: news, Fear & Greed index."""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

import config

logger = logging.getLogger("autotrade")


def get_news_data():
    """Fetch BTC news from Google News RSS (no API key required)."""
    try:
        url = "https://news.google.com/rss/search?q=bitcoin+btc&hl=en&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=config.API_TIMEOUT)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        simplified = []
        for item in items[:10]:
            title = item.findtext("title", "No title")
            source = item.findtext("source", "Unknown")
            pub_date = item.findtext("pubDate", "")
            try:
                ts = int(datetime.strptime(
                    pub_date, "%a, %d %b %Y %H:%M:%S %Z"
                ).timestamp() * 1000)
            except (ValueError, TypeError):
                ts = int(datetime.now().timestamp() * 1000)
            simplified.append((title, source, ts))

        logger.info(f"Fetched {len(simplified)} news items from Google News")
        return str(simplified)
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return "No news data available."


def fetch_fear_and_greed_index(limit=1, date_format=""):
    params = {"limit": limit, "format": "json", "date_format": date_format}
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/",
            params=params, timeout=config.API_TIMEOUT,
        )
        resp.raise_for_status()
        return "".join(str(d) for d in resp.json().get("data", []))
    except Exception as e:
        logger.error(f"Error fetching Fear & Greed Index: {e}")
        return "No fear and greed data available."
