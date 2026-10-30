"""Reports — CSV + PDF downloads for calls (with transcripts) and leads.

Endpoints:
  GET /api/reports/calls.csv     — one row per call + flattened transcript
  GET /api/reports/leads.csv     — one row per lead with all profile fields
  GET /api/reports/summary.pdf   — pretty visual summary report

The CSVs include the billable-time fields (seconds + minutes) computed via
`app.billing` so the operator can see "minutes we're charging for" per call,
matching the Usage page. Each file is named with today's date.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone, timedelta, date as _date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.billing import (
    BILLING_EPOCH,
    RATE_INR_PER_MINUTE,
    amount_inr,
    billable_seconds,
)
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])

IST = ZoneInfo("Asia/Kolkata")
VALID_TEMPS = {"all", "hot", "warm", "cold"}

# 2026-06-07: outcome filter sits on top of the temperature filter.
# Each bucket maps DIRECTLY to a single Call.outcome value as stored by
# the telephony bridge — no synthesized abstractions. Honest filter:
# the user picks exactly the outcome they want to inspect.
#   completed → real conversation (the only bucket with a transcript)
#   no_answer → rang, never picked up
#   busy      → line was busy
#   no_speech → picked up but never spoke
# "all" disables this filter entirely.
OUTCOME_BUCKETS: list[str] = ["completed", "no_answer", "busy", "no_speech"]
VALID_OUTCOMES = {"all", *OUTCOME_BUCKETS}
# An outcome counts as "had a real conversation, transcript exists" only
# when it's `completed`. Every other outcome means the call never had a
# transcript-producing exchange — so the transcripts PDF shows a
# "No transcript — caller did not respond" note in their place.
OUTCOMES_WITH_TRANSCRIPT = {"completed"}


def _outcome_filter(outcome: str):
    """Return a SQLAlchemy WHERE expression for the requested outcome,
    or None to skip filtering."""
    if outcome == "all":
        return None
    if outcome not in OUTCOME_BUCKETS:
        return None
    return models.Call.outcome == outcome

# Gujarati-capable font registration. ReportLab's default Helvetica has no
# glyphs for Devanagari / Gujarati / etc., so unicode renders as black boxes.
# We try Windows-bundled Nirmala UI first (covers Latin + most Indian
# scripts), then Shruti (Gujarati-only TTF) as a fallback, then give up
# silently so the PDF still generates with Helvetica on non-Windows hosts.
import logging as _logging
_log = _logging.getLogger("gunivox.reports")
_FONTS_REGISTERED = False
_PDF_FONT = "Helvetica"
_PDF_FONT_BOLD = "Helvetica-Bold"


def _ensure_pdf_fonts() -> tuple[str, str]:
    """Register a Gujarati-capable font (idempotent). Returns (regular, bold)
    font names to plug into ParagraphStyle. Falls back to Helvetica with a
    one-time warning if no Indian-script font is available."""
    global _FONTS_REGISTERED, _PDF_FONT, _PDF_FONT_BOLD
    if _FONTS_REGISTERED:
        return _PDF_FONT, _PDF_FONT_BOLD
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # (display name reg, display name bold, path reg, path bold, ttc idx reg, ttc idx bold)
    candidates: list[tuple[str, str, str, str, int | None, int | None]] = [
        # Nirmala UI — Windows 10/11 default, multi-script (best choice)
        ("Nirmala", "Nirmala-Bold",
         "C:/Windows/Fonts/Nirmala.ttc", "C:/Windows/Fonts/Nirmala.ttc", 0, 1),
        # Shruti — Gujarati-only fallback (also Windows-bundled)
        ("Shruti", "Shruti-Bold",
         "C:/Windows/Fonts/shruti.ttf", "C:/Windows/Fonts/shrutib.ttf", None, None),
    ]
    for reg, bold, p_reg, p_bold, idx_reg, idx_bold in candidates:
        try:
            kw_r = {"subfontIndex": idx_reg} if idx_reg is not None else {}
            kw_b = {"subfontIndex": idx_bold} if idx_bold is not None else {}
            pdfmetrics.registerFont(TTFont(reg, p_reg, **kw_r))
            pdfmetrics.registerFont(TTFont(bold, p_bold, **kw_b))
            # Register the family so `<b>...</b>` inside a Paragraph
            # actually uses the bold face (otherwise reportlab falls back
            # to its built-in Helvetica-Bold which lacks Gujarati glyphs).
            pdfmetrics.registerFontFamily(
                reg, normal=reg, bold=bold, italic=reg, boldItalic=bold,
            )
            _PDF_FONT, _PDF_FONT_BOLD = reg, bold
            _log.info("reports: PDF font %s registered for Gujarati", reg)
            break
        except Exception as e:
            _log.warning("reports: %s font unavailable: %s", reg, e)
    else:
        _log.warning(
            "reports: no Gujarati-capable font found — PDFs will use Helvetica "
            "and Gujarati text will render as black boxes. Install Nirmala UI "
            "or bundle Noto Sans Gujarati."
        )
    _FONTS_REGISTERED = True
    return _PDF_FONT, _PDF_FONT_BOLD


# ─────────────────── Branding (TomatoVation + Gunivox + Ganpat University) ──
# Co-branded header on every PDF page + every CSV file. Single source of
# truth — change here, every report updates.
BRANDING_LOGO_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "branding" / "header.png"
BRANDING_COMPANY = "TomatoVation"
BRANDING_PROJECT = "Gunivox"
BRANDING_CLIENT = "Ganpat University"
BRANDING_EMAIL = "info@tomatovation.com"
BRANDING_PHONE = "+1 (313) 688-3753"
BRANDING_WEBSITE = "tomatovation.com"

# Reserved vertical space (mm) for the header strip + footer strip. The
# PDF endpoints bump their topMargin/bottomMargin to leave room for this.
BRANDING_HEADER_HEIGHT_MM = 28
BRANDING_FOOTER_HEIGHT_MM = 18


def _draw_branding(canv, doc) -> None:
    """ReportLab canvas callback — draws the branding header + footer on
    every page. Wired via doc.build(..., onFirstPage=, onLaterPages=).

    Header: co-branded logo (TomatoVation + JATAS AI) on the left,
            "Company / Project / Client" stack on the right.
    Footer: contact email + phone (centered) + website (right).
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm

    page_w, page_h = A4

    # ── Header ─────────────────────────────────────────────────────────
    header_top = page_h - 6 * mm           # 6 mm padding from page top
    header_bottom = page_h - BRANDING_HEADER_HEIGHT_MM * mm

    # Logo on the left.
    logo_h = 16 * mm
    logo_w = 44 * mm                       # the source image is roughly 2.4:1
    logo_x = 18 * mm                       # match the doc leftMargin
    logo_y = header_top - logo_h
    if BRANDING_LOGO_PATH.is_file():
        try:
            canv.drawImage(
                str(BRANDING_LOGO_PATH),
                logo_x, logo_y,
                width=logo_w, height=logo_h,
                preserveAspectRatio=True, mask="auto",
            )
        except Exception:
            # If the image is malformed, fall back to the text header only.
            _log.exception("reports: failed to draw branding logo")

    # Right-side text stack — Company / Project / Client.
    text_right_x = page_w - 18 * mm        # match doc rightMargin
    canv.setFillColor(colors.HexColor("#222222"))
    canv.setFont("Helvetica-Bold", 9)
    canv.drawRightString(text_right_x, header_top - 4 * mm,
                         f"Company: {BRANDING_COMPANY}")
    canv.setFont("Helvetica", 9)
    canv.drawRightString(text_right_x, header_top - 9 * mm,
                         f"Project: {BRANDING_PROJECT}")
    canv.drawRightString(text_right_x, header_top - 14 * mm,
                         f"Client: {BRANDING_CLIENT}")

    # Hairline separator under the header.
    canv.setStrokeColor(colors.HexColor("#c4302b"))
    canv.setLineWidth(0.6)
    canv.line(18 * mm, header_bottom + 2 * mm,
              page_w - 18 * mm, header_bottom + 2 * mm)

    # ── Footer ─────────────────────────────────────────────────────────
    footer_top = BRANDING_FOOTER_HEIGHT_MM * mm
    canv.setStrokeColor(colors.HexColor("#cccccc"))
    canv.setLineWidth(0.4)
    canv.line(18 * mm, footer_top, page_w - 18 * mm, footer_top)

    canv.setFillColor(colors.HexColor("#444444"))
    canv.setFont("Helvetica", 8)
    # Left: contact email + phone.
    canv.drawString(
        18 * mm, footer_top - 6 * mm,
        f"Contact: {BRANDING_EMAIL}  ·  {BRANDING_PHONE}",
    )
    # Right: website + page number.
    canv.drawRightString(
        page_w - 18 * mm, footer_top - 6 * mm,
        f"{BRANDING_WEBSITE}  ·  Page {canv.getPageNumber()}",
    )


