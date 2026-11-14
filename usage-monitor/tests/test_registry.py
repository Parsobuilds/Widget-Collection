"""Tests for providers.registry: catalogue, fetch/test/collect entry points."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FAKE_KEY, FakeHttp, KeyringMixin, TempProfile, http_error, instance  # noqa: E402

import provider_config  # noqa: E402
import secrets_store  # noqa: E402
from cache import UsageCache  # noqa: E402
from providers import registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.manual_budget import ManualBudgetKind  # noqa: E402


class CatalogueTests(unittest.TestCase):
    def test_all_kinds_order_and_ids(self) -> None:
        kinds = [k.kind for k in registry.all_kinds()]
        self.assertEqual(kinds[:3], ["claude_code", "codex", "antigravity"])
        self.assertEqual(len(kinds), len(set(kinds)))
        for required in ("nanogpt", "google_ai_studio_t3", "custom_json", "manual_budget", "modal_budget"):
            self.assertIn(required, kinds)

    def test_every_kind_is_described(self) -> None:
        for kind in registry.all_kinds():
            self.assertTrue(kind.display_name, kind.kind)
            self.assertTrue(kind.description, kind.kind)
            self.assertIn(kind.stability, {"official", "unofficial", "local", "manual", "user-defined"})
            if kind.stability == "unofficial":
                self.assertTrue(kind.stability_note, kind.kind)
            self.assertGreater(kind.min_poll_seconds, 0)
            self.assertLessEqual(len([f for f in kind.fields if f.kind == "secret"]), 1)
            for spec in kind.fields:
                self.assertIn(spec.kind, {"text", "secret", "url", "number", "path", "choice"})
            self.assertNotIn("api_key", kind.default_settings())

    def test_catalogue_hides_unavailable_kinds(self) -> None:
        with TempProfile():
            shown = [k.kind for k in registry.catalogue()]
            self.assertNotIn("google_ai_studio_t3", shown)  # no T3 database in the fake profile
            self.assertNotIn("antigravity", shown)  # no T3 folder, so no token file can exist
            self.assertIn("claude_code", shown)
            self.assertIn("custom_json", shown)

    def test_catalogue_shows_t3_kinds_when_database_exists(self) -> None:
        with TempProfile() as profile:
            db = profile.home / ".t3" / "userdata" / "statev2.sqlite"
            db.parent.mkdir(parents=True)
            db.write_bytes(b"")
            shown = [k.kind for k in registry.catalogue()]
            self.assertIn("google_ai_studio_t3", shown)
            self.assertIn("antigravity", shown)

    def test_broken_is_available_hides_kind(self) -> None:
        with mock.patch.object(ManualBudgetKind, "is_available", side_effect=RuntimeError("boom")):
            self.assertNotIn("manual_budget", [k.kind for k in registry.catalogue()])

    def test_get_kind(self) -> None:
        self.assertEqual(registry.get_kind("codex").display_name, "Codex")
        self.assertIsNone(registry.get_kind("nope"))


class FetchInstanceTests(KeyringMixin, unittest.TestCase):
    def test_unknown_kind_gives_error_row(self) -> None:
        rows = registry.fetch_instance(instance("nope", "Mystery"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "error")
        self.assertEqual(rows[0].provider_id, "abcd1234")
        self.assertEqual(rows[0].provider_label, "MYSTERY")
        self.assertIn("Unknown provider type", rows[0].reason)

    def test_unexpected_exception_never_raises(self) -> None:
        with mock.patch.object(ManualBudgetKind, "fetch", side_effect=ZeroDivisionError("secret detail")):
            rows = registry.fetch_instance(instance("manual_budget"))
        self.assertEqual(rows[0].status, "error")
        self.assertEqual(rows[0].model_label, "Could not read usage")
        self.assertIn("ZeroDivisionError", rows[0].reason)
        self.assertNotIn("secret detail", rows[0].reason)

    def test_provider_error_is_redacted(self) -> None:
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        error = ProviderError(ProviderError.AUTH, f"bad key {FAKE_KEY}")
        with mock.patch("providers.nanogpt.NanoGPTKind.fetch", side_effect=error):
            rows = registry.fetch_instance(instance("nanogpt"))
        self.assertEqual(rows[0].status, "error")
        self.assertNotIn(FAKE_KEY, rows[0].reason)
        self.assertIn("***", rows[0].reason)

    def test_missing_required_secret(self) -> None:
        rows = registry.fetch_instance(instance("nanogpt"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("No API key saved", rows[0].reason)

    def test_reads_stored_secret_and_restamps(self) -> None:
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        with TempProfile(), FakeHttp(samples.NANOGPT) as http:
            rows = registry.fetch_instance(instance("nanogpt", "Nano"))
        self.assertEqual(http.header(0, "x-api-key"), FAKE_KEY)
        self.assertEqual(rows[0].provider_id, "abcd1234")
        self.assertEqual(rows[0].provider_label, "NANO")
        self.assertEqual(rows[0].provider_kind, "nanogpt")
        for row in rows:
            self.assertNotIn(FAKE_KEY, repr(row))

    def test_empty_rows_become_error_row(self) -> None:
        with mock.patch.object(ManualBudgetKind, "fetch", return_value=[]):
            rows = registry.fetch_instance(instance("manual_budget"))
        self.assertEqual(rows[0].status, "error")

    def test_unavailable_kind_gives_error_row(self) -> None:
        with TempProfile():
            rows = registry.fetch_instance(instance("google_ai_studio_t3"))
        self.assertEqual(rows[0].status, "error")
        self.assertIn("not available", rows[0].reason)


class TestInstanceTests(KeyringMixin, unittest.TestCase):
    def test_secret_override_is_used_instead_of_stored(self) -> None:
        secrets_store.set_secret("abcd1234", "stored-fake-value")
        with TempProfile(), FakeHttp(samples.NANOGPT) as http:
            result = registry.test_instance(instance("nanogpt"), secret_override=FAKE_KEY)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(http.header(0, "x-api-key"), FAKE_KEY)
        self.assertEqual(result.message, "Read 1 reading: Account balance")
        self.assertEqual(len(result.rows), 1)

    def test_failure_message(self) -> None:
        with TempProfile(), FakeHttp(http_error("https://nano-gpt.com/api/check-balance", 401)):
            result = registry.test_instance(instance("nanogpt"), secret_override=FAKE_KEY)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "The service rejected the key (HTTP 401).")
        self.assertNotIn(FAKE_KEY, result.message)

    def test_no_secret_needed(self) -> None:
        result = registry.test_instance(instance("manual_budget", settings={"monthly_credit": "30", "spent": "3"}))
        self.assertTrue(result.ok)
        self.assertIn("Monthly budget", result.message)


class CollectEnabledTests(KeyringMixin, unittest.TestCase):
    def test_no_providers_returns_empty(self) -> None:
        cache = mock.Mock()
        rows, stale, stamp = registry.collect_enabled(provider_config.default_config(), cache=cache)
        self.assertEqual(rows, [])
        self.assertFalse(stale)
        self.assertRegex(stamp, r"^\d\d:\d\d$")
        cache.fetch_with_fallback.assert_not_called()

    def test_collects_enabled_in_order_and_skips_disabled(self) -> None:
        with TempProfile() as profile:
            config = provider_config.default_config()
            provider_config.add_instance(config, instance("manual_budget", "First", {"monthly_credit": "10", "spent": "1"}, "first001"))
            off = instance("manual_budget", "Off", {"spent": "1"}, "off00001")
            off.enabled = False
            provider_config.add_instance(config, off)
            provider_config.add_instance(config, instance("nope", "Unknown", {}, "unk00001"))
            provider_config.add_instance(config, instance("modal_budget", "Modal", {"monthly_credit": "30", "spent": "6"}, "modal001"))
            cache = UsageCache(profile.root / "cache.json")
            rows, stale, _ = registry.collect_enabled(config, cache=cache)
            self.assertEqual([r.provider_id for r in rows], ["first001", "modal001"])
            self.assertEqual([r.provider_label for r in rows], ["FIRST", "MODAL"])
            self.assertFalse(stale)
            self.assertIsNotNone(cache.get("first001"))

    def test_min_poll_uses_cache_unless_forced(self) -> None:
        with TempProfile() as profile:
            config = provider_config.default_config()
            provider_config.add_instance(config, instance("manual_budget", "B", {"monthly_credit": "10", "spent": "1"}))
            cache = UsageCache(profile.root / "cache.json")
            registry.collect_enabled(config, cache=cache)
            with mock.patch.object(registry, "fetch_instance", wraps=registry.fetch_instance) as spy:
                registry.collect_enabled(config, cache=cache)
                self.assertEqual(spy.call_count, 0)
                registry.collect_enabled(config, cache=cache, force=True)
                self.assertEqual(spy.call_count, 1)

    def test_label_change_is_shown_on_cached_rows(self) -> None:
        with TempProfile() as profile:
            config = provider_config.default_config()
            provider_config.add_instance(config, instance("manual_budget", "Old", {"monthly_credit": "10", "spent": "1"}))
            cache = UsageCache(profile.root / "cache.json")
            registry.collect_enabled(config, cache=cache)
            provider_config.update_instance(config, instance("manual_budget", "New", {"monthly_credit": "10", "spent": "1"}))
            rows, _, _ = registry.collect_enabled(config, cache=cache)
            self.assertEqual(rows[0].provider_label, "NEW")

    def test_failure_after_success_is_stale(self) -> None:
        with TempProfile() as profile:
            config = provider_config.default_config()
            provider_config.add_instance(config, instance("manual_budget", "B", {"monthly_credit": "10", "spent": "1"}))
            cache = UsageCache(profile.root / "cache.json")
            registry.collect_enabled(config, cache=cache)
            with mock.patch.object(ManualBudgetKind, "fetch", side_effect=ProviderError(ProviderError.NETWORK, "down")):
                rows, stale, _ = registry.collect_enabled(config, cache=cache, force=True)
            self.assertTrue(stale)
            self.assertEqual(rows[0].status, "stale")
            self.assertEqual(rows[0].percent_used, 10.0)

    def test_failure_without_cache_shows_error_row(self) -> None:
        with TempProfile() as profile:
            config = provider_config.default_config()
            provider_config.add_instance(config, instance("nanogpt", "Nano"))
            rows, stale, _ = registry.collect_enabled(config, cache=UsageCache(profile.root / "c.json"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "error")
            self.assertFalse(stale)
            self.assertFalse((profile.root / "c.json").exists() and "Nano" in (profile.root / "c.json").read_text())


if __name__ == "__main__":
    unittest.main()
