"""Provider configuration for Usage Monitor (``config.json``).

The file lives at ``%APPDATA%\\DesktopWidgets\\UsageMonitor\\config.json`` on
Windows and ``$XDG_CONFIG_HOME/DesktopWidgets/UsageMonitor/config.json`` on
Linux, and holds only the user's provider choices and their non-secret
settings. API keys never appear here: they go to ``secrets_store`` (Windows
Credential Manager, or the Secret Service on Linux). A missing, empty or
unreadable file means "no providers yet".
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import secrets_store

CONFIG_VERSION: int = 2
DEFAULT_POLL_MINUTES: int = 5

# Settings keys that must never be stored in config.json whatever the kind
# says (defence in depth on top of each kind's "secret" FieldSpecs).
_ALWAYS_SECRET_KEYS = frozenset({"api_key", "secret", "token", "password", "access_token"})


@dataclass
class ProviderInstance:
    """One provider the user added (for example "my Claude sign-in")."""

    instance_id: str
    kind: str
    label: str
    enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready copy of this instance."""
        data = asdict(self)
        data["settings"] = copy.deepcopy(self.settings)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderInstance":
        """Build an instance from a config.json entry (tolerant of junk)."""
        settings = data.get("settings")
        return cls(
            instance_id=str(data.get("instance_id") or ""),
            kind=str(data.get("kind") or ""),
            label=str(data.get("label") or ""),
            enabled=bool(data.get("enabled", True)),
            settings=dict(settings) if isinstance(settings, dict) else {},
        )


def default_config() -> dict[str, Any]:
    """Return the first-run configuration: no providers at all."""
    return {"version": CONFIG_VERSION, "poll_minutes": DEFAULT_POLL_MINUTES, "providers": []}


def config_root() -> Path:
    """Return the folder that holds the ``DesktopWidgets`` folder.

    Windows: ``%APPDATA%`` (or the usual Roaming folder when it is missing).
    Linux: ``$XDG_CONFIG_HOME`` when it is an absolute path, else ``~/.config``.
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg and os.path.isabs(xdg):  # the XDG rules say a relative value must be ignored
        return Path(xdg)
    return Path.home() / ".config"


def data_dir() -> Path:
    """Return ``<config root>/DesktopWidgets/UsageMonitor``, creating it if needed."""
    folder = config_root() / "DesktopWidgets" / "UsageMonitor"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        if sys.platform == "win32":
            raise
        # Linux with an empty or unwritable HOME (for example HOME=""): keep running
        # with a temporary folder instead of crashing. Nothing secret is ever stored here.
        folder = Path(tempfile.gettempdir()) / "DesktopWidgets" / "UsageMonitor"
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def config_path() -> Path:
    """Return the path of ``config.json`` inside ``data_dir()``."""
    return data_dir() / "config.json"


def _is_valid_id(value: str) -> bool:
    """True for a short id made of letters, digits, '-' or '_'.

    New ids are always 8 hex characters; this check only weeds out empty or
    odd values from a hand-edited file.
    """
    return 0 < len(value) <= 32 and all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in value)


def _read_version(raw: dict[str, Any]) -> int:
    """Return the config format version of a raw file (1 when it has none)."""
    try:
        version = int(raw.get("version", 1))
    except (TypeError, ValueError):
        return 1
    return version if version >= 1 else 1


def _file_version(path: Path) -> int:
    """Return the version of the config file on disk (the current one when unknown)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or broken file has no version
        return CONFIG_VERSION
    if isinstance(raw, dict):
        return _read_version(raw)
    return CONFIG_VERSION


