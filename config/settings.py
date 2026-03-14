"""
config/settings.py
Loads all configuration from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv(override=True)


def require(key: str) -> str:
    """Get an env variable, raise clearly if missing."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Check your .env file against .env.example"
        )
    return value


# Anthropic
ANTHROPIC_API_KEY = require("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")  # research
CLAUDE_ARTICLE_MODEL = os.getenv("CLAUDE_ARTICLE_MODEL", "claude-sonnet-4-6")  # article generation

# DataForSEO
DATAFORSEO_LOGIN = require("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = require("DATAFORSEO_PASSWORD")

# Apify
APIFY_API_TOKEN = require("APIFY_API_TOKEN")

# Unsplash
UNSPLASH_ACCESS_KEY = require("UNSPLASH_ACCESS_KEY")

# Ideogram (optional fallback)
IDEOGRAM_API_KEY = os.getenv("IDEOGRAM_API_KEY", "")

# WordPress
WP_URL = require("WP_URL").rstrip("/")
WP_USERNAME = require("WP_USERNAME")
WP_APP_PASSWORD = require("WP_APP_PASSWORD")

# Database
DB_PATH = os.getenv("DB_PATH", "pipeline.db")

# Pipeline behavior
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "false").lower() == "true"
MIN_SEARCH_VOLUME = int(os.getenv("MIN_SEARCH_VOLUME", "500"))
MAX_KEYWORD_DIFFICULTY = int(os.getenv("MAX_KEYWORD_DIFFICULTY", "60"))
ARTICLES_PER_DAY = int(os.getenv("ARTICLES_PER_DAY", "1"))

# Paths — configurable via env vars for EFS mount in production
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.getenv("PROMPTS_DIR", os.path.join(_base, "prompts"))
OUTPUT_DIR  = os.getenv("OUTPUT_DIR",  os.path.join(_base, "output"))
IMAGES_DIR  = os.getenv("IMAGES_DIR",  os.path.join(OUTPUT_DIR, "images"))
HTML_DIR    = os.getenv("HTML_DIR",    os.path.join(OUTPUT_DIR, "articles"))

# Create output directories if they don't exist
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(HTML_DIR, exist_ok=True)
