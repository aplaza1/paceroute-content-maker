"""
agents/trend_spotter.py
Agent 1: Discovers trending travel topics from multiple sources.
"""

import os
import sys
import json
import re
import time
import feedparser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from pytrends.request import TrendReq

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import settings
from db import database
from pipeline.costs import tracker


TRAVEL_RSS_FEEDS = [
    "https://nomadicmatt.com/travel-blog/feed/",     # destination guides
    "https://thepointsguy.com/feed/",                 # travel rewards + destinations
    "https://www.fodors.com/news/feed",               # consumer travel news
    "https://www.cntraveler.com/feed/rss",            # Condé Nast Traveler
]

# Apify actor ID — uses ~ separator as required by the Apify REST API URL format
APIFY_REDDIT_ACTOR = "trudax~reddit-scraper-lite"

TRAVEL_SUBREDDITS = [
    "travel",
    "solotravel",
    "digitalnomad",
    "shoestring",
    "backpacking",
]

GOOGLE_TRENDS_TRAVEL_KEYWORDS = [
    "travel",
    "flights",
    "vacation",
    "best places to visit",
    "travel tips",
]

# RSS topics longer than this are likely news-desk headlines, not actionable topics
_RSS_MAX_TOPIC_LEN = 70


def fetch_rss_topics() -> list[dict]:
    """Pull trending topics from travel RSS feeds."""
    topics = []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)

    for url in TRAVEL_RSS_FEEDS:
        t0 = time.time()
        try:
            feed = feedparser.parse(url)
            count_before = len(topics)
            for entry in feed.entries[:10]:
                # Parse published date
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6])

                if published and published < cutoff:
                    continue

                title = entry.get("title", "").strip()
                summary = entry.get("summary", "")

                # Skip long titles — they're editorial news headlines, not travel topics
                if len(title) > _RSS_MAX_TOPIC_LEN:
                    continue

                topics.append({
                    "topic": title,
                    "source": "rss",
                    "raw_signal": f"Published on {url}: {summary[:100]}",
                    "score": 40,  # baseline score for RSS
                })
            added = len(topics) - count_before
            print(f"[trend_spotter] RSS {url} — {added} entries in {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"[trend_spotter] RSS fetch failed for {url} ({time.time()-t0:.1f}s): {e}")

    return topics


def _fetch_subreddit_free(subreddit: str) -> list[dict]:
    """
    Fallback: fetch trending posts directly from Reddit's public JSON API.
    No API key or Apify account required.
    """
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    t0 = time.time()
    try:
        response = requests.get(
            url,
            params={"limit": 25},
            headers={"User-Agent": "travel-blog-pipeline/1.0 (research bot)"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        posts = data.get("data", {}).get("children", [])

        topics = []
        for child in posts:
            post = child.get("data", {})
            upvotes = post.get("ups", 0)
            if upvotes < 100:
                continue
            title = post.get("title", "")
            trend_score = min(90, 40 + int(upvotes / 200))
            topics.append({
                "topic": _extract_destination(title),
                "source": "reddit",
                "raw_signal": f"r/{subreddit}: {upvotes} upvotes — \"{title}\"",
                "score": trend_score,
            })

        print(
            f"[trend_spotter] Reddit r/{subreddit} (free API) — "
            f"{len(topics)}/{len(posts)} posts kept in {time.time()-t0:.1f}s"
        )
        return topics
    except Exception as e:
        print(f"[trend_spotter] Reddit r/{subreddit} free API also failed ({time.time()-t0:.1f}s): {e}")
        return []


def _fetch_subreddit(subreddit: str) -> list[dict]:
    """
    Fetch trending posts from a single subreddit.
    Tries Apify first; falls back to Reddit's free public JSON API on any error.
    """
    url = f"https://api.apify.com/v2/acts/{APIFY_REDDIT_ACTOR}/run-sync-get-dataset-items"
    t0 = time.time()
    print(f"[trend_spotter] Reddit r/{subreddit} — requesting via Apify (sync, up to 60s)...")
    try:
        payload = {
            "startUrls": [{"url": f"https://www.reddit.com/r/{subreddit}/hot/"}],
            "maxItems": 15,
        }
        response = requests.post(
            url,
            params={"token": settings.APIFY_API_TOKEN},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        posts = response.json()
        tracker.add_apify("Trend Spotter", len(posts))

        topics = []
        for post in posts:
            upvotes = post.get("upVotes", 0)
            if upvotes < 100:
                continue
            title = post.get("title", "")
            trend_score = min(90, 40 + int(upvotes / 200))
            topics.append({
                "topic": _extract_destination(title),
                "source": "reddit",
                "raw_signal": f"r/{subreddit}: {upvotes} upvotes — \"{title}\"",
                "score": trend_score,
            })
        print(
            f"[trend_spotter] Reddit r/{subreddit} (Apify) — "
            f"{len(topics)}/{len(posts)} posts kept in {time.time()-t0:.1f}s"
        )
        return topics

    except Exception as e:
        elapsed = time.time() - t0
        print(
            f"[trend_spotter] Apify failed for r/{subreddit} ({elapsed:.1f}s): {e}. "
            f"Falling back to Reddit free API..."
        )
        return _fetch_subreddit_free(subreddit)


def fetch_reddit_topics() -> list[dict]:
    """Pull trending travel posts from Reddit via Apify (subreddits fetched concurrently)."""
    topics = []
    with ThreadPoolExecutor(max_workers=len(TRAVEL_SUBREDDITS)) as executor:
        futures = {executor.submit(_fetch_subreddit, sr): sr for sr in TRAVEL_SUBREDDITS}
        for future in as_completed(futures):
            topics.extend(future.result())
    return topics


def fetch_google_trends_topics() -> list[dict]:
    """Pull rising travel-related queries from Google Trends."""
    topics = []
    try:
        t0 = time.time()
        print("[trend_spotter] Google Trends — initializing TrendReq...")
        pytrends = TrendReq(hl="en-US", tz=360)
        print(f"[trend_spotter] Google Trends — TrendReq ready in {time.time()-t0:.1f}s, building payload...")

        t1 = time.time()
        pytrends.build_payload(
            GOOGLE_TRENDS_TRAVEL_KEYWORDS[:5],
            cat=179,  # Travel category
            timeframe="now 1-d",
            geo="US",
        )
        print(f"[trend_spotter] Google Trends — payload built in {time.time()-t1:.1f}s, fetching related queries...")

        t2 = time.time()
        # related_queries() makes multiple HTTP requests internally and can hang
        # for many minutes. Cap it at 30 seconds with a thread timeout.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(pytrends.related_queries)
            try:
                related_queries = future.result(timeout=30)
            except FuturesTimeoutError:
                print(
                    f"[trend_spotter] Google Trends related_queries() timed out after 30s "
                    f"(total {time.time()-t0:.1f}s elapsed) — skipping"
                )
                return topics

        print(f"[trend_spotter] Google Trends — related_queries() returned in {time.time()-t2:.1f}s")

        added = 0
        for keyword, data in related_queries.items():
            rising = data.get("rising")
            if rising is not None and not rising.empty:
                for _, row in rising.head(5).iterrows():
                    query = row.get("query", "")
                    value = row.get("value", 0)
                    if query:
                        topics.append({
                            "topic": query,
                            "source": "google_trends",
                            "raw_signal": f"Google Trends rising query: {value}% increase",
                            "score": min(85, 50 + int(value / 10)),
                        })
                        added += 1
        print(f"[trend_spotter] Google Trends — {added} rising queries collected, total {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"[trend_spotter] Google Trends fetch failed: {e}")

    return topics


def _extract_destination(title: str) -> str:
    """
    Attempt to extract a clean destination name from a Reddit post title.
    Falls back to the full title if no clear destination is found.
    """
    # Remove common filler prefixes
    prefixes = [
        r"^just got back from ",
        r"^i visited ",
        r"^traveling to ",
        r"^trip to ",
        r"^guide to ",
        r"^tips for ",
    ]
    cleaned = title.strip()
    for pattern in prefixes:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Truncate at punctuation
    cleaned = re.split(r"[,\-–|?!]", cleaned)[0].strip()

    # Limit length
    return cleaned[:80] if cleaned else title[:80]


def deduplicate_and_rank(raw_topics: list[dict]) -> list[dict]:
    """
    Merge topics that refer to the same destination,
    boost score for cross-source mentions,
    and return top N ranked topics not already in DB.
    """
    merged: dict[str, dict] = {}

    for item in raw_topics:
        topic = item["topic"].lower().strip()

        if not topic or len(topic) < 4:
            continue

        if topic in merged:
            merged[topic]["score"] += 15  # cross-source bonus
            existing_sources = merged[topic]["source"].split(",")
            new_source = item["source"]
            if new_source not in existing_sources:
                merged[topic]["source"] += f",{new_source}"
            merged[topic]["raw_signal"] += f" | {item['raw_signal']}"
        else:
            merged[topic] = {
                "topic": item["topic"],
                "source": item["source"],
                "score": item["score"],
                "raw_signal": item["raw_signal"],
            }

    # Filter out topics already in DB
    fresh = [
        v for v in merged.values()
        if not database.topic_exists(v["topic"])
    ]

    # Sort by score descending
    ranked = sorted(fresh, key=lambda x: x["score"], reverse=True)

    # Pass 3x more candidates than needed — keyword validation will thin them.
    # content_writer enforces the ARTICLES_PER_DAY cap independently.
    return ranked[: max(settings.ARTICLES_PER_DAY * 3, 5)]


def run() -> list[dict]:
    """
    Main entry point for the Trend Spotter agent.
    Returns a list of validated trending topics ready for keyword research.
    """
    t_start = time.time()
    print("[trend_spotter] Starting trend discovery...")

    all_topics = []

    t0 = time.time()
    print(f"[trend_spotter] Fetching RSS feeds ({len(TRAVEL_RSS_FEEDS)} sources)...")
    rss = fetch_rss_topics()
    all_topics += rss
    print(f"[trend_spotter] RSS done: {len(rss)} topics in {time.time()-t0:.1f}s")

    t0 = time.time()
    print(f"[trend_spotter] Fetching Reddit via Apify ({len(TRAVEL_SUBREDDITS)} subreddits, sync endpoint)...")
    reddit = fetch_reddit_topics()
    all_topics += reddit
    print(f"[trend_spotter] Reddit done: {len(reddit)} topics in {time.time()-t0:.1f}s")

    t0 = time.time()
    print("[trend_spotter] Fetching Google Trends...")
    gtrends = fetch_google_trends_topics()
    all_topics += gtrends
    print(f"[trend_spotter] Google Trends done: {len(gtrends)} topics in {time.time()-t0:.1f}s")

    print(f"[trend_spotter] Raw topics collected: {len(all_topics)}")

    ranked = deduplicate_and_rank(all_topics)

    print(f"[trend_spotter] Unique fresh topics after dedup: {len(ranked)}")

    # Persist to DB
    for item in ranked:
        database.insert_topic(
            topic=item["topic"],
            source=item["source"],
            trend_score=item["score"],
            raw_signal=item["raw_signal"],
            suggested_angle="travel guide",  # default, refined later
        )

    print(f"[trend_spotter] Done. {len(ranked)} topics saved to DB. Total time: {time.time()-t_start:.1f}s")
    tracker.print_agent_summary("Trend Spotter")
    return ranked


if __name__ == "__main__":
    database.init_db()
    results = run()
    print(json.dumps(results, indent=2))
