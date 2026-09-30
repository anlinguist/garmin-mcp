#!/usr/bin/env python3
"""Live smoke test against a real Garmin account. NOT run in CI.

Prereq: garmin-mcp-login has been run for the user on this machine.
Usage (on the VM):
    sudo -u garmin-mcp bash -c 'set -a; . /etc/garmin-mcp/env; set +a; \
        /opt/garmin-mcp/.venv/bin/python /opt/garmin-mcp/scripts/smoke_live.py --user andrew'

Prints only counts, dates, sports and IDs, never raw payloads.
"""

from __future__ import annotations

import argparse
import os
import sys

from garmin_mcp.config import Settings
from garmin_mcp.logs import setup_logging
from garmin_mcp.providers import GarminError
from garmin_mcp.providers.unofficial import UnofficialProvider
from garmin_mcp.registry import Registry
from garmin_mcp.resilience import RateLimiter, TTLCache
from garmin_mcp.secrets import get_secret
from garmin_mcp.tokens import EncryptedFileTokenStore
from garmin_mcp.tools import GarminService


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    args = ap.parse_args()
    os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost")
    settings = Settings.from_env()
    setup_logging("WARNING")
    key = get_secret(settings.fernet_key_secret_name, "GARMIN_MCP_FERNET_KEY", settings.gcp_project)
    provider = UnofficialProvider(EncryptedFileTokenStore(settings.token_dir, key),
                                  impersonate_api_tls=settings.garmin_api_tls_impersonation)
    svc = GarminService(provider, Registry(), allow_writes=False, cache=TTLCache(0),
                        limiter=RateLimiter(0), max_response_bytes=settings.max_response_bytes)
    try:
        out = svc.execute(args.user, "get_activities", {"start": 0, "limit": 5})
        acts = out["result"] or []
        print(f"[1/2] last {len(acts)} activities:")
        for a in acts:
            print(f"   {a.get('startTimeLocal')}  {a.get('activityType', {}).get('typeKey'):<16} "
                  f"id={a.get('activityId')}")
        if not acts:
            print("No activities; skipping details.")
            return 0
        aid = str(acts[0]["activityId"])
        det = svc.execute(args.user, "get_activity", {"activity_id": aid})
        keys = sorted((det["result"] or {}).keys())
        print(f"[2/2] get_activity({aid}): {len(keys)} top-level fields, truncated={det['truncated']}")
        recent = svc.recent_activities(args.user, days=14)
        print(f"recent_activities(14d): {recent['count']} activities")
    except GarminError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
