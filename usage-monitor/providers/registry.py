"""The catalogue of provider kinds and the calls the widget makes.

* ``catalogue()`` / ``all_kinds()`` / ``get_kind()``: what can be added.
* ``fetch_instance()``: one reading for one configured provider. Never raises;
  a failure becomes a single row with ``status="error"``.
* ``test_instance()``: the Settings window's Test button.
* ``collect_enabled()``: every enabled provider, through the cache.

Secrets are read from ``secrets_store`` here and handed to ``kind.fetch()``
in memory only. Any error text is passed through ``secrets_store.redact``
before it can reach a row.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any

import secrets_store
from models import UsageRow
from providers.antigravity import AntigravityKind
from providers.base import ProviderError, ProviderKind, TestResult, error_row
from providers.claude_code import ClaudeCodeKind
from providers.codex import CodexKind
from providers.custom_json import CustomJsonKind
from providers.deepseek import DeepSeekKind
from providers.google_ai_studio_t3 import GoogleAIStudioT3Kind
from providers.manual_budget import ManualBudgetKind, ModalBudgetKind
from providers.nanogpt import NanoGPTKind
from providers.openrouter import OpenRouterKind

if TYPE_CHECKING:
    from cache import UsageCache
    from provider_config import ProviderInstance

# Display order of the Add provider list.
_KINDS: list[ProviderKind] = [
    ClaudeCodeKind(),
    CodexKind(),
    AntigravityKind(),
    NanoGPTKind(),
    OpenRouterKind(),
    DeepSeekKind(),
    GoogleAIStudioT3Kind(),
    CustomJsonKind(),
    ManualBudgetKind(),
    ModalBudgetKind(),
]

MIN_STALE_AFTER_SECONDS = 900
MAX_PARALLEL_FETCHES = 6


def all_kinds() -> list[ProviderKind]:
    """Every kind, including ones not available on this computer."""
    return list(_KINDS)


def _available(kind: ProviderKind) -> bool:
    try:
        return bool(kind.is_available())
    except Exception:  # noqa: BLE001 - a broken check just hides the kind
        return False


def catalogue() -> list[ProviderKind]:
    """Kinds that can be added on this computer, in display order."""
    return [kind for kind in _KINDS if _available(kind)]


def get_kind(kind: str) -> ProviderKind | None:
    """Look up a kind by its id, or None."""
    return next((k for k in _KINDS if k.kind == kind), None)


def _restamp(rows: list[UsageRow], instance: "ProviderInstance") -> list[UsageRow]:
    """Make every row carry this instance's id, label and kind."""
    label = (instance.label or instance.kind).upper()
    for row in rows:
        row.provider_id = instance.instance_id
        row.provider_label = label
        row.provider_kind = instance.kind
    return rows


def _fetch(instance: "ProviderInstance", secret: str | None, use_stored: bool) -> list[UsageRow]:
    """Shared body of fetch_instance/test_instance. Never raises."""
    kind = get_kind(instance.kind)
    if kind is None:
        return [error_row(instance, f"Unknown provider type '{instance.kind}'. Remove it in Settings.")]
    if not _available(kind):
        return [error_row(instance, f"{kind.display_name} is not available on this computer.")]

    spec = kind.secret_field()
    if spec is not None and use_stored and not secret:
        try:
            secret = secrets_store.get_secret(instance.instance_id)
        except secrets_store.SecretStoreError as exc:
            return [error_row(instance, secrets_store.redact(str(exc)))]
    if spec is not None and spec.required and not secret:
        return [error_row(instance, f"No {spec.label} saved. Open Settings and add it.")]

    try:
        rows = kind.fetch(instance, secret or None)
    except ProviderError as exc:
        return [error_row(instance, secrets_store.redact(exc.message, secret))]
    except Exception as exc:  # noqa: BLE001 - never let one provider break the widget
        return [error_row(instance, f"Unexpected problem while reading usage ({type(exc).__name__}).")]

    if not rows:
        return [error_row(instance, "The service replied, but there were no readings in it.")]
    if secret:
        for row in rows:  # belt and braces: no row text may contain the key
            row.reason = secrets_store.redact(row.reason, secret)
            row.secondary_label = secrets_store.redact(row.secondary_label, secret)
            row.model_label = secrets_store.redact(row.model_label, secret)
    return _restamp(list(rows), instance)


def fetch_instance(instance: "ProviderInstance") -> list[UsageRow]:
    """One live reading for ``instance``, using its stored secret. Never raises."""
    return _fetch(instance, None, use_stored=True)


def test_instance(instance: "ProviderInstance", secret_override: str | None = None) -> TestResult:
    """One live call for the Test button. Bypasses the cache. Never raises."""
    try:
        rows = _fetch(instance, secret_override, use_stored=not secret_override)
    except Exception as exc:  # noqa: BLE001 - _fetch should not raise, but be safe
        return TestResult(ok=False, message=f"Test failed ({type(exc).__name__}).")

    errors = [row for row in rows if row.status == "error"]
    if errors:
        return TestResult(ok=False, message=errors[0].reason or "The test failed.", rows=rows)
    labels = ", ".join(row.model_label for row in rows[:4])
    if len(rows) > 4:
        labels += ", ..."
    noun = "reading" if len(rows) == 1 else "readings"
    return TestResult(ok=True, message=f"Read {len(rows)} {noun}: {labels}", rows=rows)


def collect_enabled(
    config: dict[str, Any],
    cache: "UsageCache | None" = None,
    force: bool = False,
) -> tuple[list[UsageRow], bool, str]:
    """Read every enabled, available provider through the cache.

    Returns ``(rows, any_stale, "HH:MM")``. Rows keep the order of the
    instances in the config. Pass ``force=True`` after a settings change to
    skip each kind's minimum poll interval.
    """
    import provider_config  # local import: provider_config imports this module lazily too

    stamp = datetime.now().strftime("%H:%M")
    jobs: list[tuple["ProviderInstance", ProviderKind]] = []
    for instance in provider_config.list_instances(config):
        if not instance.enabled:
            continue
        kind = get_kind(instance.kind)
        if kind is None or not _available(kind):
            continue
        jobs.append((instance, kind))
    if not jobs:
        return [], False, stamp

    if cache is None:
        from cache import UsageCache

        cache = UsageCache()

    def _one(job: tuple["ProviderInstance", ProviderKind]):
        instance, kind = job
        min_poll = 0 if force else max(0, int(kind.min_poll_seconds))
        snapshot = cache.fetch_with_fallback(
            instance.instance_id,
            lambda: fetch_instance(instance),
            min_poll_interval_seconds=min_poll,
            stale_after_seconds=max(MIN_STALE_AFTER_SECONDS, 2 * min_poll),
        )
        rows = _restamp(list(snapshot.rows), instance)  # label may have changed since caching
        if not rows:
            rows = [error_row(instance, snapshot.error or "No data available.")]
        return rows, snapshot.is_stale and any(r.status != "error" for r in rows)

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_FETCHES, len(jobs))) as pool:
        results = list(pool.map(_one, jobs))  # map keeps the config order

    rows: list[UsageRow] = []
    any_stale = False
    for job_rows, stale in results:
        rows.extend(job_rows)
        any_stale = any_stale or stale
    return rows, any_stale, stamp
