"""Probe the outbound-call path step by step to find where it breaks.

Tests in order:
  1. Env vars all set?
  2. Can we reach api.vobiz.ai at all? (DNS / TCP)
  3. Does basic auth work? (any 4xx other than 401)
  4. Try an actual Originate call to a test number and read the response.
"""
import asyncio
import os
import httpx

from app.config import get_settings


async def main() -> None:
    s = get_settings()

    print("=== 1. Settings ===")
    print(f"  VOBIZ_AUTH_ID         : {'<set, %d chars>' % len(s.vobiz_auth_id) if s.vobiz_auth_id else 'MISSING'}")
    print(f"  VOBIZ_AUTH_TOKEN      : {'<set, %d chars>' % len(s.vobiz_auth_token) if s.vobiz_auth_token else 'MISSING'}")
    print(f"  VOBIZ_BASE_URL        : {s.vobiz_base_url!r}")
    print(f"  VOBIZ_CALLER_ID       : {s.vobiz_caller_id!r}")
    print(f"  VOBIZ_PUBLIC_BASE_URL : {s.vobiz_public_base_url!r}")

    if not (s.vobiz_auth_id and s.vobiz_auth_token):
        print("ABORT — Vobiz auth not set.")
        return

    auth = httpx.BasicAuth(s.vobiz_auth_id, s.vobiz_auth_token)
    account_path = f"{s.vobiz_base_url.rstrip('/')}/api/v1/Account/{s.vobiz_auth_id}"

    async with httpx.AsyncClient(timeout=20.0) as http:
        # ── 2. Reachability ──────────────────────────────────────────
        print("\n=== 2. Reachability ===")
        try:
            r = await http.get(s.vobiz_base_url, timeout=8.0)
            print(f"  GET {s.vobiz_base_url} → HTTP {r.status_code} ({len(r.content)} bytes)")
        except Exception as e:
            print(f"  GET {s.vobiz_base_url} → TRANSPORT ERROR: {type(e).__name__}: {e}")

        # ── 3. Auth check (GET on Account is cheap, tells us auth works) ──
        print("\n=== 3. Auth probe (GET Account) ===")
        try:
            r = await http.get(account_path, auth=auth, timeout=15.0)
            body = r.text[:400].replace("\n", " ")
            print(f"  GET {account_path} → HTTP {r.status_code}")
            print(f"  body: {body!r}")
        except Exception as e:
            print(f"  GET {account_path} → TRANSPORT ERROR: {type(e).__name__}: {e}")

        # ── 4. Real originate to a TEST number ────────────────────────
        # Uses a number that should fail at the dial stage, not at our
        # API layer — we just want to know if Vobiz accepts the request.
        test_to = os.environ.get("PROBE_TO", "+919999999999")  # bogus number
        answer_url = (s.vobiz_public_base_url.rstrip("/") + "/vobiz-answer"
                       if s.vobiz_public_base_url else "https://example.com/answer")
        hangup_url = (s.vobiz_public_base_url.rstrip("/") + "/status"
                       if s.vobiz_public_base_url else "https://example.com/status")

        print(f"\n=== 4. Originate to {test_to} ===")
        payload = {
            "to": test_to,
            "from": s.vobiz_caller_id,
            "answer_url": answer_url,
            "answer_method": "POST",
            "hangup_url": hangup_url,
            "hangup_method": "POST",
            "ring_timeout": 45,
        }
        print(f"  payload keys: {list(payload.keys())}")
        print(f"  caller_id: {payload['from']!r}")
        try:
            r = await http.post(
                f"{account_path}/Call/", auth=auth, json=payload, timeout=20.0,
            )
            print(f"  POST {account_path}/Call/ → HTTP {r.status_code}")
            print(f"  response headers: content-type={r.headers.get('content-type','?')}")
            print(f"  response body (first 600 chars): {r.text[:600]!r}")
        except Exception as e:
            print(f"  POST originate → TRANSPORT ERROR: {type(e).__name__}: {e}")


asyncio.run(main())
