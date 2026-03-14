"""
pipeline/dashboard.py
Cost dashboard — shows spend history and live API balances.

Usage:
    python pipeline/dashboard.py
"""

import os
import sys
import json
import urllib.request
import urllib.error
import base64
from itertools import groupby

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import database


# ---------------------------------------------------------------------------
# Live balance helpers
# ---------------------------------------------------------------------------

def _fetch_dataforseo_balance(login: str, password: str) -> str:
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    req = urllib.request.Request(
        "https://api.dataforseo.com/v3/appendix/user_data",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    balance = data["tasks"][0]["result"][0]["money"]["balance"]
    return f"${balance:,.4f}"


def _fetch_apify_monthly_usage(token: str) -> str:
    url = f"https://api.apify.com/v2/users/me/usage/monthly?token={token}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    d = data.get("data", {})
    usd = d.get("totalUsageCreditsUsdAfterVolumeDiscount",
                 d.get("totalUsageCreditsUsd"))
    if usd is None:
        return "unavailable"
    return f"${usd:,.4f} this month"


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def show_per_run_history(logs: list[dict]):
    _section("Per-Run Cost History")

    if not logs:
        print("  No cost data recorded yet.")
        return

    # Group by run_id (logs already ordered run_id DESC, id ASC)
    for run_id, run_entries in groupby(logs, key=lambda r: r["run_id"]):
        entries = list(run_entries)
        run_started = entries[0].get("run_started_at") or "unknown"
        run_status = entries[0].get("run_status") or "?"
        run_total = sum(e["usd_cost"] for e in entries)

        print(f"\n  Run #{run_id}  |  {run_started}  |  {run_status}")
        print(f"  {'─'*50}")

        # Per-agent subtotals for this run
        agent_totals: dict[str, float] = {}
        for e in entries:
            agent_totals[e["agent"]] = agent_totals.get(e["agent"], 0.0) + e["usd_cost"]

        col_w = max(len(a) for a in agent_totals) + 2
        for agent, subtotal in agent_totals.items():
            print(f"    {agent:<{col_w}} ${subtotal:.5f}")
        print(f"  {'─'*50}")
        print(f"    {'Total':<{col_w}} ${run_total:.5f}")


def show_alltime_totals(logs: list[dict]):
    _section("All-Time Totals by API")

    if not logs:
        print("  No cost data recorded yet.")
        return

    agent_totals: dict[str, float] = {}
    for e in logs:
        agent_totals[e["agent"]] = agent_totals.get(e["agent"], 0.0) + e["usd_cost"]

    col_w = max(len(a) for a in agent_totals) + 2
    grand_total = sum(agent_totals.values())

    print()
    for agent, total in sorted(agent_totals.items(), key=lambda x: -x[1]):
        print(f"  {agent:<{col_w}} ${total:.5f}")
    print(f"  {'─'*42}")
    print(f"  {'Grand Total':<{col_w}} ${grand_total:.5f}")


def show_live_balances():
    _section("Live API Balances")
    print()

    # DataForSEO
    try:
        from config import settings
        balance = _fetch_dataforseo_balance(
            settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD
        )
        print(f"  DataForSEO account balance : {balance}")
    except Exception as exc:
        print(f"  DataForSEO account balance : unavailable ({exc})")

    # Apify
    try:
        from config import settings
        usage = _fetch_apify_monthly_usage(settings.APIFY_API_TOKEN)
        print(f"  Apify monthly usage        : {usage}")
    except Exception as exc:
        print(f"  Apify monthly usage        : unavailable ({exc})")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def show_other_costs():
    _section("Other Costs (check manually)")
    print()
    print("  Hostinger  — WordPress hosting  : check hostinger.com/cpanel")
    print("  Ideogram   — image generation   : check ideogram.ai/manage/billing")
    print()


def main():
    database.init_db()
    logs = database.get_cost_logs()

    show_per_run_history(logs)
    show_alltime_totals(logs)
    show_live_balances()
    show_other_costs()


if __name__ == "__main__":
    main()
