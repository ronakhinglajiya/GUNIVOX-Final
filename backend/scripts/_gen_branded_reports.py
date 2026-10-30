"""Generate a sample summary.pdf, leads_transcripts.pdf, calls.csv to disk
so we can visually verify the branding header + footer."""
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.api.reports import calls_csv, leads_csv, summary_pdf, leads_transcripts_pdf
from app.db import models
from app.db.session import SessionLocal


OUT = Path("scripts/_sample_reports")


async def _drain(resp) -> bytes:
    """StreamingResponse / Response → bytes."""
    if hasattr(resp, "body_iterator"):
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        return b"".join(chunks)
    return resp.body


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    async with SessionLocal() as db:
        admin = (
            await db.execute(select(models.User).where(models.User.role == "admin"))
        ).scalar_one()

        # 1) summary.pdf
        r = await summary_pdf(date=None, temperature="all", _=admin, db=db)
        (OUT / "summary.pdf").write_bytes(await _drain(r))
        print(f"  ✓ {(OUT / 'summary.pdf')}  ({(OUT / 'summary.pdf').stat().st_size:,} bytes)")

        # 2) leads_transcripts.pdf
        r = await leads_transcripts_pdf(date=None, temperature="all", _=admin, db=db)
        (OUT / "leads_transcripts.pdf").write_bytes(await _drain(r))
        print(f"  ✓ {(OUT / 'leads_transcripts.pdf')}  "
              f"({(OUT / 'leads_transcripts.pdf').stat().st_size:,} bytes)")

        # 3) calls.csv
        r = await calls_csv(date=None, temperature="all", _=admin, db=db)
        (OUT / "calls.csv").write_bytes(await _drain(r))
        print(f"  ✓ {(OUT / 'calls.csv')}  ({(OUT / 'calls.csv').stat().st_size:,} bytes)")

        # 4) leads.csv
        r = await leads_csv(date=None, temperature="all", _=admin, db=db)
        (OUT / "leads.csv").write_bytes(await _drain(r))
        print(f"  ✓ {(OUT / 'leads.csv')}  ({(OUT / 'leads.csv').stat().st_size:,} bytes)")


asyncio.run(main())
