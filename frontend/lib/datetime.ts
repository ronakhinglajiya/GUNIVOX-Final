/**
 * Date/time helpers for backend timestamps.
 *
 * The FastAPI backend serialises SQLAlchemy DateTime columns as ISO
 * strings WITHOUT a timezone suffix (e.g. "2026-05-22 10:18:23.356110").
 * The values are actually UTC, but `new Date(string)` in V8/JS parses
 * naïve strings as LOCAL time — so on an IST machine the display was
 * 5h 30m off (date right, clock wrong).
 *
 * `parseServerDate` normalises by appending 'Z' when no timezone marker
 * is present, forcing UTC parse. From there `.toLocaleString()` /
 * `.toLocaleDateString()` / `.toLocaleTimeString()` correctly converts
 * to whichever timezone the browser is in.
 */

export function parseServerDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  // Already has timezone info? Use as-is.
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) {
    return new Date(iso);
  }
  // Replace the space separator some backends use with 'T' so ISO-8601
  // parsing is unambiguous, then mark as UTC.
  const normalised = iso.replace(" ", "T") + "Z";
  return new Date(normalised);
}

/** Format as IST date+time string (e.g. "22/05/2026, 3:48:23 pm"). */
export function fmtIST(iso: string | null | undefined): string {
  const d = parseServerDate(iso);
  if (!d) return "—";
  return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
}

/** Format as IST date only (e.g. "22/05/2026"). */
export function fmtISTDate(iso: string | null | undefined): string {
  const d = parseServerDate(iso);
  if (!d) return "—";
  return d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
}

/** Format as IST time only (e.g. "3:48:23 pm"). */
export function fmtISTTime(iso: string | null | undefined): string {
  const d = parseServerDate(iso);
  if (!d) return "—";
  return d.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" });
}
