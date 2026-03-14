"""
agents/image_generator.py
Agent 4: Sources or generates a featured image for each article.
"""

import os
import sys
import time
import requests
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import settings
from db import database
from pipeline.costs import tracker

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"
IDEOGRAM_API_URL = "https://api.ideogram.ai/generate"


def _unsplash_search(query: str, exclude_ids: set, retries: int = 3, retry_delay: int = 5) -> Optional[dict]:
    """
    Run a single Unsplash query and return the first qualifying photo whose
    Unsplash photo ID is not in exclude_ids. Returns None if none qualify.
    Retries up to `retries` times on network errors before raising.
    """
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                UNSPLASH_SEARCH_URL,
                params={"query": query, "orientation": "landscape", "per_page": 10},
                headers={"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}"},
                timeout=15,
            )
            response.raise_for_status()
            for photo in response.json().get("results", []):
                photo_id = photo.get("id", "")
                if photo_id in exclude_ids:
                    continue
                width = photo.get("width", 0)
                height = photo.get("height", 0)
                if width >= 1200 and width > height:
                    url = photo["urls"]["regular"]
                    user = photo["user"]["name"]
                    alt = photo.get("alt_description") or f"{query} travel photo"
                    return {
                        "url": url,
                        "photo_id": photo_id,
                        "alt_text": alt.capitalize(),
                        "attribution": f"Photo by {user} on Unsplash",
                        "source": "unsplash",
                    }
            return None  # Got a valid response but no qualifying photo — don't retry
        except Exception as e:
            if attempt < retries:
                print(f"[image_generator] Unsplash attempt {attempt}/{retries} failed: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                raise


def fetch_unsplash_image(topic: str, exclude_ids: set = None) -> Optional[dict]:
    """
    Search Unsplash for a high-quality landscape travel photo.
    Tries progressively broader queries if the full topic returns no results:
      1. "{topic} travel"
      2. Drop the first word (often a brand name), retry
      3. Repeat until one word remains
    exclude_ids: Unsplash photo IDs already used this run — skipped to prevent
    two articles from sharing the same image.
    Returns image metadata or None if no suitable image found.
    """
    if exclude_ids is None:
        exclude_ids = set()

    # Build a list of queries to try: full topic, then drop leading word each time
    words = topic.strip().split()
    queries = []
    for i in range(len(words)):
        phrase = " ".join(words[i:])
        queries.append(f"{phrase} travel")

    try:
        for query in queries:
            result = _unsplash_search(query, exclude_ids)
            if result:
                if query != queries[0]:
                    print(f"[image_generator] Unsplash matched on broader query: '{query}'")
                # Always use the original article topic for alt text — Unsplash's
                # auto-generated alt_description is often generic or unrelated.
                result["alt_text"] = f"{topic.strip().capitalize()} travel photo"
                return result
    except Exception as e:
        print(f"[image_generator] Unsplash fetch failed: {e}")

    return None


def generate_ideogram_image(topic: str) -> Optional[dict]:
    """
    Generate a travel illustration via Ideogram API as a fallback.
    Returns image metadata or None on failure.
    """
    if not settings.IDEOGRAM_API_KEY:
        return None

    try:
        prompt = (
            f"A beautiful, high-quality travel photograph of {topic}. "
            "Wide landscape orientation, vibrant natural colors, inviting atmosphere, "
            "professional travel photography style, no text or watermarks."
        )
        response = requests.post(
            IDEOGRAM_API_URL,
            headers={
                "Api-Key": settings.IDEOGRAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "image_request": {
                    "prompt": prompt,
                    "aspect_ratio": "ASPECT_16_9",
                    "model": "V_2",
                    "magic_prompt_option": "AUTO",
                }
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        image_url = data["data"][0]["url"]

        tracker.add_ideogram("Image Generator")

        return {
            "url": image_url,
            "alt_text": f"Travel illustration of {topic}",
            "attribution": "Image generated by AI",
            "source": "ideogram",
        }

    except Exception as e:
        print(f"[image_generator] Ideogram generation failed: {e}")

    return None


def download_image(url: str, article_id: int) -> str:
    """Download image from URL and save locally. Returns local file path."""
    ext = "jpg"
    if ".png" in url:
        ext = "png"
    elif ".webp" in url:
        ext = "webp"

    filename = f"article_{article_id}.{ext}"
    path = os.path.join(settings.IMAGES_DIR, filename)

    response = requests.get(url, timeout=30, stream=True)
    response.raise_for_status()

    with open(path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    return path


def get_fallback_image(topic: str) -> dict:
    """Return a generic placeholder image path for the category."""
    # Use a local default image if all else fails
    fallback_path = os.path.join(
        os.path.dirname(__file__), "..", "output", "images", "default_travel.jpg"
    )
    return {
        "url": None,
        "local_path": fallback_path,
        "alt_text": f"Travel destination: {topic}",
        "attribution": "",
        "source": "fallback",
    }


def run() -> list[dict]:
    """
    Main entry point for the Image Generator agent.
    Finds articles without images and sources one for each.
    """
    print("[image_generator] Starting image sourcing...")

    articles = database.get_articles_pending_publish()
    needs_image = [a for a in articles if not a.get("image_path")]

    print(f"[image_generator] Articles needing images: {len(needs_image)}")
    results = []
    used_photo_ids: set = set()  # track Unsplash IDs used this run to avoid duplicates

    for article in needs_image:
        article_id = article["id"]

        # Get topic name for this article
        import sqlite3
        conn = sqlite3.connect(settings.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT t.topic FROM topics t JOIN articles a ON t.id = a.topic_id WHERE a.id = ?",
            (article_id,)
        ).fetchone()
        conn.close()

        topic = dict(row)["topic"] if row else "travel"

        print(f"[image_generator] Sourcing image for: {topic}")

        # Try Unsplash first, then Ideogram, then fallback.
        # Pass used_photo_ids so the same photo is never assigned to two articles.
        image_data = fetch_unsplash_image(topic, exclude_ids=used_photo_ids)

        if image_data and image_data.get("photo_id"):
            used_photo_ids.add(image_data["photo_id"])

        if not image_data:
            print(f"[image_generator] No Unsplash result, trying Ideogram...")
            image_data = generate_ideogram_image(topic)

        if not image_data:
            print(f"[image_generator] Using fallback image for: {topic}")
            image_data = get_fallback_image(topic)

        # Download image locally
        local_path = image_data.get("local_path")
        if not local_path and image_data.get("url"):
            try:
                local_path = download_image(image_data["url"], article_id)
            except Exception as e:
                print(f"[image_generator] Download failed: {e}")
                local_path = get_fallback_image(topic)["local_path"]

        database.update_article_image(
            article_id=article_id,
            image_path=local_path,
            image_alt_text=image_data["alt_text"],
            image_attribution=image_data.get("attribution", ""),
        )

        print(f"[image_generator] ✓ Image saved: {local_path}")
        results.append({
            "article_id": article_id,
            "image_path": local_path,
            "source": image_data["source"],
        })

    print(f"[image_generator] Done. {len(results)} images sourced.")
    tracker.print_agent_summary("Image Generator")
    return results


if __name__ == "__main__":
    results = run()
    import json
    print(json.dumps(results, indent=2))
