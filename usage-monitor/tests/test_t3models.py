"""Tests for listing the models T3 Code offers for a provider."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import instance  # noqa: E402

from providers._t3models import models_for  # noqa: E402

MANIFEST = {"manifest": {"providers": {
    "claudeAgent": {"models": [
        {"slug": "claude-big", "name": "Claude Big", "status": "current"},
        {"slug": "claude-small", "name": "Claude Small", "status": "current"},
        {"slug": "claude-old", "name": "Claude Old", "status": "legacy"},
    ]},
    "codex": {"models": [{"slug": "gpt-a", "name": "GPT A", "status": "current"},
                         {"slug": "gpt-b", "name": "GPT B", "status": "current"}]},
}}}
SETTINGS = {
    "providerInstances": {
        "codex": {"driver": "codex"},
        "claudeAgent_minimax": {"driver": "claudeAgent", "displayName": "MiniMax", "config": {
            "customModels": [{"slug": "MiniMax-M3", "name": "MiniMax M3"},
                             {"slug": "MiniMax-Fast", "name": "MiniMax Fast"}]}},
    },
    "providerModelPreferences": {
        "codex": {"hiddenModels": ["gpt-a"]},
        "claudeAgent_minimax": {"hiddenModels": ["claude-big", "claude-small"],
                                "modelOrder": ["MiniMax-Fast"]},
    },
}


class T3ModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "model-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (self.root / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_builtin_driver_without_settings_entry(self) -> None:
        # Claude is not in providerInstances but is still a T3 built-in; legacy models are left out.
        self.assertEqual(models_for(instance("claude_code"), self.root),
                         [("claude-big", "Claude Big"), ("claude-small", "Claude Small")])

    def test_hidden_models_are_left_out(self) -> None:
        self.assertEqual(models_for(instance("codex"), self.root), [("gpt-b", "GPT B")])

    def test_custom_provider_found_by_name_with_order(self) -> None:
        models = models_for(instance("custom_json", "MiniMax"), self.root)
        self.assertEqual(models, [("MiniMax-Fast", "MiniMax Fast"), ("MiniMax-M3", "MiniMax M3")])

    def test_explicit_setting_wins(self) -> None:
        inst = instance("nanogpt", "Other name", settings={"t3_provider_instance": "claudeAgent_minimax"})
        self.assertEqual(len(models_for(inst, self.root)), 2)

    def test_no_match_or_no_t3(self) -> None:
        self.assertEqual(models_for(instance("modal_budget", "Modal"), self.root), [])
        self.assertEqual(models_for(instance("claude_code"), self.root / "missing"), [])


if __name__ == "__main__":
    unittest.main()
