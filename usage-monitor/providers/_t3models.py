"""The models T3 Code offers for a provider, read from T3 Code's own files.

T3 Code keeps two files that together give its model picker:

* ``~/.t3/userdata/model-manifest.json``: the built-in models of each driver
  (Claude, Codex, Antigravity), with display names and a "current"/"legacy"
  status.
* ``~/.t3/userdata/settings.json``: each provider instance (for example
  ``claudeAgent_minimax``) with its driver and any custom models, plus the
  models the user hid in T3 Code and the order they picked.

``models_for()`` combines them the same way T3 Code's picker does, so the
widget can list "what can I use on this provider". Nothing here goes online.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from providers.base import home_dir

if TYPE_CHECKING:
    from provider_config import ProviderInstance

# Widget provider kind -> the T3 Code instance it normally matches.
DEFAULT_T3_INSTANCE = {
    "claude_code": "claudeAgent",
    "codex": "codex",
    "antigravity": "antigravity",
    "google_ai_studio_t3": "google_ai_studio",
    "nanogpt": "claudeAgent_nanogpt",
    "nanogpt_sub": "claudeAgent_nanogpt_sub",
}


def t3_dir() -> Path:
    return home_dir() / ".t3" / "userdata"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def find_t3_instance(instance: "ProviderInstance", t3_instances: dict[str, Any]) -> str | None:
    """Which T3 Code provider instance this widget provider is.

    Tried in order: the provider's own ``t3_provider_instance`` setting, the
    usual instance for its kind, then a T3 instance whose name matches the
    widget label (so a "MiniMax" card finds T3's "MiniMax" provider).
    """
    explicit = str(instance.settings.get("t3_provider_instance") or "").strip()
    if explicit in t3_instances:
        return explicit
    default = DEFAULT_T3_INSTANCE.get(instance.kind)
    if default in t3_instances:
        return default
    label = instance.label.strip().lower()
    for key, value in t3_instances.items():
        if isinstance(value, dict) and str(value.get("displayName") or "").strip().lower() == label:
            return key
    # Built-in drivers can be in use without an entry in settings.json.
    return default if default in {"claudeAgent", "codex", "antigravity"} else None


def models_for(instance: "ProviderInstance", root: Path | None = None) -> list[tuple[str, str]]:
    """``(model id, display name)`` pairs T3 Code offers for ``instance``, picker order.

    Returns an empty list when T3 Code is not installed or has no match.
    """
    root = root or t3_dir()
    settings = _read_json(root / "settings.json")
    t3_instances = settings.get("providerInstances")
    t3_instances = t3_instances if isinstance(t3_instances, dict) else {}
    key = find_t3_instance(instance, t3_instances)
    if key is None:
        return []
    entry = t3_instances.get(key) if isinstance(t3_instances.get(key), dict) else {}
    driver = str(entry.get("driver") or key)

    models: dict[str, str] = {}
    manifest = _read_json(root / "model-manifest.json").get("manifest") or {}
    driver_info = (manifest.get("providers") or {}).get(driver) or {}
    for model in driver_info.get("models") or []:
        if isinstance(model, dict) and model.get("slug") and model.get("status", "current") == "current":
            models[str(model["slug"])] = str(model.get("name") or model["slug"])
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    for model in config.get("customModels") or entry.get("customModels") or []:
        if isinstance(model, dict) and model.get("slug"):
            models[str(model["slug"])] = str(model.get("name") or model["slug"])

    prefs = (settings.get("providerModelPreferences") or {}).get(key) or {}
    for slug in prefs.get("hiddenModels") or []:
        models.pop(str(slug), None)
    order = [str(slug) for slug in prefs.get("modelOrder") or [] if str(slug) in models]
    ordered = order + [slug for slug in models if slug not in order]
    return [(slug, models[slug]) for slug in ordered]
