"""Placeholder for Garmin's official Connect Developer Program (Health/Activity API).

When approved for the program, implement this with Garmin's OAuth 2.0 PKCE
flow: ``login_start`` returns an authorization URL instead of taking a
password, ``login_complete`` exchanges the code, and ``get_client`` returns a
session whose registry maps to the official endpoints. The official API is
push/ping based (Garmin posts summaries to a webhook), so expect to add a
small ingest store rather than calling Garmin on every tool invocation.
"""

from __future__ import annotations

from .base import GarminProvider, GarminSession, LoginHandle, LoginResult


class OfficialApiProvider(GarminProvider):
    def get_client(self, user_id: str) -> GarminSession:
        raise NotImplementedError("OfficialApiProvider is not implemented yet")

    def login_start(self, user_id: str, email: str, password: str) -> LoginResult:
        raise NotImplementedError("Official API uses OAuth redirect, not passwords")

    def login_complete(self, handle: LoginHandle, mfa_code: str) -> None:
        raise NotImplementedError
