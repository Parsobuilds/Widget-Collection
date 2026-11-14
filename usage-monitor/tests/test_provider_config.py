"""Tests for provider_config: defaults, load/save, instances, secret rejection."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import FAKE_KEY, KeyringMixin, TempProfile, instance  # noqa: E402

import provider_config  # noqa: E402
import secrets_store  # noqa: E402
from provider_config import ProviderInstance  # noqa: E402


class DefaultsAndPathsTests(unittest.TestCase):
    def test_default_config_has_no_providers(self) -> None:
        config = provider_config.default_config()
        self.assertEqual(config["providers"], [])
        self.assertEqual(config["version"], provider_config.CONFIG_VERSION)
        self.assertEqual(config["poll_minutes"], 5)

    def test_data_dir_is_under_appdata_and_created(self) -> None:
        with TempProfile() as profile:
            folder = provider_config.data_dir()
            self.assertEqual(folder, profile.appdata / "DesktopWidgets" / "UsageMonitor")
            self.assertTrue(folder.is_dir())
            self.assertEqual(provider_config.config_path(), folder / "config.json")


class LoadSaveTests(unittest.TestCase):
    def test_missing_file_gives_defaults(self) -> None:
        with TempProfile():
            self.assertEqual(provider_config.load_config(), provider_config.default_config())

    def test_corrupt_file_gives_defaults(self) -> None:
        with TempProfile():
            path = provider_config.config_path()
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(provider_config.load_config(), provider_config.default_config())
            path.write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(provider_config.load_config(), provider_config.default_config())

    def test_round_trip(self) -> None:
        with TempProfile():
            config = provider_config.default_config()
            config["poll_minutes"] = 15
            provider_config.add_instance(
                config, instance("custom_json", "My API", {"url": "https://example.com/u", "used_path": "a"})
            )
            provider_config.add_instance(config, instance("manual_budget", "Budget", {"spent": "3"}, "budget01"))
            provider_config.save_config(config)
            self.assertFalse(provider_config.config_path().with_name("config.json.tmp").exists())
            loaded = provider_config.load_config()
            self.assertEqual(loaded, config)
            items = provider_config.list_instances(loaded)
            self.assertEqual([i.label for i in items], ["My API", "Budget"])
            self.assertEqual(items[0].settings["url"], "https://example.com/u")

    def test_save_to_explicit_path(self) -> None:
        with TempProfile() as profile:
            path = profile.root / "other" / "cfg.json"
            config = provider_config.default_config()
            provider_config.save_config(config, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["providers"], [])
            self.assertEqual(provider_config.load_config(path), config)

    def test_load_normalises_junk(self) -> None:
        with TempProfile():
            raw = {
                "version": 1,
                "poll_minutes": 0,
                "providers": [
                    {"instance_id": "bad id!", "kind": "manual_budget", "label": "A"},
                    {"instance_id": "same", "kind": "manual_budget", "label": "B"},
                    {"instance_id": "same", "kind": "manual_budget", "label": "C"},
                    {"label": "no kind"},
                    "not a dict",
                ],
            }
            provider_config.config_path().write_text(json.dumps(raw), encoding="utf-8")
            config = provider_config.load_config()
            self.assertEqual(config["poll_minutes"], 1)
            ids = [i.instance_id for i in provider_config.list_instances(config)]
            self.assertEqual(len(ids), 3)
            self.assertEqual(len(set(ids)), 3)
            self.assertIn("same", ids)
            self.assertNotIn("bad id!", ids)


class InstanceEditTests(KeyringMixin, unittest.TestCase):
    def test_new_instance_id_is_unique(self) -> None:
        config = provider_config.default_config()
        seen = set()
        for _ in range(20):
            new_id = provider_config.new_instance_id(config)
            self.assertRegex(new_id, r"^[0-9a-f]{8}$")
            provider_config.add_instance(config, instance("manual_budget", instance_id=new_id))
            seen.add(new_id)
        self.assertEqual(len(seen), 20)

    def test_add_rejects_secret_settings(self) -> None:
        config = provider_config.default_config()
        for key in ("api_key", "token", "password"):
            with self.assertRaises(ValueError):
                provider_config.add_instance(config, instance("nanogpt", settings={key: FAKE_KEY}))
        self.assertEqual(config["providers"], [])

    def test_update_rejects_secret_settings(self) -> None:
        config = provider_config.default_config()
        item = instance("custom_json", settings={"url": "https://example.com"})
        provider_config.add_instance(config, item)
        item.settings["api_key"] = FAKE_KEY
        with self.assertRaises(ValueError) as ctx:
            provider_config.update_instance(config, item)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertNotIn(FAKE_KEY, json.dumps(config))

    def test_add_duplicate_id_and_update_missing(self) -> None:
        config = provider_config.default_config()
        provider_config.add_instance(config, instance("manual_budget"))
        with self.assertRaises(ValueError):
            provider_config.add_instance(config, instance("manual_budget"))
        with self.assertRaises(KeyError):
            provider_config.update_instance(config, instance("manual_budget", instance_id="zzzz9999"))

    def test_update_replaces_by_id(self) -> None:
        config = provider_config.default_config()
        provider_config.add_instance(config, instance("manual_budget", "Old"))
        provider_config.update_instance(config, instance("manual_budget", "New", {"spent": "1"}))
        items = provider_config.list_instances(config)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].label, "New")

    def test_remove_deletes_secret(self) -> None:
        config = provider_config.default_config()
        provider_config.add_instance(config, instance("nanogpt"))
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        provider_config.remove_instance(config, "abcd1234")
        self.assertEqual(config["providers"], [])
        self.assertIsNone(secrets_store.get_secret("abcd1234"))
        provider_config.remove_instance(config, "abcd1234")  # second time: no error

    def test_set_enabled(self) -> None:
        config = provider_config.default_config()
        provider_config.add_instance(config, instance("manual_budget"))
        provider_config.set_enabled(config, "abcd1234", False)
        self.assertFalse(provider_config.list_instances(config)[0].enabled)
        with self.assertRaises(KeyError):
            provider_config.set_enabled(config, "missing1", True)

    def test_instance_dict_round_trip(self) -> None:
        item = ProviderInstance("abcd1234", "codex", "Work", False, {"auth_path": ""})
        self.assertEqual(ProviderInstance.from_dict(item.to_dict()), item)
        junk = ProviderInstance.from_dict({"settings": "nope"})
        self.assertEqual(junk.settings, {})


if __name__ == "__main__":
    unittest.main()