def _csv_branding_rows() -> list[list[str]]:
    """Brief branded header for CSV exports. Two metadata rows then a blank
    spacer before the column headers."""
    return [
        [f"Company: {BRANDING_COMPANY}", f"Project: {BRANDING_PROJECT}",
         f"Client: {BRANDING_CLIENT}"],
        [f"Contact: {BRANDING_EMAIL}", BRANDING_PHONE, BRANDING_WEBSITE],
        [],  # blank spacer
    ]


def _today_iso() -> str:
    """Return YYYY-MM-DD (in IST) for filenames + report header."""
    return datetime.now(IST).strftime("%Y-%m-%d")


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # IST for the operator's eyes
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


def _parse_date_to_utc_window(date_str: str | None) -> tuple[datetime | None, datetime | None]:
    """Convert a YYYY-MM-DD date (IST) to the UTC window [start, end).

    Reports are filtered on `created_at` (UTC in the DB). The operator picks a
    civil date in IST, e.g. '2026-06-03', which becomes:
        start = 2026-06-03 00:00 IST = 2026-06-02 18:30 UTC
        end   = 2026-06-04 00:00 IST = 2026-06-03 18:30 UTC
    Returns (None, None) if the input is empty/falsy → no date filter.
    """
    if not date_str:
        return None, None
    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, f"Invalid date '{date_str}'; expected YYYY-MM-DD.")
    start_ist = datetime(d.year, d.month, d.day, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _parse_range_to_utc_window(
    date_str: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[datetime | None, datetime | None]:
    """Resolve a UTC [start, end) window from either a single `date` OR a
    `date_from`/`date_to` range (all civil IST dates, YYYY-MM-DD).

    Precedence: if EITHER range bound is supplied, the range wins and the
    single `date` is ignored. A range may be open-ended (only from, or only
    to). With neither range bound, we fall back to the single-date behavior
    so every existing caller keeps working unchanged. (2026-07-23)
    """
    if not date_from and not date_to:
        return _parse_date_to_utc_window(date_str)

    def _parse(d: str) -> _date:
        try:
            return _date.fromisoformat(d)
        except ValueError:
            raise HTTPException(400, f"Invalid date '{d}'; expected YYYY-MM-DD.")

    start = end = None
    if date_from:
        d = _parse(date_from)
        start = datetime(d.year, d.month, d.day, tzinfo=IST).astimezone(timezone.utc)
    if date_to:
        d = _parse(date_to)
        # end is EXCLUSIVE → the day AFTER date_to at IST-midnight, so the
        # whole of date_to is included.
        end_ist = datetime(d.year, d.month, d.day, tzinfo=IST) + timedelta(days=1)
        end = end_ist.astimezone(timezone.utc)
    if start is not None and end is not None and start >= end:
        raise HTTPException(400, f"date_from ({date_from}) must be on/before date_to ({date_to}).")
    return start, end


def _range_label(date_str: str | None, date_from: str | None, date_to: str | None) -> str | None:
    """The date portion of the filename suffix — supports a range."""
    if date_from and date_to:
        return f"{date_from}_to_{date_to}"
    if date_from:
        return f"from_{date_from}"
    if date_to:
        return f"upto_{date_to}"
    return date_str


def _range_human(date_str: str | None, date_from: str | None, date_to: str | None) -> str:
    """Human-readable date window for PDF/report headers."""
    if date_from and date_to:
        return f"{date_from} to {date_to} (IST)"
    if date_from:
        return f"from {date_from} (IST)"
    if date_to:
        return f"up to {date_to} (IST)"
    if date_str:
        return f"{date_str} (IST)"
    return "all-time"


def _filter_label(date_str: str | None, temperature: str, outcome: str = "all") -> str:
    """Filename-safe suffix that records which filters produced this file."""
    bits = []
    if date_str:
        bits.append(date_str)
    if temperature and temperature != "all":
        bits.append(temperature)
    if outcome and outcome != "all":
        bits.append(outcome)
    return "-".join(bits) if bits else _today_iso()


def _fmt_phone(raw: str | None) -> str:
    """Reports want phones in the 10-digit local format the operator
    actually reads — strip the +91 / 91 / leading-0 prefixes so the cell
    shows `9876543210` instead of `+919876543210`.

    Side benefit: 10 digits fits comfortably under Excel's 11-digit
    scientific-notation threshold, so cells render as the number you
    expect rather than `9.18E+11` when the column is narrow.
    """
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


def _fmt_mmss(secs: float | int | None) -> str:
    if not secs:
        return "0:00"
    s = int(secs)
    return f"{s // 60}:{s % 60:02d}"


# ─── Calls CSV ────────────────────────────────────────────────────────


@router.get("/calls.csv")
async def calls_csv(
    date: str | None = Query(None, description="YYYY-MM-DD (IST). Omit for all-time."),
    date_from: str | None = Query(None, description="Range start YYYY-MM-DD (IST). Overrides `date`."),
    date_to: str | None = Query(None, description="Range end YYYY-MM-DD (IST), inclusive. Overrides `date`."),
    temperature: str = Query("all", description="all | hot | warm | cold"),
    outcome: str = Query("all", description="all | completed | no_answer | busy | no_speech"),
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """One row per call with phone, duration, billable time, outcome, transcript.

    `date` (YYYY-MM-DD, interpreted in IST) restricts to calls that started
    on that civil day. `temperature` (all|hot|warm|cold) filters on
    `Call.lead_temperature`. `outcome` (all | completed | no_answer | busy
    | no_speech) maps DIRECTLY to a single `Call.outcome` value — pick
    exactly the outcome you want to inspect. Both filters compose.
    """
    if temperature not in VALID_TEMPS:
        raise HTTPException(400, f"temperature must be one of {sorted(VALID_TEMPS)}")
    if outcome not in VALID_OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {sorted(VALID_OUTCOMES)}")
    start, end = _parse_range_to_utc_window(date, date_from, date_to)

    stmt = (
        select(models.Call, models.Lead)
        .outerjoin(models.Lead, models.Lead.id == models.Call.lead_id)
        .order_by(models.Call.id.asc())
    )
    if start is not None:
        stmt = stmt.where(models.Call.created_at >= start, models.Call.created_at < end)
    if temperature != "all":
        stmt = stmt.where(models.Call.lead_temperature == temperature)
    outcome_expr = _outcome_filter(outcome)
    if outcome_expr is not None:
        stmt = stmt.where(outcome_expr)
    rows = (await db.execute(stmt)).all()

    # Pre-fetch turns grouped by call_id for transcript flattening.
    turns_by_call: dict[int, list[models.Turn]] = {}
    for t in (
        await db.execute(select(models.Turn).order_by(models.Turn.call_id, models.Turn.turn_index))
    ).scalars():
        turns_by_call.setdefault(t.call_id, []).append(t)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(_csv_branding_rows())
    w.writerow([
        "report_date", "call_id", "started_at", "ended_at", "phone", "caller_name",
        "program", "temperature", "lead_reasons",
        "duration_seconds", "duration_mmss",
        "billable_seconds", "billable_minutes", "billable_mmss",
        "outcome", "transcript",
    ])
    today = _today_iso()
    for call, lead in rows:
        dur = float(call.duration_s or 0)
        bs = billable_seconds(dur)
        # Flatten transcript: "user: ... | assistant: ... | user: ..."
        transcript = " | ".join(
            f"{t.role}: {(t.text or '').replace(chr(10), ' ').replace('|', '/').strip()}"
            for t in turns_by_call.get(call.id, [])
            if t.text and t.text.strip()
        )
        w.writerow([
            today,
            call.id,
            _fmt_dt(call.created_at),
            _fmt_dt(call.ended_at),
            _fmt_phone(getattr(lead, "phone", None)),
            call.caller_name or (getattr(lead, "name", "") or "") or "",
            call.lead_program or "",
            call.lead_temperature or "",
            (call.lead_reasons or "").replace("\n", " "),
            f"{dur:.2f}",
            _fmt_mmss(dur),
            bs,
            round(bs / 60.0, 2),
            _fmt_mmss(bs),
            call.outcome or "",
            transcript,
        ])

    buf.seek(0)
    suffix = _filter_label(_range_label(date, date_from, date_to), temperature, outcome)
    headers = {
        "Content-Disposition": f'attachment; filename="gunivox-calls-{suffix}.csv"',
    }
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)


# ─── Leads CSV ────────────────────────────────────────────────────────


@router.get("/leads.csv")
async def leads_csv(
    date: str | None = Query(None, description="YYYY-MM-DD (IST). Omit for all-time."),
    date_from: str | None = Query(None, description="Range start YYYY-MM-DD (IST). Overrides `date`."),
    date_to: str | None = Query(None, description="Range end YYYY-MM-DD (IST), inclusive. Overrides `date`."),
    temperature: str = Query("all", description="all | hot | warm | cold"),
    outcome: str = Query("all", description="all | completed | no_answer | busy | no_speech"),
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """One row per lead with full profile + activity stats.

    Filters apply to the lead's CALLS (not the lead row itself):
      - `date`: include only leads that had at least one call on that day.
      - `temperature`: include only leads that had at least one call with
        that classification. "all" returns every lead in the system.
      - `outcome`: include only leads that had at least one call with that
        exact Call.outcome value (completed / no_answer / busy / no_speech).
    """
    if temperature not in VALID_TEMPS:
        raise HTTPException(400, f"temperature must be one of {sorted(VALID_TEMPS)}")
    if outcome not in VALID_OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {sorted(VALID_OUTCOMES)}")
    start, end = _parse_range_to_utc_window(date, date_from, date_to)

    # Decide which leads to include based on call activity, if filters set.
    include_lead_ids: set[int] | None = None
    if start is not None or temperature != "all" or outcome != "all":
        cstmt = select(models.Call.lead_id).distinct()
        if start is not None:
            cstmt = cstmt.where(
                models.Call.created_at >= start, models.Call.created_at < end,
            )
        if temperature != "all":
            cstmt = cstmt.where(models.Call.lead_temperature == temperature)
        outcome_expr = _outcome_filter(outcome)
        if outcome_expr is not None:
            cstmt = cstmt.where(outcome_expr)
        include_lead_ids = {
            int(x) for x in (await db.execute(cstmt)).scalars() if x is not None
        }

    leads_stmt = select(models.Lead).order_by(models.Lead.id.asc())
    if include_lead_ids is not None:
        if not include_lead_ids:
            leads_stmt = leads_stmt.where(models.Lead.id.is_(None))  # → empty
        else:
            leads_stmt = leads_stmt.where(models.Lead.id.in_(include_lead_ids))
    leads = (await db.execute(leads_stmt)).scalars().all()

    # Per-lead call stats (count + total billable seconds).
    call_stats: dict[int, dict[str, Any]] = {}
    for call in (await db.execute(select(models.Call))).scalars():
        if call.lead_id is None:
            continue
        s = call_stats.setdefault(call.lead_id, {"calls": 0, "billable_s": 0, "last": None})
        s["calls"] += 1
        s["billable_s"] += billable_seconds(call.duration_s or 0)
        if call.ended_at:
            # SQLite stores DateTime(timezone=True) as strings; older rows
            # land back as naive, newer ones as aware. Normalize both to
            # naive UTC for the max-comparison so we don't trip over the
            # "can't compare offset-naive and offset-aware" TypeError.
            ended_naive = (
                call.ended_at.astimezone(timezone.utc).replace(tzinfo=None)
                if call.ended_at.tzinfo is not None
                else call.ended_at
            )
            if s["last"] is None or ended_naive > s["last"]:
                s["last"] = ended_naive

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(_csv_branding_rows())
    w.writerow([
        "report_date", "lead_id", "name", "phone", "email", "status", "dnd",
        "source", "program_interest", "preferred_language", "notes",
        "attempt_count", "last_attempted_at", "created_at",
        "calls_made", "total_billable_seconds", "total_billable_minutes",
    ])
    today = _today_iso()
    for l in leads:
        s = call_stats.get(l.id, {"calls": 0, "billable_s": 0, "last": None})
        w.writerow([
            today,
            l.id,
            l.name or "",
            _fmt_phone(l.phone),
            l.email or "",
            l.status or "",
            "yes" if l.dnd else "no",
            l.source or "",
            l.program_interest or "",
            l.preferred_language or "",
            (l.notes or "").replace("\n", " "),
            l.attempt_count or 0,
            _fmt_dt(l.last_attempted_at),
            _fmt_dt(l.created_at),
            s["calls"],
            s["billable_s"],
            round(s["billable_s"] / 60.0, 2),
        ])

    buf.seek(0)
    suffix = _filter_label(_range_label(date, date_from, date_to), temperature, outcome)
    headers = {
        "Content-Disposition": f'attachment; filename="gunivox-leads-{suffix}.csv"',
    }
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)


# ─── PDF visual summary ───────────────────────────────────────────────


@router.get("/summary.pdf")
async def summary_pdf(
    date: str | None = Query(None, description="YYYY-MM-DD (IST). Omit for all-time."),
    date_from: str | None = Query(None, description="Range start YYYY-MM-DD (IST). Overrides `date`."),
    date_to: str | None = Query(None, description="Range end YYYY-MM-DD (IST), inclusive. Overrides `date`."),
    temperature: str = Query("all", description="all | hot | warm | cold"),
    outcome: str = Query("all", description="all | completed | no_answer | busy | no_speech"),
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Visual PDF report — header, stats, calls table, with a clean look.

    Filters: `date` restricts to calls started on that IST day; `temperature`
    restricts to calls of that classification; `outcome` restricts to calls
    of that exact Call.outcome value (completed / no_answer / busy / no_speech).
    """
    if temperature not in VALID_TEMPS:
        raise HTTPException(400, f"temperature must be one of {sorted(VALID_TEMPS)}")
    if outcome not in VALID_OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {sorted(VALID_OUTCOMES)}")
    start, end = _parse_range_to_utc_window(date, date_from, date_to)
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )

    # ── Pull data (respecting filters) ──
    cstmt = (
        select(models.Call, models.Lead)
        .outerjoin(models.Lead, models.Lead.id == models.Call.lead_id)
        .order_by(models.Call.id.desc())
    )
    if start is not None:
        cstmt = cstmt.where(models.Call.created_at >= start, models.Call.created_at < end)
    if temperature != "all":
        cstmt = cstmt.where(models.Call.lead_temperature == temperature)
    outcome_expr = _outcome_filter(outcome)
    if outcome_expr is not None:
        cstmt = cstmt.where(outcome_expr)
    calls = (await db.execute(cstmt)).all()

    # "Total leads" tile — when a date is set, count leads that had activity
    # that day; otherwise count all leads in the system.
    if start is not None:
        lead_ids = {c.lead_id for c, _l in calls if c.lead_id is not None}
        total_leads_count = len(lead_ids)
    else:
        total_leads = (await db.execute(select(models.Lead.id))).scalars().all()
        total_leads_count = len(total_leads)

    # Aggregate stats.
    total_calls = len(calls)
    total_billable_s = 0
    outcomes: dict[str, int] = {}
    temps: dict[str, int] = {"hot": 0, "warm": 0, "cold": 0}
    for call, _lead in calls:
        total_billable_s += billable_seconds(call.duration_s or 0)
        oc = call.outcome or "unknown"
        outcomes[oc] = outcomes.get(oc, 0) + 1
        if call.lead_temperature in temps:
            temps[call.lead_temperature] += 1

    today = _today_iso()
    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    # ── Build PDF ──
    # Register a Gujarati-capable font so caller names render properly.
    GUJ, GUJ_B = _ensure_pdf_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        # Top margin makes room for the branded header; bottom for the
        # contact-info footer. See _draw_branding().
        topMargin=(BRANDING_HEADER_HEIGHT_MM + 6) * mm,
        bottomMargin=(BRANDING_FOOTER_HEIGHT_MM + 6) * mm,
        title=f"Gunivox Report {today}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=GUJ_B, fontSize=22,
                        textColor=colors.HexColor("#c4302b"), spaceAfter=4)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontName=GUJ, fontSize=10,
                         textColor=colors.HexColor("#666666"), spaceAfter=14)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=GUJ_B, fontSize=13,
                        textColor=colors.HexColor("#222222"), spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["Normal"], fontName=GUJ, fontSize=9, leading=12)

    story: list[Any] = []

    story.append(Paragraph("Gunivox — Call Activity Report", h1))
    # Subtitle reflects active filters so it's unambiguous what the report covers.
    filter_bits: list[str] = []
    filter_bits.append(f"<b>Date</b>: {_range_human(date, date_from, date_to)}")
    filter_bits.append(
        f"<b>Temperature</b>: {temperature if temperature != 'all' else 'all'}"
    )
    filter_bits.append(
        f"<b>Outcome</b>: {outcome.replace('_', ' ') if outcome != 'all' else 'all'}"
    )
    story.append(Paragraph(
        f"Ganpat University admissions dialer · Generated {now_str}<br/>"
        + " · ".join(filter_bits),
        sub,
    ))

    # ── Summary tiles (as a 2x3 grid in a table) ──
    story.append(Paragraph("Summary", h2))
    minutes = round(total_billable_s / 60.0, 2)
    summary_data = [
        ["Total leads", str(total_leads_count), "Total calls", str(total_calls)],
        ["Billable minutes", f"{minutes:.2f}", "Hot / Warm / Cold",
         f"{temps['hot']} / {temps['warm']} / {temps['cold']}"],
    ]
    summary_tbl = Table(summary_data, colWidths=[40 * mm, 38 * mm, 40 * mm, 38 * mm])
    summary_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), GUJ),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f7")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f5f5f7")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#666666")),
        ("FONTNAME", (1, 0), (1, -1), GUJ_B),
        ("FONTNAME", (3, 0), (3, -1), GUJ_B),
        ("FONTSIZE", (1, 0), (1, -1), 13),
        ("FONTSIZE", (3, 0), (3, -1), 13),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
    ]))
    story.append(summary_tbl)

    # ── Outcomes breakdown ──
    if outcomes:
        story.append(Paragraph("Call outcomes", h2))
        oc_rows = [["Outcome", "Count"]]
        for oc, n in sorted(outcomes.items(), key=lambda kv: -kv[1]):
            oc_rows.append([oc, str(n)])
        oc_tbl = Table(oc_rows, colWidths=[80 * mm, 30 * mm])
        oc_tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), GUJ),
            ("FONTNAME", (0, 0), (-1, 0), GUJ_B),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c4302b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(oc_tbl)

    # ── Per-call list ──
    story.append(PageBreak())
    story.append(Paragraph("Calls", h2))
    if not calls:
        story.append(Paragraph("No calls in the system.", body))
    else:
        rows_data: list[list[Any]] = [
            ["#", "When", "Phone", "Caller", "Duration", "Billable", "Outcome"]
        ]
        for call, lead in calls:
            dur = float(call.duration_s or 0)
            rows_data.append([
                str(call.id),
                _fmt_dt(call.created_at),
                _fmt_phone(getattr(lead, "phone", None)) or "—",
                Paragraph(
                    call.caller_name or (getattr(lead, "name", "") or "—"),
                    ParagraphStyle("c", fontName=GUJ, fontSize=8, leading=10),
                ),
                _fmt_mmss(dur),
                _fmt_mmss(billable_seconds(dur)),
                call.outcome or "—",
            ])
        col_widths = [10 * mm, 32 * mm, 32 * mm, 38 * mm, 18 * mm, 18 * mm, 25 * mm]
        tbl = Table(rows_data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), GUJ),
            ("FONTNAME", (0, 0), (-1, 0), GUJ_B),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c4302b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (4, 0), (5, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        f"<i>Billing rules: 1–30 sec rounds up to 30 sec; >30 sec rounds up to "
        f"the next full minute. Reference rate (suppressed in UI): "
        f"₹{RATE_INR_PER_MINUTE}/minute. Billing epoch: "
        f"{BILLING_EPOCH.isoformat()}.</i>",
        ParagraphStyle("footer", fontSize=7.5, textColor=colors.HexColor("#888888"),
                       leading=10),
    ))

    doc.build(story, onFirstPage=_draw_branding, onLaterPages=_draw_branding)
    buf.seek(0)
    suffix = _filter_label(_range_label(date, date_from, date_to), temperature, outcome)
    headers = {
        "Content-Disposition": f'attachment; filename="gunivox-report-{suffix}.pdf"',
    }
    return Response(content=buf.read(), media_type="application/pdf", headers=headers)


# ─── PDF: Leads with transcripts (only picked-up calls) ───────────────


@router.get("/leads_transcripts.pdf")
async def leads_transcripts_pdf(
    date: str | None = Query(None, description="YYYY-MM-DD (IST). Omit for all-time."),
    date_from: str | None = Query(None, description="Range start YYYY-MM-DD (IST). Overrides `date`."),
    date_to: str | None = Query(None, description="Range end YYYY-MM-DD (IST), inclusive. Overrides `date`."),
    temperature: str = Query("all", description="all | hot | warm | cold"),
    outcome: str = Query("all", description="all | completed | no_answer | busy | no_speech"),
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Readable PDF — one section per LEAD with their call activity.

    Default behaviour (outcome=all or outcome=completed): show only leads
    that were picked up — one Call with at least one Turn — with the full
    user/assistant transcript laid out turn-by-turn.

    When outcome=no_answer / busy / no_speech: show the matching leads
    with their call duration + outcome, and a "No transcript — caller did
    not respond" note in place of the conversation. Those calls don't
    have Turn rows by design — the bridge only records turns once Sarvam
    transcribes inbound audio, which only happens after pickup + speech.

    Honours the same date + temperature filters as the other reports.
    """
    if temperature not in VALID_TEMPS:
        raise HTTPException(400, f"temperature must be one of {sorted(VALID_TEMPS)}")
    if outcome not in VALID_OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {sorted(VALID_OUTCOMES)}")
    start, end = _parse_range_to_utc_window(date, date_from, date_to)

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )
    from xml.sax.saxutils import escape as _xml_escape

    # Register Nirmala UI (or fallback) so Gujarati text isn't black boxes.
    GUJ, GUJ_B = _ensure_pdf_fonts()

    # ── 1. Pull every call (with lead) that survived the filters ──
    cstmt = (
        select(models.Call, models.Lead)
        .outerjoin(models.Lead, models.Lead.id == models.Call.lead_id)
        .order_by(models.Call.id.asc())
    )
    if start is not None:
        cstmt = cstmt.where(models.Call.created_at >= start, models.Call.created_at < end)
    if temperature != "all":
        cstmt = cstmt.where(models.Call.lead_temperature == temperature)
    outcome_expr = _outcome_filter(outcome)
    if outcome_expr is not None:
        cstmt = cstmt.where(outcome_expr)
    call_rows = (await db.execute(cstmt)).all()

    # ── 2. Index turns by call_id ──
    turns_by_call: dict[int, list[models.Turn]] = {}
    for t in (
        await db.execute(
            select(models.Turn).order_by(models.Turn.call_id, models.Turn.turn_index)
        )
    ).scalars():
        turns_by_call.setdefault(t.call_id, []).append(t)

    # ── 3. Group calls by lead.
    #       When "all" or "completed": keep only leads with at least one
    #       call that has turns — the original "picked up + spoke" gate.
    #       For any other specific outcome (no_answer / busy / no_speech):
    #       KEEP the no-turn calls because that's exactly what the user
    #       wants to inspect (duration + outcome with a "no transcript"
    #       note instead of dialogue).
    require_turns = outcome == "all" or outcome in OUTCOMES_WITH_TRANSCRIPT
    calls_by_lead: dict[int, list[tuple[Any, Any]]] = {}
    for call, lead in call_rows:
        if call.lead_id is None:
            continue
        if require_turns and not turns_by_call.get(call.id):
            continue
        calls_by_lead.setdefault(call.lead_id, []).append((call, lead))

    # ── 4. Build PDF ──
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        # Bumped to match the branding header/footer reserved space.
        topMargin=(BRANDING_HEADER_HEIGHT_MM + 6) * mm,
        bottomMargin=(BRANDING_FOOTER_HEIGHT_MM + 6) * mm,
        title=f"Gunivox Transcripts {_range_label(date, date_from, date_to) or _today_iso()}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=GUJ_B, fontSize=22,
                        textColor=colors.HexColor("#c4302b"), spaceAfter=4)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontName=GUJ, fontSize=10,
                         textColor=colors.HexColor("#666666"), spaceAfter=12)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=GUJ_B, fontSize=15,
                        textColor=colors.HexColor("#c4302b"),
                        spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontName=GUJ, fontSize=11,
                        textColor=colors.HexColor("#444444"),
                        spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["Normal"], fontName=GUJ,
                          fontSize=10, leading=14)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontName=GUJ, fontSize=9,
                          textColor=colors.HexColor("#666666"), leading=12)
    # Turn styles: a soft blue ribbon for the user, a soft red ribbon for AI.
    user_style = ParagraphStyle(
        "user", fontName=GUJ, fontSize=10, leading=14, leftIndent=8,
        textColor=colors.HexColor("#1d4ed8"),  # blue-700
        spaceBefore=2, spaceAfter=2,
    )
    ai_style = ParagraphStyle(
        "ai", fontName=GUJ, fontSize=10, leading=14, leftIndent=8,
        textColor=colors.HexColor("#000000"),
        spaceBefore=2, spaceAfter=2,
    )

    story: list[Any] = []

    # Header
    story.append(Paragraph("Gunivox — Leads & Transcripts", h1))
    filter_bits = [
        f"<b>Date</b>: {_range_human(date, date_from, date_to)}",
        f"<b>Temperature</b>: {temperature}",
        f"<b>Outcome</b>: {outcome.replace('_', ' ')}",
    ]
    header_caption = (
        "Picked-up calls only" if require_turns
        else f"Showing {outcome.replace('_', ' ')} calls — no transcript available"
    )
    story.append(Paragraph(
        f"{header_caption} · Generated {now_str}<br/>" + " · ".join(filter_bits),
        sub,
    ))

    if not calls_by_lead:
        story.append(Paragraph(
            "<i>No picked-up leads matched these filters. Try widening the date "
            "or selecting a different temperature.</i>", body,
        ))
    else:
        story.append(Paragraph(
            f"<b>{len(calls_by_lead)}</b> lead{'s' if len(calls_by_lead) != 1 else ''} "
            f"in this report.",
            body,
        ))

    # Per-lead sections, ordered by lead_id (stable)
    for lead_id in sorted(calls_by_lead.keys()):
        lead_calls = calls_by_lead[lead_id]
        # Lead row is the same across all calls of this lead — grab from first.
        _, lead = lead_calls[0]
        lead_name = (lead.name or f"Lead #{lead_id}") if lead else f"Lead #{lead_id}"
        lead_phone = _fmt_phone(lead.phone if lead else None) or "—"
        lead_program = (lead.program_interest if lead else "") or ""

        story.append(PageBreak())
        story.append(Paragraph(_xml_escape(lead_name), h2))

        # Lead-level metadata table
        meta_rows = [
            ["Phone", lead_phone,
             "Program interest", lead_program or "—"],
            ["Status", (lead.status if lead else "") or "—",
             "DND", "yes" if (lead and lead.dnd) else "no"],
            ["Source", (lead.source if lead else "") or "—",
             "Calls", str(len(lead_calls))],
        ]
        meta_tbl = Table(meta_rows, colWidths=[28 * mm, 50 * mm, 28 * mm, 50 * mm])
        meta_tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), GUJ),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f7")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f5f5f7")),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#666666")),
            ("FONTNAME", (1, 0), (1, -1), GUJ_B),
            ("FONTNAME", (3, 0), (3, -1), GUJ_B),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ]))
        story.append(meta_tbl)
        story.append(Spacer(1, 4 * mm))

        # One block per CALL of this lead
        for idx, (call, _l) in enumerate(lead_calls, start=1):
            dur = float(call.duration_s or 0)
            bs = billable_seconds(dur)
            call_header = (
                f"<b>Call #{call.id}</b> · {_fmt_dt(call.created_at)} · "
                f"duration {_fmt_mmss(dur)} · billable {_fmt_mmss(bs)} · "
                f"outcome <b>{(call.outcome or '—')}</b>"
                + (f" · temperature <b>{call.lead_temperature}</b>" if call.lead_temperature else "")
            )
            story.append(Paragraph(call_header, h3))

            # Conversation — each turn on its own line, colour-coded.
            call_turns = turns_by_call.get(call.id, [])
            rendered_any = False
            for t in call_turns:
                text = (t.text or "").strip()
                if not text:
                    continue
                # Render line breaks within a turn as PDF line breaks.
                text_html = _xml_escape(text).replace("\n", "<br/>")
                if t.role == "user":
                    story.append(Paragraph(
                        f"<b>Caller:</b> {text_html}", user_style,
                    ))
                else:
                    story.append(Paragraph(
                        f"<b>Agent:</b> {text_html}", ai_style,
                    ))
                rendered_any = True
            # No-turn calls (busy / no_answer / no_speech): the user
            # specifically asked to still see these via the outcome
            # filter — show a single grey line in place of dialogue so
            # the call_header + meta still tells the story.
            if not rendered_any:
                story.append(Paragraph(
                    "<i>No transcript — caller did not respond.</i>",
                    meta,
                ))
            # If there's another call for this same lead below, leave a gap.
            if idx < len(lead_calls):
                story.append(Spacer(1, 3 * mm))

    doc.build(story, onFirstPage=_draw_branding, onLaterPages=_draw_branding)
    buf.seek(0)
    suffix = _filter_label(_range_label(date, date_from, date_to), temperature, outcome)
    headers = {
        "Content-Disposition":
            f'attachment; filename="gunivox-leads-transcripts-{suffix}.pdf"',
    }
    return Response(content=buf.read(), media_type="application/pdf", headers=headers)
