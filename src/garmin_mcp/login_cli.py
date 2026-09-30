"""garmin-mcp-login: one-time interactive Garmin login that writes encrypted tokens.

Run on the VM (same egress IP as the server) as the service user:
    sudo garmin-mcp-login --user andrew   (wrapper installed by bootstrap-vm.sh)

Makes exactly one login attempt per run. It never retries automatically:
Garmin rate-limits logins per IP/fingerprint. The password is held only in
memory for the duration of this process and is never written anywhere.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from .config import Settings
from .logs import setup_logging
from .providers import CloudflareBlocked, LoginStatus
from .providers.unofficial import UnofficialProvider, translate_error
from .secrets import get_secret
from .tokens import EncryptedFileTokenStore, validate_user_id

MAX_MFA_CODE_TRIES = 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user", required=True, help="internal user id (e.g. andrew)")
    ap.add_argument("--email", help="Garmin email (prompted if omitted)")
    ap.add_argument("--delete", action="store_true", help="delete stored tokens and exit")
    args = ap.parse_args(argv)

    os.umask(0o077)
    os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost")
    settings = Settings.from_env()
    setup_logging("WARNING")
    logging.getLogger("garmin_mcp").setLevel(logging.INFO)

    user_id = validate_user_id(args.user)
    key = get_secret(settings.fernet_key_secret_name, "GARMIN_MCP_FERNET_KEY", settings.gcp_project)
    store = EncryptedFileTokenStore(settings.token_dir, key)

    if args.delete:
        store.delete(user_id)
        print(f"Deleted tokens for {user_id}.")
        return 0

    provider = UnofficialProvider(store, impersonate_api_tls=settings.garmin_api_tls_impersonation)
    email = args.email or input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        return 2

    print("Logging in to Garmin (single attempt; may take up to a minute)...")
    try:
        result = provider.login_start(user_id, email, password)
    except Exception as e:
        print(f"Login failed ({type(e).__name__}): {_safe(e)}", file=sys.stderr)
        if isinstance(translate_error(e, user_id), CloudflareBlocked) or "cloudflare" in str(e).lower():
            print("This looks like a Cloudflare block/challenge, not a credential problem.",
                  file=sys.stderr)
        print("Not retrying automatically. Wait several minutes before trying again.", file=sys.stderr)
        return 1
    finally:
        del password

    if result.status is LoginStatus.NEEDS_MFA:
        assert result.handle is not None
        for attempt in range(1, MAX_MFA_CODE_TRIES + 1):
            code = input("MFA code from Garmin (email/SMS/app): ").strip()
            try:
                provider.login_complete(result.handle, code)
                break
            except Exception as e:
                print(f"MFA failed ({type(e).__name__}): {_safe(e)}", file=sys.stderr)
                if attempt == MAX_MFA_CODE_TRIES:
                    return 1
                print("You can re-enter the code (same login session).")

    print(f"Tokens saved (encrypted) to {settings.token_dir}/{user_id}.fernet")
    try:
        sess = provider.get_client(user_id)
        from garminconnect import Garmin

        name = sess.call(Garmin.get_full_name, {}, retry=False)
        print(f"Verified: resumed session for {name or 'your account'}.")
    except Exception as e:
        print(f"Warning: saved tokens but verification failed: {_safe(e)}", file=sys.stderr)
        return 1
    return 0


def _safe(e: BaseException) -> str:
    from .logs import redact

    return redact(str(e))[:300]


if __name__ == "__main__":
    sys.exit(main())
