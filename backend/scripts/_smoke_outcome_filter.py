"""Verify the outcome filter on every report endpoint produces correct
row counts after the 2026-06-07 refactor to RAW outcome values:
  completed → real conversation (only one with transcripts)
  no_answer → never picked up
  busy      → line busy
  no_speech → picked up but never spoke
"""
import asyncio
import sqlite3
from pathlib import Path
from sqlalchemy import select

from app.api.reports import (
    calls_csv, leads_csv, summary_pdf, leads_transcripts_pdf,
    OUTCOME_BUCKETS, OUTCOMES_WITH_TRANSCRIPT,
)
from app.db import models
from app.db.session import SessionLocal


async def _drain(resp) -> bytes:
    if hasattr(resp, "body_iterator"):
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        return b"".join(out)
    return resp.body


def _csv_data_row_count(blob: bytes) -> int:
    """Count rows in CSV minus 3 branding rows + the column-header row."""
    lines = blob.decode("utf-8", errors="replace").splitlines()
    return max(0, len(lines) - 4)


async def main() -> None:
    # Ground truth from the DB.
    c = sqlite3.connect("data/gunivox.db")
    print("=== Ground truth (Call.outcome distribution) ===")
    truth = {row[0]: row[1] for row in c.execute(
        "SELECT outcome, COUNT(*) FROM calls WHERE outcome IS NOT NULL GROUP BY outcome"
    )}
    for k, v in sorted(truth.items()):
        marker = "  (has transcript)" if k in OUTCOMES_WITH_TRANSCRIPT else ""
        print(f"  {k:14} {v}{marker}")

    out_dir = Path("scripts/_sample_outcome_reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    async with SessionLocal() as db:
        admin = (await db.execute(select(models.User).where(models.User.role == "admin"))).scalar_one()

        print()
        print("=== calls.csv row counts vs DB ===")
        for bucket in (*OUTCOME_BUCKETS, "all"):
            resp = await calls_csv(date=None, temperature="all", outcome=bucket, _=admin, db=db)
            body = await _drain(resp)
            n = _csv_data_row_count(body)
            expected = (
                sum(truth.values()) if bucket == "all" else truth.get(bucket, 0)
            )
            ok = "✓" if n == expected else "✗"
            print(f"  {ok} outcome={bucket:12} rows={n:5}  expected={expected}")
            (out_dir / f"calls.{bucket}.csv").write_bytes(body)

        print()
        print("=== leads_transcripts.pdf bytes per bucket ===")
        for bucket in (*OUTCOME_BUCKETS, "all"):
            resp = await leads_transcripts_pdf(
                date=None, temperature="all", outcome=bucket, _=admin, db=db,
            )
            body = await _drain(resp)
            tag = "(with transcripts)" if bucket in OUTCOMES_WITH_TRANSCRIPT or bucket == "all" else "(no-transcript notes)"
            print(f"  outcome={bucket:12} bytes={len(body):>9,}  {tag}")
            (out_dir / f"transcripts.{bucket}.pdf").write_bytes(body)


asyncio.run(main())
