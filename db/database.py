"""
db/database.py
SQLite interface for the travel blog pipeline.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "pipeline.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize the database, creating tables if they don't exist."""
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    with get_connection() as conn:
        conn.executescript(schema)
    print(f"Database initialized at {DB_PATH}")


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

def insert_topic(topic: str, source: str, trend_score: int,
                 raw_signal: str, suggested_angle: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO topics (topic, source, trend_score, raw_signal, suggested_angle)
               VALUES (?, ?, ?, ?, ?)""",
            (topic, source, trend_score, raw_signal, suggested_angle)
        )
        return cursor.lastrowid


def topic_exists(topic: str) -> bool:
    """
    Check if a topic has already been processed (any status except rejected).
    Uses fuzzy matching to catch near-duplicates (e.g. 'go hilton team member
    travel program' vs 'hilton team member travel program').
    """
    from difflib import SequenceMatcher
    topic_lower = topic.lower().strip()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT topic FROM topics WHERE status != 'rejected'"
        ).fetchall()
    for row in rows:
        existing = row[0].lower().strip()
        if existing == topic_lower:
            return True
        # Flag as duplicate if 85%+ similar (catches prefix variants, rewordings)
        if SequenceMatcher(None, topic_lower, existing).ratio() >= 0.85:
            return True
    return False


def get_pending_topics() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM topics WHERE status = 'pending' ORDER BY trend_score DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_topic_status(topic_id: int, status: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE topics SET status = ? WHERE id = ?",
            (status, topic_id)
        )


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

def insert_keyword(topic_id: int, target_keyword: str, monthly_volume: int,
                   keyword_difficulty: int, related_keywords: list[str]) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO keywords
               (topic_id, target_keyword, monthly_volume, keyword_difficulty, related_keywords)
               VALUES (?, ?, ?, ?, ?)""",
            (topic_id, target_keyword, monthly_volume,
             keyword_difficulty, json.dumps(related_keywords))
        )
        return cursor.lastrowid


def get_keyword_for_topic(topic_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM keywords WHERE topic_id = ?", (topic_id,)
        ).fetchone()
        if row:
            result = dict(row)
            result["related_keywords"] = json.loads(result["related_keywords"] or "[]")
            return result
        return None


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

def insert_article(topic_id: int, title: str, meta_description: str,
                   word_count: int, html_path: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO articles (topic_id, title, meta_description, word_count, html_path)
               VALUES (?, ?, ?, ?, ?)""",
            (topic_id, title, meta_description, word_count, html_path)
        )
        return cursor.lastrowid


def update_article_image(article_id: int, image_path: str,
                          image_alt_text: str, image_attribution: str):
    with get_connection() as conn:
        conn.execute(
            """UPDATE articles
               SET image_path = ?, image_alt_text = ?, image_attribution = ?
               WHERE id = ?""",
            (image_path, image_alt_text, image_attribution, article_id)
        )


def update_article_published(article_id: int, wordpress_post_id: int, wordpress_url: str):
    with get_connection() as conn:
        conn.execute(
            """UPDATE articles
               SET wordpress_post_id = ?, wordpress_url = ?,
                   status = 'published', published_at = ?
               WHERE id = ?""",
            (wordpress_post_id, wordpress_url,
             datetime.now(timezone.utc).isoformat(), article_id)
        )


def update_article_failed(article_id: int, error_message: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE articles SET status = 'failed', error_message = ? WHERE id = ?",
            (error_message, article_id)
        )


def get_articles_pending_publish() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT a.*, t.topic
               FROM articles a
               JOIN topics t ON a.topic_id = t.id
               WHERE a.status IN ('generated', 'failed')"""
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pipeline Runs
# ---------------------------------------------------------------------------

def start_pipeline_run() -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO pipeline_runs DEFAULT VALUES"
        )
        return cursor.lastrowid


def finish_pipeline_run(run_id: int, topics_discovered: int, topics_validated: int,
                         articles_generated: int, articles_published: int,
                         status: str = "completed", error_message: str = None):
    with get_connection() as conn:
        conn.execute(
            """UPDATE pipeline_runs
               SET finished_at = ?, topics_discovered = ?, topics_validated = ?,
                   articles_generated = ?, articles_published = ?,
                   status = ?, error_message = ?
               WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), topics_discovered, topics_validated,
             articles_generated, articles_published, status, error_message, run_id)
        )


# ---------------------------------------------------------------------------
# Cost Logs
# ---------------------------------------------------------------------------

def insert_cost_logs(run_id: int, entries: list[tuple[str, str, float]]):
    """Persist cost entries for a pipeline run.

    entries: list of (agent, description, usd_cost) tuples
    """
    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO cost_logs (run_id, agent, description, usd_cost) VALUES (?, ?, ?, ?)",
            [(run_id, agent, description, usd_cost) for agent, description, usd_cost in entries],
        )


def get_cost_logs() -> list[dict]:
    """Return all cost_logs rows joined with pipeline_runs, newest run first."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT cl.id, cl.run_id, cl.agent, cl.description, cl.usd_cost, cl.created_at,
                      pr.started_at AS run_started_at, pr.status AS run_status
               FROM cost_logs cl
               LEFT JOIN pipeline_runs pr ON cl.run_id = pr.id
               ORDER BY cl.run_id DESC, cl.id ASC"""
        ).fetchall()
        return [dict(r) for r in rows]
