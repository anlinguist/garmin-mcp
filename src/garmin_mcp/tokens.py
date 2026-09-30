"""Per-user Garmin token storage.

The stored value is an opaque string (garminconnect's ``Client.dumps()`` JSON).
It is only ever written to disk encrypted. The Garmin password is never stored.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import stat
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TokenStoreError(Exception):
    pass


def validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not _USER_ID_RE.match(user_id):
        raise ValueError("user_id must match [a-z0-9][a-z0-9_-]{0,63}")
    return user_id


class TokenStore(ABC):
    @abstractmethod
    def load(self, user_id: str) -> str | None:
        """Return the stored token blob, or None if the user has none."""

    @abstractmethod
    def save(self, user_id: str, blob: str) -> None:
        """Persist the token blob atomically."""

    @abstractmethod
    def delete(self, user_id: str) -> None:
        """Remove the user's tokens (no-op if absent)."""


class EncryptedFileTokenStore(TokenStore):
    """One Fernet-encrypted file per user: ``<dir>/<user_id>.fernet``.

    Directory is forced to 0700, files are created 0600 via O_EXCL temp file +
    fsync + os.replace, so readers never see a partial file and a crash never
    leaves a truncated token file behind.
    """

    SUFFIX = ".fernet"

    def __init__(self, directory: Path, key: bytes | str) -> None:
        self._dir = Path(directory)
        self._fernet = Fernet(key)
        self._lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        if self._dir.is_symlink():
            raise TokenStoreError("token directory must not be a symlink")
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)

    def _path(self, user_id: str) -> Path:
        return self._dir / f"{validate_user_id(user_id)}{self.SUFFIX}"

    def load(self, user_id: str) -> str | None:
        path = self._path(user_id)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise TokenStoreError(f"refusing to read {path.name}: permissions too open")
            data = f.read()
        try:
            return self._fernet.decrypt(data).decode("utf-8")
        except InvalidToken:
            raise TokenStoreError(
                f"could not decrypt tokens for {user_id!r} (wrong key or corrupt file)"
            ) from None

    def save(self, user_id: str, blob: str) -> None:
        path = self._path(user_id)
        ciphertext = self._fernet.encrypt(blob.encode("utf-8"))
        tmp = self._dir / f".{path.name}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with self._lock:
            self._ensure_dir()
            fd = os.open(tmp, flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(ciphertext)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
                # Persist the rename itself.
                dfd = os.open(self._dir, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise

    def delete(self, user_id: str) -> None:
        with self._lock, contextlib.suppress(FileNotFoundError):
            self._path(user_id).unlink()


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")
