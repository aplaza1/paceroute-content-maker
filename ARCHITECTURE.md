# Travel Trend Blog — AI Agent Pipeline Architecture

## Overview

An automated, trend-reactive travel blog that monitors the web for trending travel topics, validates them against search demand, generates SEO-optimized articles using AI, and publishes them to WordPress — with minimal human intervention.

**Goal:** Publish 3–5 high-quality travel articles per day, targeting trending topics with real search volume, to drive ad revenue via Mediavine/Raptive.

---

## Stack

| Layer | Technology | Cost |
|---|---|---|
| Language | Python 3.11+ | Free |
| Orchestration | n8n (self-hosted) | Free |
| Hosting | Hetzner CX22 VPS (2 vCPU, 4GB RAM) | ~$4/month |
| WordPress | Self-hosted on same VPS or Hostinger | ~$3–6/month |
| AI Content | Anthropic Claude API (claude-sonnet-4-6) | Pay per use (~$0.10–0.30/article) |
| Keyword Data | DataForSEO API | Pay per use (~$0.01–0.05/query) |
| Web Scraping | Apify (free tier or $49/month) | Free tier to start |
| Image Generation | Ideogram API or Unsplash API | Free tier available |
| Database | SQLite (local) → PostgreSQL (if scaled) | Free |

