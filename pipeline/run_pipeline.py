"""
pipeline/run_pipeline.py
Main CLI entrypoint. Runs all agents end-to-end in sequence.

Usage:
  python pipeline/run_pipeline.py           # full run
  python pipeline/run_pipeline.py --dry-run # trend + keyword only, no writing
"""

import os
import sys
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import database
from agents import trend_spotter, keyword_validator, content_writer, image_generator, publisher
from pipeline.costs import tracker


def run(dry_run: bool = False):
    print(f"\n{'='*60}")
    print(f"  Travel Blog Pipeline — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    database.init_db()
    run_id = database.start_pipeline_run()
    tracker.reset()

    topics_discovered = 0
    topics_validated = 0
    articles_generated = 0
    articles_published = 0

    status = "completed"
    error_message = None

    try:
        # Agent 1: Discover trending topics
        print("--- Step 1: Trend Spotter ---")
        trends = trend_spotter.run()
        topics_discovered = len(trends)

        if not trends:
            print("No new trending topics found today. Exiting.")
            return

        # Agent 2: Validate keywords
        print("\n--- Step 2: Keyword Validator ---")
        validated = keyword_validator.run()
        topics_validated = len(validated)

        if not validated:
            print("No topics passed keyword validation. Exiting.")
            return

        if dry_run:
            print("\n[dry-run] Stopping after keyword validation. No articles written.")
            return

        # Agent 3: Generate articles
        print("\n--- Step 3: Content Writer ---")
        articles = content_writer.run()
        articles_generated = len(articles)

        if not articles:
            print("No articles generated. Exiting.")
            return

        # Agent 4: Source images
        print("\n--- Step 4: Image Generator ---")
        image_generator.run()

        # Agent 5: Publish to WordPress
        print("\n--- Step 5: Publisher ---")
        published = publisher.run()
        articles_published = len(published)

        print(f"\n{'='*60}")
        print(f"  Pipeline Complete")
        print(f"  Topics discovered : {topics_discovered}")
        print(f"  Topics validated  : {topics_validated}")
        print(f"  Articles written  : {articles_generated}")
        print(f"  Articles published: {articles_published}")
        tracker.print_run_summary()
        print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n[pipeline] Fatal error: {e}")
        status = "failed"
        error_message = str(e)
        raise

    finally:
        database.finish_pipeline_run(
            run_id,
            topics_discovered=topics_discovered,
            topics_validated=topics_validated,
            articles_generated=articles_generated,
            articles_published=articles_published,
            status=status,
            error_message=error_message,
        )
        tracker.save_to_db(run_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the travel blog pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run trend spotting and keyword validation only. No articles written.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
