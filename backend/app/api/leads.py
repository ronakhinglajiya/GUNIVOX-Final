"""Leads REST: CRUD + CSV bulk upload + webhook ingestion."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_admin, require_counsellor_or_admin
from app.db import models
from app.db import repo
from app.db.session import get_db
from app.schemas.lead import CsvUploadResult, LeadIn, LeadList, LeadOut, LeadUpdate

log = logging.getLogger("gunivox.api.leads")

# Indian mobile phone — 10 digits starting 6-9, optionally with country
# code 91/+91 / leading 0. We look for this anywhere in a row's cells so
# a CSV uploaded with no header (or with unusual column names) still
# gets phone numbers picked up automatically. Demo-friendly fallback
# for the most common case: someone pastes a list of numbers.
_PHONE_IN_CELL_RE = re.compile(r"(?<!\d)(?:\+?91[-.\s]?|0)?([6-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w+")


def _find_phone_in_row(values: list[str]) -> str | None:
    """Scan every cell for an Indian-mobile-shaped 10-digit number.
    Strips +91 / 91 / leading 0 to normalise. Returns the 10-digit form
    with no country code prefix, or None if nothing matches."""
    for v in values:
        if not v:
            continue
        # Remove visual separators (dashes, parens, dots, spaces) so
        # numbers like "+91 79902-67016" or "(91) 7990 26 7016" parse.
        compact = re.sub(r"[\s\-().]+", "", str(v))
        m = _PHONE_IN_CELL_RE.search(compact)
        if m:
            return m.group(1)
    return None


def _find_email_in_row(values: list[str]) -> str | None:
    for v in values:
        if not v:
            continue
        m = _EMAIL_RE.search(str(v))
        if m:
            return m.group(0)
    return None


def _looks_like_header_row(row: list[str]) -> bool:
    """True if the row looks like a CSV header (field names, not data).
    Heuristic: most cells are short, non-numeric, and contain header-
    typical words. If even one cell is clearly a phone number, it's data
    not a header."""
    if not row:
        return False
    if _find_phone_in_row(row):
        return False  # row has data, not a header
    header_words = {
        "phone", "mobile", "contact", "number", "no", "no.",
        "name", "email", "e-mail", "id",
        "program", "course", "interest", "field",
        "language", "lang",
        "source", "notes", "remarks",
    }
    cells_lower = [(c or "").strip().lower() for c in row]
    matches = sum(1 for c in cells_lower if any(w in c for w in header_words))
    return matches >= 1

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("", response_model=LeadList)
async def list_endpoint(
    q: str | None = None,
    status: str | None = None,
    # 2026-06-03: raised 200 → 1000 to match the Pipeline page's fetch.
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LeadList:
    rows, total = await repo.list_leads(db, q=q, status=status, limit=limit, offset=offset)
    return LeadList(total=total, items=[LeadOut.model_validate(r) for r in rows])


@router.post("", response_model=LeadOut, status_code=201)
async def create_endpoint(
    body: LeadIn,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    lead, created = await repo.upsert_lead(db, **body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.get("/{lead_id}", response_model=LeadOut)
async def get_endpoint(
    lead_id: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    lead = await repo.get_lead(db, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return LeadOut.model_validate(lead)


@router.put("/{lead_id}", response_model=LeadOut)
async def update_endpoint(
    lead_id: int,
    body: LeadUpdate,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    lead = await repo.update_lead(db, lead_id, **body.model_dump(exclude_none=True))
    if not lead:
        raise HTTPException(404, "Lead not found")
    await db.commit()
    await db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.delete("/{lead_id}", status_code=204)
async def delete_endpoint(
    lead_id: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await repo.delete_lead(db, lead_id)
    if not ok:
        raise HTTPException(404, "Lead not found")
    await db.commit()


class _NoteIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/{lead_id}/notes", response_model=LeadOut)
async def append_note_endpoint(
    lead_id: int,
    body: _NoteIn,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    """Append a free-form note to a lead from the Lead Detail page.

    Mirrors the same stanza format that inbox.unlock writes so the
    timeline-parser on the frontend sees both kinds of notes the same way:
        [YYYY-MM-DD HH:MM UTC] <user-name>:
        <text>

    Also emits a LeadEvent (notes_updated) so the event timeline reflects
    the action with an actor name + timestamp. Idempotent only in the
    trivial sense — every call appends another stanza.
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Note text cannot be empty.")

    lead = await db.get(models.Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")

    now_utc = datetime.utcnow()
    stamp = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    existing = (lead.notes or "").rstrip()
    header = f"\n\n[{stamp}] {user.name}:"
    lead.notes = f"{existing}{header}\n{text}".lstrip()

    db.add(models.LeadEvent(
        lead_id=lead_id,
        actor_kind=(
            models.LeadEventActorKind.COUNSELLOR.value
            if user.role == "counsellor"
            else models.LeadEventActorKind.ADMIN.value
        ),
        actor_user_id=user.id,
        event_type=models.LeadEventType.NOTES_UPDATED.value,
        payload={"length": len(text), "source": "lead_detail"},
        created_at=now_utc,
    ))

    await db.commit()
    await db.refresh(lead)
    return LeadOut.model_validate(lead)


async def parse_and_upsert_leads_csv(
    db: AsyncSession, content: bytes,
    *,
    phone_from_first_column: bool = False,
) -> tuple[list[int], int, int, int, list[str]]:
    """Parse a CSV byte-blob and upsert every row that has a detectable
    10-digit Indian mobile phone. Returns (lead_ids, inserted, updated,
    skipped, errors). Used by both /api/leads/upload and
    /api/campaigns/upload-and-start so they share identical CSV parsing.

    `phone_from_first_column` (default False) — historical name; now means
    "use the strict positional CSV-dialer schema":
      • False — flexible: a named phone column wins; otherwise we scan
        EVERY cell of the row for a phone-shaped value. Used for the
        regular leads import where users paste assorted data shapes.
      • True  — strict client-pinned schema (2026-06-03 update):
            col L (idx 11) → phone
            col E (idx 4)  → first name → Lead.name
            col G (idx 6)  → last name  → Lead.name
        Used by the CSV-dialer flow (Campaigns ▸ Upload CSV & start
        dialing). Header detection still applies, so a header row with
        labels in col L is silently skipped.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("utf-8-sig", errors="replace")

    # Peek at the first row to decide header vs headerless layout.
    raw_rows = list(csv.reader(io.StringIO(text)))
    if not raw_rows:
        return [], 0, 0, 0, ["empty file"]

    has_header = _looks_like_header_row(raw_rows[0])
    if has_header:
        header = [(c or "").strip().lower() for c in raw_rows[0]]
        data_rows = raw_rows[1:]
        start_row_num = 2
    else:
        header = []
        data_rows = raw_rows
        start_row_num = 1

    def named(row: list[str], key_candidates: tuple[str, ...]) -> str | None:
        """Return the cell whose header matches any of key_candidates."""
        if not header:
            return None
        for key in key_candidates:
            if key in header:
                idx = header.index(key)
                if idx < len(row):
                    val = (row[idx] or "").strip()
                    if val:
                        return val
        return None

    inserted = updated = skipped = 0
    errors: list[str] = []
    lead_ids: list[int] = []

    for offset, row in enumerate(data_rows):
        i = start_row_num + offset
        if not any((c or "").strip() for c in row):
            continue  # skip blank rows silently

        # 1. Find phone.
        if phone_from_first_column:
            # Strict CSV-dialer schema (client-pinned 2026-06-03):
            #   col L (idx 11) → phone     (NOT col A — that's a different field
            #                              in the client's source CSV format)
            #   col E (idx 4)  → first name
            #   col G (idx 6)  → last name
            # The flag name `phone_from_first_column` is now historical — kept
            # for back-compat at the call site; semantics are "strict positional
            # schema" rather than literally column-0.
            cell = (row[11] or "").strip() if len(row) > 11 else ""
            phone_clean = _find_phone_in_row([cell]) or ""
            if not phone_clean:
                # Fallback (2026-07-04): the phone wasn't at column L — the
                # sheet may have only the phone column, shorter rows, or the
                # number shifted. Scan EVERY cell so a valid 10-digit phone
                # anywhere in the row is still accepted instead of rejected.
                phone_clean = _find_phone_in_row(row) or ""
            if not phone_clean:
                skipped += 1
                preview = cell[:30] if cell else "(empty)"
                errors.append(
                    f"Row {i}: no valid 10-digit phone in column L or any cell "
                    f"(col L got '{preview}')"
                )
                continue
        else:
            # Flexible: named column first, then scan ANY cell.
            phone = named(row, ("phone", "mobile", "contact", "number", "no", "no."))
            if phone:
                # Even with a named column, normalise via the same regex so
                # "+91 79902-67016" → "7990267016".
                phone_clean = _find_phone_in_row([phone]) or ""
            else:
                phone_clean = ""
            if not phone_clean:
                phone_clean = _find_phone_in_row(row) or ""

            if not phone_clean:
                skipped += 1
                errors.append(f"Row {i}: no 10-digit phone found in any cell")
                continue

        # 2. Auto-fill the rest.
        if phone_from_first_column:
            # Strict CSV-dialer mode (2026-06-03): the campaign CSV follows a
            # fixed positional schema set by the client —
            #   col A (idx 0)  → phone
            #   col E (idx 4)  → first name
            #   col G (idx 6)  → last name
            # We assemble "FirstName LastName" into `Lead.name` so the UI and
            # reports can display the real caller name BEFORE the call
            # connects (otherwise `Call.caller_name` stays NULL until the
            # agent transcribes a self-introduction mid-call). Other fields
            # are left blank in this mode — strict by design.
            first_name = (row[4] or "").strip() if len(row) > 4 else ""
            last_name = (row[6] or "").strip() if len(row) > 6 else ""
            full = f"{first_name} {last_name}".strip()
            name = full or None
            email = _find_email_in_row(row)  # opportunistic, anywhere in the row
            program = None
            lang = None
            notes = None
            source = "csv"
        else:
            # Flexible mode (leads upload): use header-based lookup.
            name = named(row, ("name", "full name", "fullname", "first name"))
            email = named(row, ("email", "e-mail", "mail")) or _find_email_in_row(row)
            program = named(row, ("program_interest", "program", "course", "interest", "field"))
            lang = named(row, ("preferred_language", "language", "lang"))
            notes = named(row, ("notes", "remarks", "comment"))
            source = named(row, ("source",)) or "csv"

        # Store phones with +91 prefix so the dialer can use them
        # directly without further parsing.
        phone_with_cc = f"+91{phone_clean}"

        try:
            lead, created = await repo.upsert_lead(
                db,
                phone=phone_with_cc,
                name=name,
                email=email,
                program_interest=program,
                preferred_language=lang,
                source=source,
                notes=notes,
            )
            if lead is not None:
                lead_ids.append(lead.id)
            if created:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            skipped += 1
            errors.append(f"Row {i}: {type(e).__name__}: {e}")
    await db.commit()
    log.info(
        "csv upload: %d inserted, %d updated, %d skipped (header=%s)",
        inserted, updated, skipped, has_header,
    )
    return lead_ids, inserted, updated, skipped, errors


@router.post("/upload", response_model=CsvUploadResult)
async def upload_csv_endpoint(
    file: UploadFile = File(...),
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CsvUploadResult:
    """Thin wrapper around `parse_and_upsert_leads_csv` that exposes
    the counts via the public CSV upload endpoint."""
    content = await file.read()
    _ids, inserted, updated, skipped, errors = await parse_and_upsert_leads_csv(db, content)
    return CsvUploadResult(
        inserted=inserted, updated=updated, skipped=skipped, errors=errors[:50],
    )


# ─── Backfill existing leads' names from a CSV (admin-only) ────────────


@router.post("/backfill_csv_names")
async def backfill_csv_names(
    file: UploadFile = File(...),
    # ADMIN-ONLY (2026-06-03 adversarial review fix): this endpoint can
    # rewrite caller names on every Lead and every Call in the system.
    # Counsellors must not be able to mass-rewrite the DB via this path,
    # even if they discover the URL — Reports is admin-only in the sidebar
    # but the page itself didn't enforce role until now.
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """One-shot: take a CSV with phone in col L, first name in col E,
    last name in col G — find each matching Lead (by +91-prefixed phone) and
    set Lead.name + Call.caller_name (on every Call this lead has). No leads
    are created and no dialing is triggered. Use this to retro-fix names on
    yesterday's already-dialed campaigns so today's report shows real names.

    Returns counts so the UI can show what changed.
    """
    from sqlalchemy import update as _update

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("utf-8-sig", errors="replace")

    raw_rows = list(csv.reader(io.StringIO(text)))
    if not raw_rows:
        raise HTTPException(400, "empty file")
    data_rows = raw_rows[1:] if _looks_like_header_row(raw_rows[0]) else raw_rows
    start_row_num = 2 if _looks_like_header_row(raw_rows[0]) else 1

    leads_updated = 0
    calls_updated = 0
    skipped: list[str] = []
    lead_phones_seen: set[str] = set()

    for offset, row in enumerate(data_rows):
        i = start_row_num + offset
        if not any((c or "").strip() for c in row):
            continue

        # Phone lives in col L (idx 11) per the client's CSV schema.
        cell = (row[11] or "").strip() if len(row) > 11 else ""
        phone = _find_phone_in_row([cell]) or ""
        if not phone:
            preview = cell[:30] if cell else "(empty)"
            skipped.append(f"Row {i}: col L is not a valid phone (got '{preview}')")
            continue

        first_name = (row[4] or "").strip() if len(row) > 4 else ""
        last_name = (row[6] or "").strip() if len(row) > 6 else ""
        full = f"{first_name} {last_name}".strip()
        if not full:
            skipped.append(f"Row {i}: no name in cols E + G")
            continue

        phone_with_cc = f"+91{phone}"
        if phone_with_cc in lead_phones_seen:
            continue  # duplicate row for same phone in the CSV — skip silently
        lead_phones_seen.add(phone_with_cc)

        lead = await repo.get_lead_by_phone(db, phone_with_cc)
        if lead is None:
            skipped.append(f"Row {i}: no matching lead for {phone_with_cc}")
            continue

        # Update the Lead row
        lead.name = full
        leads_updated += 1

        # Update every Call row this lead has — overwriting any transcribed
        # garbage from yesterday so reports/UI show the right caller name.
        # This is a deliberate "CSV is now authoritative" sweep, not a
        # COALESCE — the user is explicitly retro-fixing.
        result = await db.execute(
            _update(models.Call)
            .where(models.Call.lead_id == lead.id)
            .values(caller_name=full)
        )
        calls_updated += int(result.rowcount or 0)

    await db.commit()
    log.info(
        "backfill_csv_names: leads_updated=%d calls_updated=%d skipped=%d",
        leads_updated, calls_updated, len(skipped),
    )
    return {
        "leads_updated": leads_updated,
        "calls_updated": calls_updated,
        "total_skipped": len(skipped),
        "skipped_sample": skipped[:10],
    }


webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@webhook_router.post("/lead", response_model=LeadOut)
async def webhook_lead(body: LeadIn, db: AsyncSession = Depends(get_db)) -> LeadOut:
    """Public webhook: external systems (web form, CRM) push a lead here."""
    fields = body.model_dump(exclude_none=True)
    fields.setdefault("source", "webhook")
    lead, _ = await repo.upsert_lead(db, **fields)
    await db.commit()
    await db.refresh(lead)
    return LeadOut.model_validate(lead)
