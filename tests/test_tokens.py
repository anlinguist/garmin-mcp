from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from garmin_mcp.tokens import EncryptedFileTokenStore, TokenStoreError

BLOB = '{"di_token": "eyJhbGciOi.secret-access", "di_refresh_token": "refresh-secret", "di_client_id": "x"}'


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_roundtrip_and_encrypted_at_rest(tmp_path: Path, fernet_key: str) -> None:
    store = EncryptedFileTokenStore(tmp_path / "tokens", fernet_key)
    store.save("andrew", BLOB)
    assert store.load("andrew") == BLOB
    raw = (tmp_path / "tokens" / "andrew.fernet").read_bytes()
    assert b"refresh-secret" not in raw and b"di_token" not in raw
    assert Fernet(fernet_key).decrypt(raw).decode() == BLOB


def test_permissions(tmp_path: Path, fernet_key: str) -> None:
    d = tmp_path / "tokens"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    store = EncryptedFileTokenStore(d, fernet_key)
    store.save("andrew", BLOB)
    assert _mode(d) == 0o700
    assert _mode(d / "andrew.fernet") == 0o600


def test_refuses_world_readable_file(tmp_path: Path, fernet_key: str) -> None:
    store = EncryptedFileTokenStore(tmp_path, fernet_key)
    store.save("andrew", BLOB)
    os.chmod(tmp_path / "andrew.fernet", 0o644)
    with pytest.raises(TokenStoreError, match="permissions"):
        store.load("andrew")


def test_missing_user_returns_none(tmp_path: Path, fernet_key: str) -> None:
    assert EncryptedFileTokenStore(tmp_path, fernet_key).load("nobody") is None


def test_wrong_key(tmp_path: Path, fernet_key: str) -> None:
    EncryptedFileTokenStore(tmp_path, fernet_key).save("andrew", BLOB)
    other = EncryptedFileTokenStore(tmp_path, Fernet.generate_key())
    with pytest.raises(TokenStoreError, match="could not decrypt") as exc:
        other.load("andrew")
    assert "refresh-secret" not in str(exc.value)


def test_atomic_write_preserves_old_file_on_failure(tmp_path: Path, fernet_key: str,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    store = EncryptedFileTokenStore(tmp_path, fernet_key)
    store.save("andrew", BLOB)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save("andrew", '{"di_token": "new"}')
    monkeypatch.undo()
    assert store.load("andrew") == BLOB  # old contents intact
    assert [p.name for p in tmp_path.iterdir()] == ["andrew.fernet"]  # no temp left behind


def test_atomic_write_uses_rename(tmp_path: Path, fernet_key: str,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    store = EncryptedFileTokenStore(tmp_path, fernet_key)
    seen: list[tuple[str, str]] = []
    real = os.replace

    def spy(src, dst):
        seen.append((Path(src).name, Path(dst).name))
        assert _mode(Path(src)) == 0o600  # temp file never world-readable
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.save("andrew", BLOB)
    assert len(seen) == 1
    src, dst = seen[0]
    assert src.startswith(".andrew.fernet.") and src.endswith(".tmp") and dst == "andrew.fernet"


@pytest.mark.parametrize("uid", ["../etc/passwd", "a/b", "", "UPPER", ".hidden", "x" * 65])
def test_user_id_validation(tmp_path: Path, fernet_key: str, uid: str) -> None:
    store = EncryptedFileTokenStore(tmp_path, fernet_key)
    with pytest.raises(ValueError):
        store.save(uid, BLOB)


def test_refuses_symlinked_file(tmp_path: Path, fernet_key: str) -> None:
    store = EncryptedFileTokenStore(tmp_path / "t", fernet_key)
    target = tmp_path / "elsewhere"
    target.write_bytes(b"x")
    (tmp_path / "t" / "andrew.fernet").symlink_to(target)
    with pytest.raises(OSError):
        store.load("andrew")
