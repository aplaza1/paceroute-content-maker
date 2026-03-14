-- Travel Blog Pipeline Database Schema
-- SQLite compatible

-- Tracks all discovered trending topics
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  source TEXT,                          -- comma-separated: "reddit,tiktok,google_trends"
  trend_score INTEGER DEFAULT 0,
  raw_signal TEXT,                      -- human-readable description of why it's trending
  suggested_angle TEXT,                 -- e.g. "budget travel guide", "safety update"
  discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status TEXT DEFAULT 'pending'         -- pending | validated | rejected | published | failed
);

-- Tracks validated keywords per topic
CREATE TABLE IF NOT EXISTS keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  target_keyword TEXT NOT NULL,
  monthly_volume INTEGER,
  keyword_difficulty INTEGER,
  related_keywords TEXT                 -- JSON array of strings
);

-- Tracks generated and published articles
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  title TEXT,
  meta_description TEXT,
  word_count INTEGER,
  html_path TEXT,                       -- local path to saved HTML file
  image_path TEXT,                      -- local path to featured image
  image_alt_text TEXT,
  image_attribution TEXT,
  wordpress_post_id INTEGER,
  wordpress_url TEXT,
  status TEXT DEFAULT 'generated',      -- generated | published | failed
  error_message TEXT,                   -- populated if status = failed
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  published_at TIMESTAMP
);

-- Per-API-call cost entries linked to a pipeline run
CREATE TABLE IF NOT EXISTS cost_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    agent TEXT NOT NULL,
    description TEXT NOT NULL,
    usd_cost REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Pipeline run log — one row per daily pipeline execution
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP,
  topics_discovered INTEGER DEFAULT 0,
  topics_validated INTEGER DEFAULT 0,
  articles_generated INTEGER DEFAULT 0,
  articles_published INTEGER DEFAULT 0,
  status TEXT DEFAULT 'running',        -- running | completed | failed
  error_message TEXT
);
