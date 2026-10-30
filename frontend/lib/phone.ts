// 2026-06-06: masking removed — counsellors now see full numbers same as
// admin. Kept the (phone, isAdmin) signature so every call site keeps
// compiling without churn; the `isAdmin` arg is just ignored.
export function maskPhone(phone: string | null | undefined, _isAdmin: boolean): string {
  if (!phone) return "—";
  return phone;
}
