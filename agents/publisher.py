"""
agents/publisher.py
Agent 5: Publishes finished articles to WordPress via REST API.
"""

import os
import sys
import json
import re
import base64
import mimetypes
import requests
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import settings
from db import database


def get_auth_header() -> dict:
    """Build Basic Auth header from WP credentials."""
    credentials = f"{settings.WP_USERNAME}:{settings.WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_auth():
    """Print auth debug info and test the WordPress connection."""
    credentials = f"{settings.WP_USERNAME}:{settings.WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode()
    print(f"[publisher] WP_URL      : {settings.WP_URL}")
    print(f"[publisher] WP_USERNAME : {settings.WP_USERNAME}")
    print(f"[publisher] WP_APP_PASSWORD (len={len(settings.WP_APP_PASSWORD)}): {settings.WP_APP_PASSWORD}")
    print(f"[publisher] Auth header : Basic {token}")
    print(f"[publisher] Decoded     : {credentials}")
    try:
        r = requests.get(
            f"{settings.WP_URL}/wp-json/wp/v2/users/me",
            headers=get_auth_header(),
            timeout=10,
        )
        print(f"[publisher] /users/me status: {r.status_code}")
        print(f"[publisher] Response: {r.text[:300]}")
    except Exception as e:
        print(f"[publisher] Request failed: {e}")


def upload_image(image_path: str, alt_text: str) -> Optional[int]:
    """
    Upload a local image to WordPress Media Library.
    Returns the media ID on success, None on failure.
    """
    if not image_path or not os.path.exists(image_path):
        return None

    url = f"{settings.WP_URL}/wp-json/wp/v2/media"
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"
    filename = Path(image_path).name

    try:
        with open(image_path, "rb") as f:
            response = requests.post(
                url,
                headers={
                    **get_auth_header(),
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type": mime_type,
                },
                data=f.read(),
                timeout=30,
            )
        response.raise_for_status()
        media_id = response.json().get("id")

        # Set alt text
        if media_id and alt_text:
            requests.post(
                f"{url}/{media_id}",
                headers={**get_auth_header(), "Content-Type": "application/json"},
                json={"alt_text": alt_text},
                timeout=15,
            )

        return media_id

    except Exception as e:
        print(f"[publisher] Image upload failed: {e}")
        return None


def get_or_create_category(name: str) -> int:
    """Get WordPress category ID by name, creating it if it doesn't exist."""
    url = f"{settings.WP_URL}/wp-json/wp/v2/categories"
    headers = get_auth_header()

    try:
        # Check if category exists
        response = requests.get(url, params={"search": name}, headers=headers, timeout=15)
        response.raise_for_status()
        results = response.json()
        for cat in results:
            if cat["name"].lower() == name.lower():
                return cat["id"]

        # Create it
        response = requests.post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            json={"name": name},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["id"]

    except Exception as e:
        print(f"[publisher] Category lookup/create failed: {e}")
        return 1  # fallback to default category (Uncategorized)