**Estimated monthly infrastructure cost: ~$15–30/month**

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        n8n Scheduler                        │
│                    (runs daily at 6am UTC)                  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Agent 1: Trend Spotter                     │
│  Monitors Reddit, Google Trends, TikTok, RSS feeds         │
│  Output: ranked list of trending travel topics              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               Agent 2: Keyword Validator                    │
│  Cross-references topics with DataForSEO search volume     │
│  Filters: volume > 500/mo, competition < 0.6               │
│  Output: validated topic list with keyword data             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                Agent 3: Content Writer                      │
│  Web research pass → Claude article generation             │
│  Output: full SEO-optimized article in Markdown/HTML        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               Agent 4: Image Generator                      │
│  Generates or sources a featured image per article         │
│  Output: image file + alt text                              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                 Agent 5: Publisher                          │
│  Pushes article + image to WordPress via REST API          │
│  Sets categories, tags, SEO meta, schedules publication    │
└─────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
travel-blog-pipeline/
│
├── agents/
│   ├── trend_spotter.py        # Agent 1: scrape and rank trending topics
│   ├── keyword_validator.py    # Agent 2: validate topics with search volume
│   ├── content_writer.py       # Agent 3: research + generate article via Claude
│   ├── image_generator.py      # Agent 4: fetch or generate featured image
│   └── publisher.py            # Agent 5: push to WordPress
│
├── pipeline/
│   └── run_pipeline.py         # Orchestrates all agents end-to-end (CLI entrypoint)
│
├── db/
│   ├── database.py             # SQLite interface (topics, articles, publish log)
│   └── schema.sql              # DB schema
│
├── prompts/
│   ├── article_writer.txt      # Claude system prompt for article generation
│   └── topic_evaluator.txt     # Claude prompt for topic scoring/relevance
│
├── config/
│   └── settings.py             # All API keys, thresholds, toggles (loaded from .env)
│
├── n8n/
│   └── workflow.json           # Exported n8n workflow (import to your n8n instance)
│
├── tests/
│   ├── test_trend_spotter.py
│   ├── test_keyword_validator.py
│   └── test_content_writer.py
│
├── .env.example                # Template for environment variables
├── requirements.txt
└── README.md
```

---

## Agent Specifications

### Agent 1 — Trend Spotter (`trend_spotter.py`)

**Purpose:** Identify trending travel topics from multiple sources daily.

**Data Sources:**
- Reddit: r/travel, r/solotravel, r/digitalnomad, r/shoestring (via Apify Reddit scraper)
- Google Trends: travel category, rising queries (via `pytrends` library)
- RSS Feeds: Lonely Planet News, Travel + Leisure, The Points Guy, Skift
- TikTok: trending travel hashtags (via Apify TikTok scraper)

**Logic:**
1. Pull top 50 posts/items from each source from the past 24 hours
2. Score each topic by: engagement velocity, cross-source mentions, novelty (not already in DB)
3. Deduplicate and normalize topic names
4. Return top 10 ranked topics

**Output Schema:**
```python
{
  "topic": "Albanian Riviera",
  "source": ["reddit", "tiktok"],
  "score": 87,
  "raw_signal": "2.4k upvotes in 18hrs on r/travel",
  "suggested_angle": "budget travel guide"
}
```

---

### Agent 2 — Keyword Validator (`keyword_validator.py`)

**Purpose:** Filter trending topics to only those with real search demand.

**Data Source:** DataForSEO API (Keywords Data endpoint)

**Logic:**
1. For each topic from Agent 1, generate 3–5 candidate keyword variants
2. Query DataForSEO for monthly search volume and keyword difficulty
3. Apply filters:
   - Monthly volume ≥ 500
   - Keyword difficulty ≤ 60 (0–100 scale)
4. Select best-performing keyword variant per topic
5. Return validated topics with chosen target keyword

**Output Schema:**
```python
{
  "topic": "Albanian Riviera",
  "target_keyword": "Albanian Riviera travel guide",
  "monthly_volume": 4400,
  "keyword_difficulty": 22,
  "related_keywords": ["albania beach", "ksamil albania", "albania budget travel"]
}
```

---

### Agent 3 — Content Writer (`content_writer.py`)

**Purpose:** Research and generate a full SEO-optimized article for each validated topic.

**Research Step:**
- Use Claude's web search tool (or Perplexity API as alternative) to gather:
  - Current facts about the destination/topic
  - Recent news or events relevant to the trend
  - Practical travel info (visa, cost, transport, best time to visit)

**Generation Step:**
- Send research summary + target keyword to Claude API
- Use the system prompt in `prompts/article_writer.txt`
- Claude generates a structured article

**Article Structure:**
```
- Title (H1, includes target keyword)
- Meta description (150–160 chars)
- Introduction (hook + what article covers)
- 4–6 H2 sections (practical, informative, scannable)
- FAQ section (3–5 questions, targets "People Also Ask")
- Conclusion with CTA
```

**Content Guidelines (in system prompt):**
- Minimum 1,200 words, target 1,800
- Synthesize and rewrite — never copy source material
- Write in second person ("you"), friendly and practical tone
- Include internal link placeholders: `[[INTERNAL_LINK: budget travel tips]]`
- Include 2–3 suggested external authority links
- Output valid HTML with proper heading tags

**Output Schema:**
```python
{
  "title": "The Ultimate Albanian Riviera Travel Guide (2025)",
  "meta_description": "Planning a trip to the Albanian Riviera? ...",
  "html_content": "<h1>...</h1><p>...</p>...",
  "word_count": 1847,
  "target_keyword": "Albanian Riviera travel guide",
  "tags": ["Albania", "Europe", "Budget Travel", "Beach"],
  "category": "Destination Guides"
}
```

---

### Agent 4 — Image Generator (`image_generator.py`)

**Purpose:** Source or generate a featured image for each article.

**Strategy (in order of preference):**
1. **Unsplash API** — search for high-quality free photos matching the topic (fastest, free)
2. **Ideogram API** — generate a travel illustration if no good Unsplash match found
3. **Fallback** — use a category-default placeholder image

**Logic:**
- Search Unsplash with topic keywords
- Select first result with landscape orientation (min 1200px wide)
- Download and store locally
- Generate descriptive alt text via Claude

**Output Schema:**
```python
{
  "image_path": "/tmp/images/albanian-riviera.jpg",
  "alt_text": "Crystal clear turquoise water at Ksamil Beach on the Albanian Riviera",
  "attribution": "Photo by John Doe on Unsplash",
  "source": "unsplash"
}
```

---

### Agent 5 — Publisher (`publisher.py`)

**Purpose:** Publish the finished article to WordPress.

**Method:** WordPress REST API (`/wp-json/wp/v2/posts`)

**Steps:**
1. Upload featured image to WordPress Media Library
2. Create post with: title, content, meta description (via Yoast/RankMath API), tags, category, featured image ID
3. Set status to `draft` (default) or `publish` based on `AUTO_PUBLISH` env variable
4. Log publication to DB with post ID and WordPress URL

**Human Review Toggle:**
- `AUTO_PUBLISH=false` → posts go to WordPress as drafts for manual review
- `AUTO_PUBLISH=true` → posts publish immediately (enable only after validating quality)

**Output Schema:**
```python
{
  "wordpress_post_id": 1042,
  "url": "https://yourblog.com/albanian-riviera-travel-guide",
  "status": "draft",
  "published_at": "2025-10-14T06:32:00Z"
}
```

---

## Database Schema (`schema.sql`)

```sql
-- Tracks all discovered trending topics
CREATE TABLE topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  source TEXT,
  trend_score INTEGER,
  discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status TEXT DEFAULT 'pending' -- pending | validated | rejected | published
);

