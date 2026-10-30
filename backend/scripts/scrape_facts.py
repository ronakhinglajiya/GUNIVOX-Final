"""Scrape Ganpat University website for non-program facts.

The admission_brain.json only has per-program data (fees / counsellor /
eligibility). Callers ask other things — location, scholarship policy,
transport, placement stats — that the agent currently has to refuse. This
script pulls those facts from ganpatuniversity.ac.in and writes them to
backend/data/university_facts.json, which the bridge then injects into the
LLM context when the caller's question mentions one of the topics.

Run once after a fresh checkout, or after the GU website changes:
    ../venv/Scripts/python.exe -m scripts.scrape_facts

Output format:
{
  "location": {"summary": "...", "address": "...", "city": "...", "url": "..."},
  "scholarship": {...},
  "transport": {...},
  "placement": {...},
  ...
}
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pages to scrape. Each entry: (topic_key, url, css-ish hint for extraction).
PAGES = [
    ("location", "https://www.ganpatuniversity.ac.in/contact-us/"),
    ("location_about", "https://www.ganpatuniversity.ac.in/about-us/"),
    ("scholarship", "https://www.ganpatuniversity.ac.in/scholarships/"),
    ("transport", "https://www.ganpatuniversity.ac.in/transportation/"),
    ("placement", "https://www.ganpatuniversity.ac.in/placement/"),
    ("placement_iet", "https://www.ganpatuniversity.ac.in/iet/placement/"),
]

# Light "fact extractor": strip HTML tags, collapse whitespace, take first N chars.
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip(html: str, max_chars: int = 1500) -> str:
    txt = _TAG.sub(" ", html)
    txt = _WS.sub(" ", txt).strip()
    return txt[:max_chars]


async def fetch_one(client: httpx.AsyncClient, key: str, url: str) -> dict:
    try:
        resp = await client.get(url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return {"key": key, "url": url, "error": str(e), "summary": ""}
    summary = _strip(resp.text)
    return {"key": key, "url": url, "summary": summary}


async def main() -> None:
    out_path = Path(__file__).resolve().parent.parent / "data" / "university_facts.json"

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; Gunivox/1.0)"},
    ) as client:
        results = await asyncio.gather(*(fetch_one(client, k, u) for k, u in PAGES))

    # Collapse multiple pages per topic into one nested dict.
    out: dict[str, dict] = {}
    for r in results:
        topic = r["key"].split("_")[0]
        out.setdefault(topic, {"sources": []})
        if r.get("summary"):
            out[topic]["sources"].append({"url": r["url"], "summary": r["summary"]})

    # Always-true facts known from project memory + the QA report.
    out.setdefault("location", {"sources": []})
    out["location"]["canonical"] = (
        "Ganpat University, Ganpat Vidyanagar, Kherva (near Mehsana), "
        "Gujarat 384012, India. Main campus is at Kherva, Mehsana — NOT "
        "Gandhinagar."
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v.get("sources", [])) for v in out.values())
    print(f"Wrote {out_path}  ({len(out)} topics, {total} sources)")


if __name__ == "__main__":
    asyncio.run(main())
