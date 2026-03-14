"""
agents/keyword_validator.py
Agent 2: Validates trending topics against real search demand via DataForSEO.
"""

import os
import sys
import json
import requests
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import settings
from db import database
from pipeline.costs import tracker

DATAFORSEO_API_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"

KEYWORD_TEMPLATES = [
    "{topic}",
    "{topic} travel guide",
    "{topic} travel tips",
    "visiting {topic}",
    "is {topic} worth visiting",
    "how to get to {topic}",
    "{topic} budget travel",
]


def generate_keyword_variants(topic: str) -> list[str]:
    """Generate candidate keyword variants for a topic."""
    variants = []
    for template in KEYWORD_TEMPLATES:
        kw = template.format(topic=topic.lower().strip())
        variants.append(kw)
    return variants[:7]  # cap at 7 to limit API calls


def query_dataforseo(keywords: list[str]) -> list[dict]:
    """
    Query DataForSEO for search volume and keyword difficulty.
    Returns a list of keyword data dicts.
    """
    payload = [
        {
            "keywords": keywords,
            "location_code": 2840,   # United States
            "language_code": "en",
        }
    ]

    try:
        response = requests.post(
            DATAFORSEO_API_URL,
            auth=(settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        tracker.add_dataforseo("Keyword Validator", len(keywords))

        results = []
        for task in data.get("tasks") or []:
            for item in task.get("result") or []:
                if item.get("keyword"):
                    results.append({
                        "keyword": item.get("keyword"),
                        "monthly_volume": item.get("search_volume") or 0,
                        "keyword_difficulty": item.get("competition_index") or 100,
                    })
        return results

    except Exception as e:
        print(f"[keyword_validator] DataForSEO API error: {e}")
        return []


def select_best_keyword(keyword_data: list[dict]) -> Optional[dict]:
    """
    From a list of keyword candidates, pick the best one based on:
    - monthly_volume >= MIN_SEARCH_VOLUME
    - keyword_difficulty <= MAX_KEYWORD_DIFFICULTY
    - highest volume among qualifying keywords
    """
    qualifying = [
        kw for kw in keyword_data
        if kw["monthly_volume"] >= settings.MIN_SEARCH_VOLUME
        and kw["keyword_difficulty"] <= settings.MAX_KEYWORD_DIFFICULTY
    ]

    if not qualifying:
        return None

    return max(qualifying, key=lambda x: x["monthly_volume"])


def get_related_keywords(keyword_data: list[dict], best_keyword: str) -> list[str]:
    """Return related keywords excluding the chosen target keyword."""
    return [
        kw["keyword"] for kw in keyword_data
        if kw["keyword"] != best_keyword
        and kw["monthly_volume"] >= 100  # low bar for related keywords
    ][:5]


def run() -> list[dict]:
    """
    Main entry point for the Keyword Validator agent.
    Reads pending topics from DB, validates each, updates DB status.
    Returns list of validated topics with keyword data.
    """
    print("[keyword_validator] Starting keyword validation...")

    pending = database.get_pending_topics()
    print(f"[keyword_validator] Topics to validate: {len(pending)}")

    validated = []

    for topic_row in pending:
        topic_id = topic_row["id"]
        topic = topic_row["topic"]

        print(f"[keyword_validator] Validating: {topic}")

        variants = generate_keyword_variants(topic)
        keyword_data = query_dataforseo(variants)

        if not keyword_data:
            print(f"[keyword_validator] No data returned for: {topic}")
            database.update_topic_status(topic_id, "rejected")
            continue

        best = select_best_keyword(keyword_data)

        if not best:
            print(f"[keyword_validator] No qualifying keyword for: {topic} — rejecting")
            database.update_topic_status(topic_id, "rejected")
            continue

        related = get_related_keywords(keyword_data, best["keyword"])

        database.insert_keyword(
            topic_id=topic_id,
            target_keyword=best["keyword"],
            monthly_volume=best["monthly_volume"],
            keyword_difficulty=best["keyword_difficulty"],
            related_keywords=related,
        )
        database.update_topic_status(topic_id, "validated")

        validated.append({
            "topic_id": topic_id,
            "topic": topic,
            "target_keyword": best["keyword"],
            "monthly_volume": best["monthly_volume"],
            "keyword_difficulty": best["keyword_difficulty"],
            "related_keywords": related,
        })

        print(
            f"[keyword_validator] ✓ {topic} → \"{best['keyword']}\" "
            f"(vol: {best['monthly_volume']}, diff: {best['keyword_difficulty']})"
        )

    print(f"[keyword_validator] Done. {len(validated)}/{len(pending)} topics validated.")
    tracker.print_agent_summary("Keyword Validator")
    return validated


if __name__ == "__main__":
    results = run()
    print(json.dumps(results, indent=2))
