"""Reply samples used by the parser tests.

Each one is the same JSON as the matching ``<!-- sample:NAME -->`` block in
SOURCES.md (``test_sources_samples`` checks they stay identical). All numbers,
ids and names are fake.
"""

from __future__ import annotations

CLAUDE_CODE = {
    "five_hour": {
        "utilization": 12.0,
        "resets_at": "2030-01-01T05:00:00.000000+00:00",
        "limit_dollars": None,
        "used_dollars": None,
        "remaining_dollars": None,
        "locked_reason": None,
    },
    "seven_day": {
        "utilization": 34.0,
        "resets_at": "2030-01-06T17:00:00.000000+00:00",
        "limit_dollars": None,
        "used_dollars": None,
        "remaining_dollars": None,
        "locked_reason": None,
    },
    "seven_day_opus": None,
    "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None, "utilization": None},
    "limits": [
        {
            "kind": "session",
            "group": "session",
            "percent": 12,
            "severity": "normal",
            "resets_at": "2030-01-01T05:00:00.000000+00:00",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_all",
            "group": "weekly",
            "percent": 34,
            "severity": "normal",
            "resets_at": "2030-01-06T17:00:00.000000+00:00",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": 5,
            "severity": "normal",
            "resets_at": "2030-01-06T17:00:00.000000+00:00",
            "scope": {"model": {"id": None, "display_name": "Opus"}, "surface": None},
            "is_active": True,
        },
    ],
}

# An older reply shape that named each limit with ``id`` and ``title``.
CLAUDE_CODE_TITLED = {
    "five_hour": {"utilization": 12.0, "resets_at": "2030-01-01T05:00:00+00:00"},
    "seven_day": {"utilization": 34.0, "resets_at": "2030-01-06T17:00:00+00:00"},
    "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None, "utilization": None},
    "limits": [
        {
            "id": "session",
            "category": "session",
            "title": "Current session",
            "scope": None,
            "percent": 12,
            "resets_at": "2030-01-01T05:00:00Z",
        },
        {
            "id": "weekly_all",
            "category": "weekly_all",
            "title": "All models",
            "scope": "All models",
            "percent": 34,
            "resets_at": "2030-01-06T17:00:00Z",
        },
        {
            "id": "weekly_scoped_example",
            "category": "weekly_scoped",
            "title": "Opus only",
            "scope": "Opus",
            "percent": 5,
            "resets_at": "2030-01-06T17:00:00Z",
        },
    ],
}

CODEX = {
    "user_id": "user-REDACTED",
    "account_id": "account-REDACTED",
    "email": "someone@example.com",
    "plan_type": "plus",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 42,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 3600,
            "reset_at": 1893474000,
        },
        "secondary_window": {
            "used_percent": 17,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 86400,
            "reset_at": 1893556800,
        },
    },
    "code_review_rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 3,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 604800,
            "reset_at": 1894075200,
        },
        "secondary_window": None,
    },
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
}

ANTIGRAVITY = {
    "models": {
        "example-flash-tiered": {
            "displayName": "Gemini Flash",
            "quotaInfo": {"remainingFraction": 0.75, "resetTime": "2030-01-01T04:00:00Z"},
        },
        "example-sonnet-high": {
            "displayName": "Claude Sonnet (Thinking)",
            "quotaInfo": {"remainingFraction": 0.5, "resetTime": "2030-01-01T05:00:00Z"},
        },
        "example-used-up": {
            "displayName": "Example Model (used up)",
            "quotaInfo": {"resetTime": "2030-01-01T05:00:00Z"},
        },
        "example-internal": {"displayName": "Internal helper"},
    },
    "defaultAgentModelId": "example-flash-tiered",
}

NANOGPT = {
    "usd_balance": "12.34567890",
    "nano_balance": "0.5",
    "nanoDepositAddress": "nano_1exampledepositaddress",
}

OPENROUTER = {
    "data": {
        "label": "my-key",
        "limit": 40,
        "limit_reset": "monthly",
        "limit_remaining": 30,
        "include_byok_in_limit": False,
        "usage": 10,
        "usage_daily": 1,
        "usage_weekly": 4,
        "usage_monthly": 10,
        "byok_usage": 0,
        "byok_usage_daily": 0,
        "byok_usage_weekly": 0,
        "byok_usage_monthly": 0,
        "is_free_tier": False,
        "free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38},
    }
}

DEEPSEEK = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "CNY",
            "total_balance": "42.00",
            "granted_balance": "2.00",
            "topped_up_balance": "40.00",
        }
    ],
}

CUSTOM_JSON = {"data": {"used": 12.5, "limit": 50, "balance": 37.5, "items": [{"used": 3}]}}

ALL = {
    "claude_code": CLAUDE_CODE,
    "codex": CODEX,
    "antigravity": ANTIGRAVITY,
    "nanogpt": NANOGPT,
    "openrouter": OPENROUTER,
    "deepseek": DEEPSEEK,
    "custom_json": CUSTOM_JSON,
}
