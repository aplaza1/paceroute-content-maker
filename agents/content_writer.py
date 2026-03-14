"""
agents/content_writer.py
Agent 3: Researches topics and generates SEO-optimized articles via Claude.
"""

import os
import sys
import json
import re
import time
import anthropic

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import settings
from db import database
from pipeline.costs import tracker

client = anthropic.Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=300.0,  # 5-minute hard limit per request — prevents indefinite hangs
)

ARTICLE_WRITER_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "prompts", "article_writer.txt"
)

MAX_RETRIES = 3
RETRY_WAIT = 60  # seconds to wait on a 429 before retrying


def load_system_prompt() -> str:
    with open(ARTICLE_WRITER_PROMPT_PATH, "r") as f:
        return f.read()


_SYSTEM_PROMPT = load_system_prompt()


def _call_with_retry(fn):
    """Call fn(), retrying up to MAX_RETRIES times on 429 rate-limit errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except anthropic.RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_WAIT * attempt
            print(f"[content_writer] Rate limit hit, waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)


def research_topic(topic: str, target_keyword: str) -> str:
    """
    Use Claude with web search to gather current facts about the topic.
    Returns a research summary string.
    """
    print(f"[content_writer] Researching: {topic}")

    research_prompt = f"""Research the following travel topic for a blog article:

Topic: {topic}
Target keyword: {target_keyword}

Please gather:
1. Current, practical travel information (visa requirements, costs, transport options)
2. Any recent news or developments that make this topic trending right now
3. Top things to do / key attractions
4. Best time to visit
5. Safety considerations if relevant
6. 2-3 specific facts with numbers (prices, distances, times)

Write a concise research summary (300-400 words) that a travel writer can use as source material.
Cite your sources briefly. Do not write the article itself — just the research notes."""

    response = _call_with_retry(lambda: client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=500,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": research_prompt}],
    ))

    tracker.add_claude("Content Writer", f"research: {topic}", response.usage)

    # Extract text content from response (may include tool use blocks)
    research_text = " ".join(
        block.text for block in response.content
        if hasattr(block, "text")
    )

    return research_text


def generate_article(topic: str, target_keyword: str, related_keywords: list[str],
                     trend_context: str, research_summary: str) -> dict:
    """
    Generate a full article using the article_writer system prompt.
    Returns a dict with title, meta_description, html_content, word_count.
    """
    print(f"[content_writer] Generating article for: {topic}")

    system_prompt = _SYSTEM_PROMPT

    user_message = f"""Please write a travel article with the following inputs:

target_keyword: {target_keyword}
topic: {topic}
related_keywords: {json.dumps(related_keywords)}
trend_context: {trend_context}

research_summary:
{research_summary}

Follow all instructions in the system prompt exactly. Return raw HTML only."""

    response = _call_with_retry(lambda: client.messages.create(
        model=settings.CLAUDE_ARTICLE_MODEL,
        max_tokens=4000,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    ))

    tracker.add_claude("Content Writer", f"article: {topic}", response.usage)

    html_content = response.content[0].text.strip()

    # Extract title from H1
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html_content, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else topic

    # Extract meta description
    meta_match = re.search(
        r'<p class="meta-description"[^>]*>(.*?)</p>',
        html_content, re.IGNORECASE | re.DOTALL
    )
    meta_description = re.sub(r"<[^>]+>", "", meta_match.group(1)).strip() if meta_match else ""

    # Estimate word count (strip tags first)
    plain_text = re.sub(r"<[^>]+>", " ", html_content)
    word_count = len(plain_text.split())

    return {
        "title": title,
        "meta_description": meta_description,
        "html_content": html_content,
        "word_count": word_count,
    }


def save_article_html(article_id: int, html_content: str) -> str:
    """Save article HTML to disk and return the file path."""
    path = os.path.join(settings.HTML_DIR, f"article_{article_id}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return path


def run() -> list[dict]:
    """
    Main entry point for the Content Writer agent.
    Reads validated topics from DB, generates articles, saves to disk and DB.
    """
    print("[content_writer] Starting article generation...")

    # Get validated topics that don't yet have articles
    import sqlite3
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM topics WHERE status IN ('validated', 'failed') LIMIT ?",
        (settings.ARTICLES_PER_DAY,)
    ).fetchall()
    conn.close()
    validated_topics = [dict(r) for r in rows]

    print(f"[content_writer] Topics to write: {len(validated_topics)}")
    generated = []

    for topic_row in validated_topics:
        topic_id = topic_row["id"]
        topic = topic_row["topic"]
        trend_context = topic_row.get("raw_signal", "This destination is currently trending.")

        keyword_data = database.get_keyword_for_topic(topic_id)
        if not keyword_data:
            print(f"[content_writer] No keyword data for topic_id {topic_id}, skipping")
            continue

        target_keyword = keyword_data["target_keyword"]
        related_keywords = keyword_data.get("related_keywords", [])

        try:
            research_summary = research_topic(topic, target_keyword)
            article = generate_article(
                topic=topic,
                target_keyword=target_keyword,
                related_keywords=related_keywords,
                trend_context=trend_context,
                research_summary=research_summary,
            )

            # Save a placeholder article to DB to get an ID, then save HTML
            article_id = database.insert_article(
                topic_id=topic_id,
                title=article["title"],
                meta_description=article["meta_description"],
                word_count=article["word_count"],
                html_path="",  # updated below
            )

            html_path = save_article_html(article_id, article["html_content"])

            # Update the DB with the actual path
            import sqlite3
            conn = sqlite3.connect(settings.DB_PATH)
            conn.execute(
                "UPDATE articles SET html_path = ? WHERE id = ?",
                (html_path, article_id)
            )
            conn.commit()
            conn.close()

            database.update_topic_status(topic_id, "article_generated")

            print(
                f"[content_writer] ✓ Article written: \"{article['title']}\" "
                f"({article['word_count']} words)"
            )

            generated.append({
                "article_id": article_id,
                "topic_id": topic_id,
                "title": article["title"],
                "html_path": html_path,
                "word_count": article["word_count"],
            })

        except Exception as e:
            print(f"[content_writer] ✗ Failed for topic '{topic}': {e}")
            database.update_topic_status(topic_id, "failed")

        if topic_row is not validated_topics[-1]:
            time.sleep(5)

    print(f"[content_writer] Done. {len(generated)} articles generated.")
    tracker.print_agent_summary("Content Writer")
    return generated


if __name__ == "__main__":
    results = run()
    print(json.dumps(results, indent=2))
