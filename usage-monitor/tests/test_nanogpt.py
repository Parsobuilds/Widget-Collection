"""Tests for the NanoGPT balance provider and the optional T3 run count."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from fakes import FAKE_KEY, FakeHttp, FakeResponse, KeyringMixin, TempProfile, instance  # noqa: E402

import secrets_store  # noqa: E402
from providers import _t3db, registry  # noqa: E402
from providers.base import ProviderError  # noqa: E402
from providers.nanogpt import NanoGPTKind, parse_payload  # noqa: E402

RUNS_SCHEMA = """
CREATE TABLE orchestration_v2_projection_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    provider TEXT NOT NULL,
    provider_thread_id TEXT,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    payload_json TEXT NOT NULL,
    provider_instance_id TEXT
)
"""


def make_db(path, runs: list[tuple[str, str, str]]) -> None:
    """runs: (provider_instance_id, model, requested_at)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(RUNS_SCHEMA)
    for index, (provider, model, when) in enumerate(runs):
        payload = json.dumps({"providerInstanceId": provider, "modelSelection": {"instanceId": provider, "model": model}})
        conn.execute(
            "INSERT INTO orchestration_v2_projection_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"run{index}", "thread1", index, "fake", None, "completed", when, when, payload, provider),
        )
    conn.commit()
    conn.close()


class ParseTests(unittest.TestCase):
    def test_sample_balance(self) -> None:
        rows = parse_payload(samples.NANOGPT, instance("nanogpt", "Nano"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.model_label, "Account balance")
        self.assertEqual(row.credit_balance_usd, 12.35)
        self.assertIsNone(row.percent_used)
        self.assertEqual(row.status, "ok")
        self.assertEqual(row.reason, "$12.35 balance · pay-per-use")

    def test_run_count_in_reason(self) -> None:
        rows = parse_payload(samples.NANOGPT, instance("nanogpt"), run_count=7)
        self.assertEqual(rows[0].reason, "$12.35 balance · pay-per-use · 7 runs in T3 Code")
        rows = parse_payload(samples.NANOGPT, instance("nanogpt"), run_count=0)
        self.assertEqual(rows[0].reason, "$12.35 balance · pay-per-use")

    def test_bad_balance(self) -> None:
        for payload in ({"usd_balance": "lots"}, {}, [], {"usd_balance": None}):
            with self.assertRaises(ProviderError):
                parse_payload(payload, instance("nanogpt"))


class FetchTests(KeyringMixin, unittest.TestCase):
    def test_posts_with_api_key_header(self) -> None:
        secrets_store.set_secret("abcd1234", FAKE_KEY)
        with TempProfile(), FakeHttp(samples.NANOGPT) as http:
            rows = registry.fetch_instance(instance("nanogpt"))
        request = http.requests[0]
        self.assertEqual(request.full_url, "https://nano-gpt.com/api/check-balance")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(http.header(0, "x-api-key"), FAKE_KEY)
        self.assertIsNone(http.header(0, "Authorization"))
        self.assertEqual(rows[0].credit_balance_usd, 12.35)

    def test_no_key_is_config_error(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            NanoGPTKind().fetch(instance("nanogpt"), None)
        self.assertEqual(ctx.exception.code, ProviderError.CONFIG)

    def test_network_and_bad_body(self) -> None:
        with TempProfile(), FakeHttp(urllib.error.URLError("offline")):
            rows = registry.test_instance(instance("nanogpt"), secret_override=FAKE_KEY).rows
        self.assertIn("Could not reach nano-gpt.com", rows[0].reason)
        with TempProfile(), FakeHttp(FakeResponse("not json")):
            rows = registry.test_instance(instance("nanogpt"), secret_override=FAKE_KEY).rows
        self.assertEqual(rows[0].reason, "The reply was not JSON.")

    def test_optional_t3_run_count(self) -> None:
        with TempProfile() as profile:
            make_db(profile.home / ".t3" / "userdata" / "statev2.sqlite", [
                ("nanogpt_t3", "model-a", "2030-01-01T01:00:00Z"),
                ("nanogpt_t3", "model-b", "2030-01-01T02:00:00Z"),
                ("other", "model-a", "2030-01-01T02:00:00Z"),
            ])
            with FakeHttp(samples.NANOGPT):
                result = registry.test_instance(
                    instance("nanogpt", settings={"t3_provider_instance": "nanogpt_t3"}), secret_override=FAKE_KEY
                )
        self.assertTrue(result.ok)
        self.assertIn("2 runs in T3 Code", result.rows[0].reason)

    def test_missing_database_is_ignored(self) -> None:
        with TempProfile(), FakeHttp(samples.NANOGPT):
            result = registry.test_instance(
                instance("nanogpt", settings={"t3_provider_instance": "nanogpt_t3"}), secret_override=FAKE_KEY
            )
        self.assertTrue(result.ok)
        self.assertNotIn("runs", result.rows[0].reason)


class T3DatabaseTests(unittest.TestCase):
    def test_counts_by_model_and_since(self) -> None:
        from datetime import datetime, timezone

        with TempProfile() as profile:
            db = profile.root / "state.sqlite"
            make_db(db, [
                ("p1", "model-a", "2030-01-01T01:00:00Z"),
                ("p1", "model-a", "2030-01-01T09:00:00Z"),
                ("p1", "model-b", "2030-01-01T10:00:00Z"),
                ("p2", "model-a", "2030-01-01T10:00:00Z"),
            ])
            self.assertEqual(_t3db.count_runs(db, "p1"), {"model-a": 2, "model-b": 1})
            since = datetime(2030, 1, 1, 8, tzinfo=timezone.utc)
            self.assertEqual(_t3db.count_runs(db, "p1", since=since), {"model-a": 1, "model-b": 1})

    def test_missing_and_corrupt_database(self) -> None:
        with TempProfile() as profile:
            with self.assertRaises(ProviderError) as ctx:
                _t3db.count_runs(profile.root / "missing.sqlite", "p1")
            self.assertEqual(ctx.exception.code, ProviderError.NOT_FOUND)
            bad = profile.root / "bad.sqlite"
            bad.write_bytes(b"this is not sqlite at all" * 10)
            with self.assertRaises(ProviderError):
                _t3db.count_runs(bad, "p1")
            empty = profile.root / "empty.sqlite"
            sqlite3.connect(empty).close()
            with self.assertRaises(ProviderError):
                _t3db.count_runs(empty, "p1")


if __name__ == "__main__":
    unittest.main()
