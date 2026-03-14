"""
pipeline/costs.py
Shared cost tracker for all API calls in a pipeline run.

Import `tracker` from any agent and call the appropriate add_*() method.
The pipeline runner resets the tracker at the start of each run and prints
the final summary at the end.

Pricing notes:
  - Claude costs are EXACT — taken from the API response usage object.
  - DataForSEO, Apify, and Ideogram costs are ESTIMATES based on published
    pricing. Update the constants below if rates change.
"""

# ── Pricing constants ────────────────────────────────────────────────────────

# Anthropic claude-haiku-4-5-20251001 (anthropic.com/pricing)
_CLAUDE_INPUT_PER_TOKEN  = 0.80  / 1_000_000   # $0.80  / M input tokens
_CLAUDE_OUTPUT_PER_TOKEN = 4.00  / 1_000_000   # $4.00  / M output tokens

# DataForSEO — Google Ads Keywords, search volume live endpoint
_DATAFORSEO_PER_KEYWORD  = 0.0075              # ~$0.0075 per keyword (live task)

# Apify — Reddit Scraper Lite actor (~$0.30 per 1,000 result items)
_APIFY_PER_ITEM          = 0.0003

# Ideogram V_2 image generation (ideogram.ai/pricing)
_IDEOGRAM_V2_PER_IMAGE   = 0.08


# ── Tracker ─────────────────────────────────────────────────────────────────

class CostTracker:
    def __init__(self):
        self._entries: list[tuple[str, str, float]] = []  # (agent, description, usd)

    def reset(self):
        self._entries.clear()

    # -- low-level -----------------------------------------------------------

    def add(self, agent: str, description: str, usd_cost: float) -> float:
        self._entries.append((agent, description, usd_cost))
        return usd_cost

    # -- convenience adders --------------------------------------------------

    def add_claude(self, agent: str, label: str, usage) -> float:
        """Record cost from an Anthropic API response .usage object (exact)."""
        cost = (usage.input_tokens  * _CLAUDE_INPUT_PER_TOKEN +
                usage.output_tokens * _CLAUDE_OUTPUT_PER_TOKEN)
        desc = (f"{label} "
                f"({usage.input_tokens:,} in + {usage.output_tokens:,} out tokens)")
        return self.add(agent, desc, cost)

    def add_dataforseo(self, agent: str, keyword_count: int) -> float:
        """Record estimated DataForSEO cost for a keyword volume lookup."""
        cost = keyword_count * _DATAFORSEO_PER_KEYWORD
        return self.add(agent, f"DataForSEO search volume ({keyword_count} keywords)", cost)

    def add_apify(self, agent: str, item_count: int) -> float:
        """Record estimated Apify cost based on items returned."""
        cost = item_count * _APIFY_PER_ITEM
        return self.add(agent, f"Apify Reddit scrape ({item_count} items fetched)", cost)

    def add_ideogram(self, agent: str) -> float:
        """Record Ideogram V_2 image generation cost."""
        return self.add(agent, "Ideogram V_2 image generation", _IDEOGRAM_V2_PER_IMAGE)

    # -- reporting -----------------------------------------------------------

    def agent_subtotal(self, agent: str) -> float:
        return sum(c for a, _, c in self._entries if a == agent)

    def total(self) -> float:
        return sum(c for _, _, c in self._entries)

    def _agents_in_order(self) -> list[str]:
        seen: list[str] = []
        for agent, _, _ in self._entries:
            if agent not in seen:
                seen.append(agent)
        return seen

    def print_agent_summary(self, agent: str):
        """Print a per-agent cost block. Call at the end of each agent's run()."""
        entries = [(d, c) for a, d, c in self._entries if a == agent]
        if not entries:
            return
        subtotal = sum(c for _, c in entries)
        tag = agent.lower().replace(" ", "_")
        print(f"[{tag}] Cost estimate:")
        for desc, cost in entries:
            marker = "" if "exact" not in desc else " (exact)"
            print(f"  • {desc}: ${cost:.5f}")
        print(f"  Subtotal: ${subtotal:.5f}")

    def print_run_summary(self):
        """Print the full cost table across all agents. Call at end of pipeline."""
        agents = self._agents_in_order()
        if not agents:
            return
        col_w = max(len(a) for a in agents) + 2
        print(f"\n  Cost Summary")
        print(f"  {'─' * 42}")
        for agent in agents:
            print(f"  {agent:<{col_w}} ${self.agent_subtotal(agent):.5f}")
        print(f"  {'─' * 42}")
        print(f"  {'Total':<{col_w}} ${self.total():.5f}")

    def save_to_db(self, run_id: int):
        """Persist all cost entries for this run to the database."""
        from db import database
        database.insert_cost_logs(run_id, self._entries)


# Module-level singleton — shared across all agents in one pipeline run
tracker = CostTracker()