-- Tracks validated keywords per topic
CREATE TABLE keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER REFERENCES topics(id),
  target_keyword TEXT NOT NULL,
  monthly_volume INTEGER,
  keyword_difficulty INTEGER,
  related_keywords TEXT -- JSON array
);

-- Tracks generated and published articles
CREATE TABLE articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER REFERENCES topics(id),
  title TEXT,
  word_count INTEGER,
  html_path TEXT, -- local path to saved HTML
  wordpress_post_id INTEGER,
  wordpress_url TEXT,
  status TEXT DEFAULT 'generated', -- generated | published | failed
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  published_at TIMESTAMP
);
```

---

## Environment Variables (`.env.example`)

```bash
# Anthropic
ANTHROPIC_API_KEY=your_key_here

# DataForSEO
DATAFORSEO_LOGIN=your_login
DATAFORSEO_PASSWORD=your_password

# Apify
APIFY_API_TOKEN=your_token

# Unsplash
UNSPLASH_ACCESS_KEY=your_key

# Ideogram (optional)
IDEOGRAM_API_KEY=your_key

# WordPress
WP_URL=https://yourblog.com
WP_USERNAME=your_wp_username
WP_APP_PASSWORD=your_wp_application_password

# Pipeline Settings
AUTO_PUBLISH=false              # true = publish immediately, false = save as draft
MIN_SEARCH_VOLUME=500           # minimum monthly searches to accept a keyword
MAX_KEYWORD_DIFFICULTY=60       # 0-100, lower = easier to rank
ARTICLES_PER_DAY=5              # how many articles to generate per pipeline run
CLAUDE_MODEL=claude-sonnet-4-6  # Claude model to use
```

---

## n8n Workflow Overview

The n8n workflow (`n8n/workflow.json`) orchestrates the pipeline on a daily schedule. It can be imported directly into any self-hosted n8n instance.

**Workflow Nodes:**

```
[Cron: 6am UTC]
       ↓
[Execute: trend_spotter.py]
       ↓
[IF: topics found?] → No → [Send notification: no trends today]
       ↓ Yes
[Execute: keyword_validator.py]
       ↓
[IF: keywords validated?] → No → [Log: no valid keywords]
       ↓ Yes
[Split in Batches: one article per topic]
       ↓
[Execute: content_writer.py --topic "..."]
       ↓
[Execute: image_generator.py --topic "..."]
       ↓
[Execute: publisher.py --article-id "..."]
       ↓
[Send Summary Email/Slack: N articles published today]
```

---

## Build Order (Recommended for Claude Code)

Build in this sequence to validate each layer before building on top of it:

1. `db/schema.sql` + `db/database.py` — foundation, everything else writes to this
2. `config/settings.py` + `.env` — get all credentials wired up
3. `agents/trend_spotter.py` — start with RSS feeds (simplest), then add Reddit/Google Trends
4. `agents/keyword_validator.py` — validate with DataForSEO API
5. `prompts/article_writer.txt` — craft and test the Claude system prompt independently
6. `agents/content_writer.py` — integrate Claude API with the prompt
7. `agents/image_generator.py` — Unsplash first, Ideogram as fallback
8. `agents/publisher.py` — WordPress REST API integration
9. `pipeline/run_pipeline.py` — wire all agents together end-to-end
10. `n8n/workflow.json` — set up the scheduler in n8n last, once the pipeline runs cleanly from CLI

---

## Key Design Principles

**Quality over quantity in prompting.** The Claude system prompt (`prompts/article_writer.txt`) is the most important file in this project. Articles must synthesize and rewrite source material — never summarize it verbatim. Google penalizes thin AI content. Invest time iterating on this prompt.

**Start with AUTO_PUBLISH=false.** Review the first 20–30 articles manually before enabling auto-publish. This lets you catch prompt issues, formatting problems, or factual errors early.

**Deduplication is critical.** The DB tracks every topic ever processed. Always check before generating — republishing the same destination guide multiple times kills SEO authority.

**Trend freshness window.** Trending topics have a short shelf life. The pipeline should run once daily, ideally early morning UTC, so articles are live before peak search hours in the US.
