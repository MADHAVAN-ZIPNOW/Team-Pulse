"""Saved meetings: one encrypted file per meeting, in a private folder per person.

Meeting conversations are sensitive, so:
- every file is encrypted (Fernet, key derived from SESSION_SECRET); nothing is written in plain text;
- a meeting can only be opened by the person who owns it (the folder name is a hash of their identity);
- meeting ids are random and validated, so a request can never reach another file;
- meetings older than the retention period are deleted automatically.
If SESSION_SECRET changes, old files can no longer be read (they are skipped, not shown).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_ID = re.compile(r"^[0-9a-f]{32}$")


class MeetingStore:
    def __init__(self, directory: str | Path, secret: str, retention_days: int = 90) -> None:
        self._dir = Path(directory)
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("meetings:" + secret).encode()).digest()))
        self._retention = retention_days * 86400
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _owner_dir(self, owner: str) -> Path:
        d = self._dir / hashlib.sha256(owner.strip().lower().encode()).hexdigest()[:24]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _file(self, owner: str, meeting_id: str) -> Path | None:
        return self._owner_dir(owner) / f"{meeting_id}.enc" if _ID.match(meeting_id or "") else None

    def encode(self, meeting: dict[str, Any]) -> bytes:
        """Serialize and encrypt in the caller's thread (so the meeting cannot change while it is being read)."""
        return self._fernet.encrypt(json.dumps(meeting, ensure_ascii=False).encode())

    def write(self, owner: str, meeting_id: str, blob: bytes) -> None:
        path = self._file(owner, meeting_id)
        if not path:
            raise ValueError("Bad meeting id.")
        with self._lock:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)

    def load(self, owner: str) -> list[dict[str, Any]]:
        """Every readable meeting of this person. Also deletes the ones past the retention period."""
        out = []
        now = time.time()
        for f in self._owner_dir(owner).glob("*.enc"):
            if now - f.stat().st_mtime > self._retention:
                f.unlink(missing_ok=True)
                continue
            try:
                m = json.loads(self._fernet.decrypt(f.read_bytes()))
            except (InvalidToken, ValueError, OSError):
                continue
            if isinstance(m, dict) and m.get("id") == f.stem and str(m.get("owner", "")).strip().lower() == owner.strip().lower():
                m["stage"], m["stage_error"] = "", False  # a job that was running when the server stopped is not running now
                out.append(m)
        return out

    def load_prefs(self, owner: str) -> dict[str, Any]:
        path = self._owner_dir(owner) / "settings.bin"
        try:
            data = json.loads(self._fernet.decrypt(path.read_bytes())) if path.is_file() else {}
        except (InvalidToken, ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_prefs(self, owner: str, prefs: dict[str, Any]) -> None:
        self.write_raw(self._owner_dir(owner) / "settings.bin", self._fernet.encrypt(json.dumps(prefs).encode()))

    def write_raw(self, path: Path, blob: bytes) -> None:
        with self._lock:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)

    def delete(self, owner: str, meeting_id: str) -> bool:
        path = self._file(owner, meeting_id)
        if not path or not path.is_file():
            return False
        path.unlink()
        return True
