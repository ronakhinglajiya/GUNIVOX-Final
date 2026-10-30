"""One-shot: derive each lead's funnel_stage from its existing data.

Rules (highest priority wins):
  1. lead.dnd == True                              → funnel_stage = lost  (and DND remains)
  2. lead.last_disposition mapped in DISPOSITION_MAPPING → use that mapping
  3. lead.status == LeadStatus.NOT_INTERESTED      → lost
  4. lead.status == LeadStatus.INTERESTED          → engaged
  5. lead.status == LeadStatus.CALLBACK_SCHEDULED  → engaged
  6. lead.attempt_count > 0                        → still inquiry (called, no real progress)
  7. default                                       → inquiry
"""
import asyncio

from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        leads = (await db.execute(select(models.Lead))).scalars().all()
        print(f"backfilling funnel_stage for {len(leads)} leads...")
        bucket_counts: dict[str, int] = {}
        for lead in leads:
            stage: str
            if lead.dnd:
                stage = models.FunnelStage.LOST.value
            elif lead.last_disposition and lead.last_disposition in models.DISPOSITION_MAPPING:
                mapped_stage, _, _ = models.DISPOSITION_MAPPING[lead.last_disposition]
                stage = mapped_stage.value if mapped_stage else lead.funnel_stage
            elif lead.status == models.LeadStatus.NOT_INTERESTED.value:
                stage = models.FunnelStage.LOST.value
            elif lead.status in (
                models.LeadStatus.INTERESTED.value,
                models.LeadStatus.CALLBACK_SCHEDULED.value,
            ):
                stage = models.FunnelStage.ENGAGED.value
            else:
                stage = models.FunnelStage.INQUIRY.value

            if lead.funnel_stage != stage:
                lead.funnel_stage = stage
                lead.funnel_stage_set_at = lead.updated_at or lead.created_at
            bucket_counts[stage] = bucket_counts.get(stage, 0) + 1

        await db.commit()
        print("\nfinal funnel distribution:")
        for stage, n in sorted(bucket_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {stage:14} {n}")


asyncio.run(main())
