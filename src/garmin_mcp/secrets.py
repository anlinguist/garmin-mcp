"""Secret loading: GCP Secret Manager first, env var fallback for local dev."""

from __future__ import annotations

import logging
import os

from .logs import log_event

logger = logging.getLogger(__name__)


def _project_from_metadata() -> str | None:
    try:
        import google.auth

        _, project = google.auth.default()
        return project
    except Exception:
        return None


def get_secret(secret_name: str, env_fallback: str, project: str = "") -> str:
    """Return a secret value.

    Order: env var ``env_fallback`` (only if set, intended for local dev), then
    Secret Manager ``projects/<project>/secrets/<secret_name>/versions/latest``.
    Raises RuntimeError without echoing the value.
    """
    value = os.environ.get(env_fallback, "").strip()
    if value:
        log_event(logger, logging.INFO, "secret_loaded", secret=env_fallback, source="env")
        return value

    if not secret_name:
        raise RuntimeError(f"No secret configured: set {env_fallback} or a secret name")

    from google.cloud import secretmanager

    project = project or _project_from_metadata() or ""
    if not project:
        raise RuntimeError("GCP_PROJECT not set and could not be discovered")
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    try:
        resp = client.access_secret_version(request={"name": name})
    except Exception as e:  # don't leak details beyond the type
        raise RuntimeError(
            f"Could not access secret {secret_name!r} ({type(e).__name__})"
        ) from None
    log_event(logger, logging.INFO, "secret_loaded", secret=secret_name, source="secret_manager")
    return resp.payload.data.decode("utf-8").strip()