def _normalise(raw: Any) -> dict[str, Any]:
    """Turn whatever was read from disk into a valid config dictionary.

    Two rules run over every provider: an id that is empty or already used is
    replaced by a fresh one, and a setting that holds a secret is dropped (a
    secret belongs in the key store, never in config.json).
    """
    config = default_config()
    if not isinstance(raw, dict):
        return config

    version = _read_version(raw)
    if version > CONFIG_VERSION:
        # The file was written by a newer version of the app: its shape is
        # unknown, so none of it is used. The version is kept so the file is
        # never silently downgraded (save_config refuses to overwrite it).
        config["version"] = version
        return config

    try:
        poll = int(raw.get("poll_minutes", DEFAULT_POLL_MINUTES))
        config["poll_minutes"] = min(max(poll, 1), 1440)
    except (TypeError, ValueError):
        pass

    providers = raw.get("providers")
    if not isinstance(providers, list):
        return config

    used_ids: set[str] = set()
    for entry in providers:
        if not isinstance(entry, dict) or not entry.get("kind"):
            continue
        instance = ProviderInstance.from_dict(entry)
        if not _is_valid_id(instance.instance_id) or instance.instance_id in used_ids:
            instance.instance_id = new_instance_id({"providers": config["providers"]}, used_ids)
        used_ids.add(instance.instance_id)
        _drop_secret_settings(instance)
        config["providers"].append(instance.to_dict())
    return config


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read config.json. Never raises: returns defaults on any problem."""
    try:
        target = Path(path) if path is not None else config_path()
        text = target.read_text(encoding="utf-8")
        if not text.strip():
            return default_config()
        return _normalise(json.loads(text))
    except Exception:  # noqa: BLE001 - a broken file must not stop the widget
        return default_config()


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    """Write config.json safely: write a temp file, then swap it in.

    A config that a newer version of the app wrote is never overwritten: its
    shape is not known here, so the file is left alone and the caller is told.
    """
    target = Path(path) if path is not None else config_path()
    if _file_version(target) > CONFIG_VERSION:
        raise ValueError(
            "This configuration was written by a newer version of the app, so it was not changed."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _normalise(config)
    tmp_path = target.with_name(target.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def list_instances(config: dict[str, Any]) -> list[ProviderInstance]:
    """Return every provider instance in the config, in display order."""
    providers = config.get("providers") if isinstance(config, dict) else None
    if not isinstance(providers, list):
        return []
    return [
        ProviderInstance.from_dict(entry)
        for entry in providers
        if isinstance(entry, dict) and entry.get("kind")
    ]


def new_instance_id(config: dict[str, Any], also_taken: set[str] | None = None) -> str:
    """Return a fresh 8-hex-character id not used in ``config``."""
    taken = {inst.instance_id for inst in list_instances(config)}
    if also_taken:
        taken |= also_taken
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in taken:
            return candidate


def _secret_keys_for(kind: str) -> set[str]:
    """Return the settings keys that are secrets for ``kind``."""
    keys = set(_ALWAYS_SECRET_KEYS)
    try:
        # Imported here to avoid a circular import (registry -> providers ->
        # base -> provider_config).
        from providers import registry

        kind_obj = registry.get_kind(kind)
    except Exception:  # noqa: BLE001
        kind_obj = None
    if kind_obj is not None:
        keys |= {spec.key for spec in kind_obj.fields if spec.kind == "secret"}
    return keys


def _check_no_secrets(instance: ProviderInstance) -> None:
    """Raise ValueError when ``instance.settings`` holds a secret field."""
    bad = sorted(set(instance.settings) & _secret_keys_for(instance.kind))
    if bad:
        raise ValueError(
            "Secret settings must be saved with secrets_store, not in config.json: "
            + ", ".join(bad)
        )


def _drop_secret_settings(instance: ProviderInstance) -> None:
    """Remove every secret setting from an instance that came from disk.

    A config.json that already holds a key (hand-edited, copied from another
    tool or half-written) must not keep it: the key belongs in the key store,
    and a file that holds one is a file that leaks one.
    """
    secret_keys = _secret_keys_for(instance.kind)
    for key in [key for key in instance.settings if key in secret_keys]:
        instance.settings.pop(key)


def add_instance(config: dict[str, Any], instance: ProviderInstance) -> None:
    """Append ``instance`` to ``config`` (in place)."""
    _check_no_secrets(instance)
    providers = config.setdefault("providers", [])
    if any(isinstance(p, dict) and p.get("instance_id") == instance.instance_id for p in providers):
        raise ValueError(f"A provider with id {instance.instance_id} already exists.")
    providers.append(instance.to_dict())


def update_instance(config: dict[str, Any], instance: ProviderInstance) -> None:
    """Replace the entry with the same ``instance_id`` (in place)."""
    _check_no_secrets(instance)
    providers = config.setdefault("providers", [])
    for index, entry in enumerate(providers):
        if isinstance(entry, dict) and entry.get("instance_id") == instance.instance_id:
            providers[index] = instance.to_dict()
            return
    raise KeyError(f"No provider with id {instance.instance_id}.")


def remove_instance(config: dict[str, Any], instance_id: str) -> None:
    """Remove the instance from ``config`` and delete its saved key."""
    providers = config.get("providers")
    if isinstance(providers, list):
        config["providers"] = [
            p for p in providers
            if not (isinstance(p, dict) and p.get("instance_id") == instance_id)
        ]
    secrets_store.delete_secret(instance_id)


def set_enabled(config: dict[str, Any], instance_id: str, enabled: bool) -> None:
    """Turn one instance on or off (in place)."""
    for entry in config.get("providers") or []:
        if isinstance(entry, dict) and entry.get("instance_id") == instance_id:
            entry["enabled"] = bool(enabled)
            return
    raise KeyError(f"No provider with id {instance_id}.")
