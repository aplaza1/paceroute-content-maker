# Travel Blog Pipeline

An automated system that discovers trending travel topics, validates them against search demand, generates SEO-optimized articles using Claude AI, and publishes them to WordPress — running daily with minimal human intervention.

## How It Works

```
Trend Spotter → Keyword Validator → Content Writer → Image Generator → Publisher
```

1. **Trend Spotter** monitors Reddit, Google Trends, and travel RSS feeds for hot topics
2. **Keyword Validator** filters topics using DataForSEO to ensure real search demand
3. **Content Writer** researches each topic and generates a full article via Claude
4. **Image Generator** sources a featured image from Unsplash (or generates one via Ideogram)
5. **Publisher** pushes everything to WordPress as a draft (or live post)

---

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in all API keys
```

**Required API keys:**
- `ANTHROPIC_API_KEY` — [console.anthropic.com](https://console.anthropic.com)
- `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` — [dataforseo.com](https://dataforseo.com)
- `APIFY_API_TOKEN` — [apify.com](https://apify.com)
- `UNSPLASH_ACCESS_KEY` — [unsplash.com/developers](https://unsplash.com/developers)
- WordPress credentials — generate an Application Password at: `yoursite.com/wp-admin → Users → Profile → Application Passwords`

### 3. Initialize the database

```bash
python -c "from db import database; database.init_db()"
```

### 4. Add a default fallback image

```bash
# Add any travel landscape image as:
output/images/default_travel.jpg
```

---

## Running the Pipeline

### Full run (discovers trends → writes → publishes)
```bash
python pipeline/run_pipeline.py
```

### Dry run (trend + keyword validation only, no writing)
```bash
python pipeline/run_pipeline.py --dry-run
```

### Run individual agents
```bash
python agents/trend_spotter.py
python agents/keyword_validator.py
python agents/content_writer.py
python agents/image_generator.py
python agents/publisher.py
```

---

## Project Structure

```
travel-blog-pipeline/
├── agents/
│   ├── trend_spotter.py       # Agent 1: discover trending topics
│   ├── keyword_validator.py   # Agent 2: validate with search volume data
│   ├── content_writer.py      # Agent 3: generate articles via Claude
│   ├── image_generator.py     # Agent 4: source featured images
│   └── publisher.py           # Agent 5: publish to WordPress
├── pipeline/
│   └── run_pipeline.py        # Main CLI entrypoint
├── db/
│   ├── database.py            # SQLite interface
│   └── schema.sql             # Database schema
├── prompts/
│   └── article_writer.txt     # Claude system prompt for article generation
├── config/
│   └── settings.py            # Config loaded from .env
├── output/
│   ├── articles/              # Generated HTML files
│   └── images/                # Downloaded/generated images
├── n8n/
│   └── workflow.json          # n8n workflow (import to schedule daily runs)
├── .env.example
├── requirements.txt
└── README.md
```

---

## Configuration

Key settings in `.env`:

| Variable | Default | Description |
|---|---|---|
| `AUTO_PUBLISH` | `false` | Set to `true` to publish immediately instead of saving as draft |
| `MIN_SEARCH_VOLUME` | `500` | Minimum monthly searches to accept a keyword |
| `MAX_KEYWORD_DIFFICULTY` | `60` | Maximum keyword difficulty score (0–100) |
| `ARTICLES_PER_DAY` | `5` | Max articles per pipeline run |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model to use |

---

## Scheduling with n8n

1. Install n8n: `npm install -g n8n`
2. Start it: `n8n start`
3. Open `http://localhost:5678`
4. Import `n8n/workflow.json`
5. Set the cron to run daily at 6am UTC
6. Configure the Execute Command nodes to point to your project path

---

## Important Notes

**Start with `AUTO_PUBLISH=false`.** Review the first 20–30 articles manually in WordPress before enabling auto-publish. Check for factual accuracy, tone, and formatting.

**The article prompt is your most important asset.** The system prompt in `prompts/article_writer.txt` controls article quality. Iterate on it as you review early output.

**Keyword research comes first.** The pipeline only writes articles where real search demand exists. Don't bypass the keyword validator step.
# paceroute-content-maker