def generate_faq_schema(html: str) -> str:
    """
    Parse the FAQ section of an article and return a JSON-LD <script> block
    for FAQ schema markup. Returns an empty string if no FAQ section is found.

    Expects the article structure produced by article_writer.txt:
      <h2>Frequently Asked Questions About ...</h2>
      <h3>Question?</h3>
      <p>Answer.</p>
      ...
    """
    # Isolate the FAQ section (between the FAQ h2 and the next h2)
    faq_section_match = re.search(
        r'<h2[^>]*>[^<]*Frequently Asked Questions[^<]*</h2>(.*?)(?=<h2|$)',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not faq_section_match:
        return ''

    section = faq_section_match.group(1)

    # Extract h3/p pairs
    pairs = re.findall(
        r'<h3[^>]*>(.*?)</h3>\s*<p[^>]*>(.*?)</p>',
        section,
        re.IGNORECASE | re.DOTALL,
    )
    if not pairs:
        return ''

    entities = []
    for question, answer in pairs:
        q_text = re.sub(r'<[^>]+>', '', question).strip()
        a_text = re.sub(r'<[^>]+>', '', answer).strip()
        if q_text and a_text:
            entities.append({
                '@type': 'Question',
                'name': q_text,
                'acceptedAnswer': {'@type': 'Answer', 'text': a_text},
            })

    if not entities:
        return ''

    schema = {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': entities,
    }

    return f'\n<script type="application/ld+json">\n{json.dumps(schema, indent=2)}\n</script>\n'


def remove_standalone_placeholders(html: str) -> str:
    """
    Remove <p> tags whose only content is a single link placeholder.
    These are artifacts of Claude generating [[INTERNAL_LINK: ...]] or
    [[EXTERNAL_LINK: ...]] as a standalone paragraph instead of inline.
    After resolution they'd become orphaned bare-text paragraphs.
    """
    return re.sub(
        r'<p[^>]*>\s*\[\[(?:INTERNAL|EXTERNAL)_LINK:[^\]]+\]\]\s*</p>',
        '',
        html,
        flags=re.IGNORECASE,
    )


def resolve_internal_links(html: str, exclude_post_id: Optional[int] = None) -> str:
    """
    Replace [[INTERNAL_LINK: topic]] placeholders with real WordPress links.

    Two-step process:
    1. Remove any placeholder that immediately follows sentence-ending punctuation
       (trailing references). These are always removed — even if WP finds a fuzzy
       match — because the anchor text is ungrammatical without proper sentence context.
    2. Resolve remaining inline placeholders via WP search. exclude_post_id prevents
       the current article from linking to itself via fuzzy search false-positives.
       If no valid post is found, keep the anchor text so the sentence still reads.
    """
    # Step 1: strip trailing-reference placeholders (preceded by . ! or ?)
    html = re.sub(
        r'(?<=[.!?])\s*\[\[INTERNAL_LINK:[^\]]+\]\]',
        '',
        html,
    )

    # Step 2: resolve remaining inline placeholders
    pattern = r"\[\[INTERNAL_LINK:\s*([^\]]+)\]\]"

    def replace_link(match):
        topic = match.group(1).strip()
        try:
            search_url = f"{settings.WP_URL}/wp-json/wp/v2/posts"
            response = requests.get(
                search_url,
                params={"search": topic, "per_page": 5},
                headers=get_auth_header(),
                timeout=10,
            )
            posts = response.json()
            if isinstance(posts, list):
                for post in posts:
                    # Skip the article being processed to avoid self-referential links
                    if exclude_post_id and post.get("id") == exclude_post_id:
                        continue
                    if post.get("link"):
                        return f'<a href="{post["link"]}">{topic}</a>'
        except Exception:
            pass
        # No valid match — keep anchor text (mid-sentence, reads naturally)
        return topic

    return re.sub(pattern, replace_link, html)


def _extract_merchant(url: str) -> str:
    """Derive a merchant label from a URL (e.g. 'chase' from 'https://www.chase.com/travel')."""
    from urllib.parse import urlparse
    try:
        host = urlparse(url).netloc or url.split("/")[0]
        host = re.sub(r"^www\.", "", host)
        parts = host.split(".")
        # Use second-to-last part so subdomains like travel.capitalone.com → 'capitalone'
        return parts[-2].lower() if len(parts) >= 2 else parts[0].lower()
    except Exception:
        return "unknown"


def resolve_external_links(html: str) -> str:
    """
    Replace [[EXTERNAL_LINK: anchor | source]] placeholders.
    - If source is a full URL (http/https), link to it directly.
    - If source looks like a bare domain (has a dot, no spaces), prepend https://.
    - Otherwise remove the placeholder and keep only the anchor text.
    All resolved links receive data-affiliate-ready and data-merchant attributes
    so they can be swapped for affiliate links systematically later.

    Also fixes malformed patterns where Claude embedded a placeholder inside an
    href attribute: <a href="[[EXTERNAL_LINK: ... | url]]">anchor text</a>.
    These are rewritten to proper resolved links before normal processing runs.
    """
    # Pre-pass: fix href="[[EXTERNAL_LINK: ... | url]]">anchor</a> patterns.
    # Claude occasionally wraps the placeholder in an <a> tag directly instead of
    # using it as inline text. The URL comes from the placeholder; the anchor text
    # comes from the tag's inner content (which is more descriptive than the placeholder name).
    href_pattern = re.compile(
        r'<a\s+href="\[\[EXTERNAL_LINK:\s*[^|]+\|([^\]]+)\]\]"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    def fix_href_placeholder(m):
        source = m.group(1).strip()
        anchor = m.group(2).strip()
        if source.startswith(("http://", "https://")):
            url = source
        elif "." in source and " " not in source:
            url = f"https://{source}"
        else:
            return anchor  # can't resolve — keep visible text only
        merchant = _extract_merchant(url)
        return (
            f'<a href="{url}" target="_blank" rel="noopener"'
            f' data-affiliate-ready="true" data-merchant="{merchant}">{anchor}</a>'
        )

    html = href_pattern.sub(fix_href_placeholder, html)

    # Normal pass: inline [[EXTERNAL_LINK: anchor | source]] placeholders.
    pattern = r"\[\[EXTERNAL_LINK:\s*([^|]+)\|([^\]]+)\]\]"

    def replace_link(match):
        anchor = match.group(1).strip()
        source = match.group(2).strip()

        if source.startswith(("http://", "https://")):
            url = source
        elif "." in source and " " not in source:
            url = f"https://{source}"
        else:
            return anchor

        merchant = _extract_merchant(url)
        return (
            f'<a href="{url}" target="_blank" rel="noopener"'
            f' data-affiliate-ready="true" data-merchant="{merchant}">{anchor}</a>'
        )

    return re.sub(pattern, replace_link, html)


# Ordered rules for category assignment. First match wins.
# Category IDs: Travel Guides=3, Travel Rewards=5, Airlines=6, Vacation Clubs=7
_CATEGORY_RULES = [
    (["vacation club", "vacation ownership", "vacation resort", "armed forces vacation"],
     "Vacation Clubs"),
    (["airline", "airlines", "southwest", "jetblue", "flight"],
     "Airlines"),
    (["travel portal", "capital one travel", "chase travel", "american express travel",
      "chase sapphire", "venture card", "travel credit", "edit by chase"],
     "Travel Rewards"),
]
_DEFAULT_CATEGORY = "Travel Guides"


def get_category_for_topic(topic: str) -> int:
    """Return the WP category ID that best fits this topic string."""
    topic_lower = topic.lower()
    for keywords, category_name in _CATEGORY_RULES:
        if any(kw in topic_lower for kw in keywords):
            return get_or_create_category(category_name)
    return get_or_create_category(_DEFAULT_CATEGORY)


def publish_post(article: dict, media_id: Optional[int], category_id: int) -> dict:
    """
    Create a WordPress post via REST API.
    Returns the created post data.
    """
    url = f"{settings.WP_URL}/wp-json/wp/v2/posts"

    html = article["html_content"]
    # Strip the H1 title — WordPress renders the post title as H1 automatically
    html = re.sub(r"<h1[^>]*>.*?</h1>", "", html, count=1, flags=re.IGNORECASE | re.DOTALL)
    # Strip the meta-description paragraph — used for SEO only, must not appear in body
    html = re.sub(r'<p[^>]*class="meta-description"[^>]*>.*?</p>', "", html, count=1, flags=re.IGNORECASE | re.DOTALL)
    # Remove <p> tags that are solely a link placeholder (avoids orphaned text)
    html = remove_standalone_placeholders(html)
    # Pass post_id so the resolver can exclude the current post from WP search results
    html = resolve_internal_links(html, exclude_post_id=article.get("wp_post_id"))
    html = resolve_external_links(html)
    # Append FAQ JSON-LD schema for Google rich results
    html += generate_faq_schema(html)

    status = "publish" if settings.AUTO_PUBLISH else "draft"

    payload = {
        "title": article["title"],
        "content": html,
        "status": status,
        "categories": [category_id],
    }

    if media_id:
        payload["featured_media"] = media_id

    headers = {**get_auth_header(), "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    post = response.json()

    # Set meta description and focus keyword via Rank Math (Yoast is not installed)
    meta_description = article.get("meta_description", "")
    focus_keyword = article.get("target_keyword", "")
    rank_math_meta = {}
    if meta_description:
        rank_math_meta["rank_math_description"] = meta_description
    if focus_keyword:
        rank_math_meta["rank_math_focus_keyword"] = focus_keyword
    if rank_math_meta:
        try:
            requests.post(
                f"{settings.WP_URL}/wp-json/rankmath/v1/updateMeta",
                headers={**get_auth_header(), "Content-Type": "application/json"},
                json={
                    "objectID": post["id"],
                    "objectType": "post",
                    "meta": rank_math_meta,
                },
                timeout=15,
            )
        except Exception as e:
            print(f"[publisher] Warning: failed to set Rank Math meta: {e}")

    return post


def run() -> list[dict]:
    """
    Main entry point for the Publisher agent.
    Reads generated articles from DB and publishes each to WordPress.
    """
    print("[publisher] Starting WordPress publishing...")

    articles = database.get_articles_pending_publish()
    print(f"[publisher] Articles to publish: {len(articles)}")

    published = []

    for article_row in articles:
        article_id = article_row["id"]
        title = article_row["title"]
        topic = article_row.get("topic", "")
        category_id = get_category_for_topic(topic)

        print(f"[publisher] Publishing: {title}")

        # Skip articles without a real image
        image_path = article_row.get("image_path", "")
        if not image_path or image_path.endswith("default_travel.jpg"):
            print(f"[publisher] Skipping '{title}': no real image sourced")
            continue

        # Load HTML from disk
        html_path = article_row.get("html_path", "")
        if not html_path or not os.path.exists(html_path):
            print(f"[publisher] HTML file not found: {html_path}")
            database.update_article_failed(article_id, "HTML file missing")
            continue

        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        keyword_data = database.get_keyword_for_topic(article_row["topic_id"])
        target_keyword = keyword_data["target_keyword"] if keyword_data else ""

        article_data = {
            "title": title,
            "html_content": html_content,
            "meta_description": article_row.get("meta_description", ""),
            "target_keyword": target_keyword,
        }

        # Upload featured image
        media_id = upload_image(
            article_row.get("image_path"),
            article_row.get("image_alt_text", ""),
        )

        try:
            post = publish_post(article_data, media_id, category_id)
            wp_id = post["id"]
            wp_url = post["link"]

            database.update_article_published(article_id, wp_id, wp_url)

            status_label = "published" if settings.AUTO_PUBLISH else "saved as draft"
            print(f"[publisher] ✓ {status_label}: {wp_url}")

            published.append({
                "article_id": article_id,
                "wordpress_post_id": wp_id,
                "url": wp_url,
                "status": "published" if settings.AUTO_PUBLISH else "draft",
            })

        except Exception as e:
            error = str(e)
            print(f"[publisher] ✗ Failed to publish '{title}': {error}")
            database.update_article_failed(article_id, error)

    print(f"[publisher] Done. {len(published)} articles sent to WordPress.")
    return published


if __name__ == "__main__":
    results = run()
    print(json.dumps(results, indent=2))
