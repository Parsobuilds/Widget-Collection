"""Shared test helpers: fake keyring, fake HTTP replies, temporary profile.

Every test stays offline: HTTP goes through ``providers.base.open_url``,
which ``FakeHttp`` replaces, and secrets go to ``MemoryKeyring``.
All values here are fake.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from typing import Any, Callable
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import keyring  # noqa: E402
import keyring.errors  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402

FAKE_KEY = "test-key-not-real-0000"


class MemoryKeyring(KeyringBackend):
    """A keyring that keeps passwords in a dict for the test's lifetime."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


class BrokenKeyring(MemoryKeyring):
    """A keyring whose every call fails (locked vault, missing backend)."""

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError(f"backend failed for {username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError(f"could not store {password}")


class KeyringMixin:
    """unittest mixin: install a fresh MemoryKeyring for every test."""

    def setUp(self) -> None:  # noqa: D401
        super().setUp()  # type: ignore[misc]
        self._old_keyring = keyring.get_keyring()
        self.keyring = MemoryKeyring()
        keyring.set_keyring(self.keyring)

    def tearDown(self) -> None:
        keyring.set_keyring(self._old_keyring)
        super().tearDown()  # type: ignore[misc]


class FakeResponse:
    """Stands in for the object ``urlopen`` returns."""

    def __init__(self, body: bytes | str | Any, status: int = 200) -> None:
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._buffer = io.BytesIO(body)
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    """An HTTPError like urllib raises for a 4xx/5xx reply."""
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, io.BytesIO(b'{"error": "fake"}'))


class FakeHttp:
    """Patch ``providers.base.open_url`` and record every request.

    ``replies`` is a list consumed in order; each item is a FakeResponse,
    a JSON-able object, an exception to raise, or a callable
    ``(request) -> reply``.
    """

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[Any] = []
        self._patcher = mock.patch("providers.base.open_url", side_effect=self._open)

    def _open(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"Unexpected HTTP call to {request.full_url}")
        reply = self.replies.pop(0)
        if callable(reply) and not isinstance(reply, FakeResponse):
            reply = reply(request)
        if isinstance(reply, BaseException):
            raise reply
        if not isinstance(reply, FakeResponse):
            reply = FakeResponse(reply)
        return reply

    def header(self, index: int, name: str) -> str | None:
        """Header value of request ``index`` (case-insensitive name)."""
        request = self.requests[index]
        for key, value in request.header_items():
            if key.lower() == name.lower():
                return value
        return None

    def __enter__(self) -> "FakeHttp":
        self._patcher.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._patcher.stop()


class TempProfile:
    """Point USERPROFILE and APPDATA at a fresh temporary folder."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "profile"
        self.appdata = self.home / "AppData" / "Roaming"
        self.xdg_config = self.home / ".config"
        # The folder that holds "DesktopWidgets" on this operating system.
        self.config_root = self.appdata if sys.platform == "win32" else self.xdg_config
        self.home.mkdir(parents=True)
        self._env = mock.patch.dict(
            os.environ,
            {
                "USERPROFILE": str(self.home),
                "APPDATA": str(self.appdata),
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.xdg_config),
            },
        )

    def write_json(self, relative: str, data: Any) -> Path:
        """Write ``data`` as JSON under the fake profile and return the path."""
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def __enter__(self) -> "TempProfile":
        self._env.start()
        os.environ.pop("CODEX_HOME", None)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._env.stop()
        self._tmp.cleanup()


def instance(kind: str, label: str = "Test", settings: dict[str, Any] | None = None, instance_id: str = "abcd1234"):
    """Build a ProviderInstance for tests."""
    from provider_config import ProviderInstance

    return ProviderInstance(instance_id=instance_id, kind=kind, label=label, settings=dict(settings or {}))


def raises(func: Callable[[], Any]) -> BaseException | None:
    """Return the exception ``func`` raises, or None."""
    try:
        func()
    except BaseException as exc:  # noqa: BLE001
        return exc
    return None
