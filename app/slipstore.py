"""Saves each day's slips once per tier, so results can be tracked honestly over time.

Folder: SLIPS_DIR (default ./slips_history). On a host with an ephemeral disk (Render free),
point SLIPS_DIR at a persistent disk, or the history disappears whenever the app restarts.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("oddslab")

# Only what settlement needs. Drops the bulky evidence rows.
KEEP = ("fixture_id", "fixture_name", "home_team", "away_team", "league", "kickoff", "market",
        "selection", "base_key", "probability", "odds", "lift", "confidence")


def _file(day_str: str) -> Path:
    return Path(os.getenv("SLIPS_DIR", "slips_history")) / f"{day_str}.json"


def load(day_str: str):
    try:
        return json.loads(_file(day_str).read_text())
    except (OSError, ValueError):
        return None


def write(day_str: str, doc: dict) -> bool:
    f = _file(day_str)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc))
        os.replace(tmp, f)
        return True
    except OSError as exc:
        log.error("Could not save slips for %s: %s", day_str, exc)
        return False


def save_once(day_str: str, target: float, slips: list) -> bool:
    """Store a tier's slips the first time they are built from complete data. Never overwritten."""
    doc = load(day_str) or {"date": day_str, "tiers": {}}
    key = f"{target:.2f}"
    if key in doc["tiers"]:
        return False
    doc["tiers"][key] = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "slips": [{"combined_odds": s["combined_odds"], "probability": s["probability"],
                   "legs": [{k: leg.get(k) for k in KEEP} for leg in s["legs"]]} for s in slips],
    }
    return write(day_str, doc)