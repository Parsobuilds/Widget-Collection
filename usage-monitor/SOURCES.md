# Usage Monitor: data sources

Where each provider kind gets its numbers, what it sends, what comes back and
how stable that is. Every sample below is scrubbed: the numbers, ids and
names are fake. The test suite parses these exact samples
(`tests/samples.py`, checked by `test_models_and_cache.SourcesSamplesTests`).

Nothing is configured out of the box. You add providers in Settings; each one
you add is an *instance* with its own id, label and settings.

## Ground rules

- **Keys** you type are saved in Windows Credential Manager (service
  `DesktopWidgets.UsageMonitor`, one entry per instance id). They are never
  written to `config.json`, the cache, logs or error messages.
- **Sign-in files** that Claude Code, Codex or T3 Code already keep on your
  computer are read when a reading is taken. The token inside is used in
  memory for that one request and never copied anywhere.
- **Files the widget writes:** `%APPDATA%\DesktopWidgets\UsageMonitor\config.json`
  (your provider list, no secrets) and `cache.json` (the last good rows per
  instance, nothing else).
- **HTTP:** https only (plain http only for `localhost`, `127.0.0.1`, `::1`),
  10-second timeout, replies over 1 MB are ignored, and a redirect to a
  different host is not followed so a key is never sent somewhere else.
- **Errors** become one row named "Could not read usage" with a plain-English
  reason. The reply body of a failed request is never read or shown.

## Stability labels

| label | meaning |
|---|---|
| official | Endpoint and reply are in the provider's public API docs. |
| unofficial | Endpoint used by the provider's own app; not documented, may change or stop working without notice. |
| local | Counted from files on this computer; no network. |
| manual | You type the numbers yourself. |
| user-defined | You describe the endpoint yourself (custom JSON). |

## Summary

| kind | display name | stability | source | min poll |
|---|---|---|---|---|
| `claude_code` | Claude Code | unofficial | Claude Code sign-in file + `GET https://api.anthropic.com/api/oauth/usage` | 10 min |
| `codex` | Codex | unofficial | Codex CLI `auth.json` + `GET https://chatgpt.com/backend-api/wham/usage` | 5 min |
| `antigravity` | Antigravity | unofficial | T3 Code's Antigravity token file + Google Cloud Code `fetchAvailableModels` | 5 min |
| `nanogpt` | NanoGPT | official | `POST https://nano-gpt.com/api/check-balance` | 5 min |
| `openrouter` | OpenRouter | official | `GET https://openrouter.ai/api/v1/key` | 5 min |
| `deepseek` | DeepSeek | official | `GET https://api.deepseek.com/user/balance` | 5 min |
| `google_ai_studio_t3` | Google AI Studio (via T3 Code) | local | T3 Code's local database (`statev2.sqlite`) | 5 min |
| `custom_json` | Custom (JSON endpoint) | user-defined | any URL you give | 5 min |
| `manual_budget` | Manual budget | manual | numbers you type | 1 min |
| `modal_budget` | Modal (manual budget) | manual | numbers you type from the Modal dashboard | 1 min |

---

## `claude_code`: Claude Code plan limits

- **Sign-in file:** `%USERPROFILE%\.claude\.credentials.json`, key
  `claudeAiOauth.accessToken`. Detect looks there; the path can be changed in
  Settings.
- **Request:** `GET https://api.anthropic.com/api/oauth/usage`
  - `Authorization: Bearer <accessToken>`
  - `anthropic-beta: oauth-2025-04-20`
  - `User-Agent: claude-code/2.0.32`
- **Rows:** one per entry of `limits`: `percent` is the bar and `resets_at`
  the countdown. The label comes from the entry's `kind`: `session` is
  "Current session", `weekly_all` is "All models", and `weekly_scoped` is
  "<model> only" using `scope.model.display_name`. Older replies named each
  entry with `id` and `title` instead; those are used when present. Replies
  without `limits` fall back to `five_hour` ("5-hour session") and
  `seven_day` ("7-day, all models"), using `utilization`. The reply has many
  other top-level keys (promotions, extra usage, spend); they are ignored.
- **Stability:** unofficial. Unofficial endpoint used by Claude Code, may stop
  working. It answers HTTP 429 when polled often (Claude Code polls it too),
  so the widget reads it at most every 10 minutes and shows the last good
  reading, marked stale, after a 429.
- **Docs:** none for this endpoint. Product page:
  https://docs.anthropic.com/en/docs/claude-code/overview
- **Sample reply:**

<!-- sample:claude_code -->
```json
{
  "five_hour": {
    "utilization": 12.0,
    "resets_at": "2030-01-01T05:00:00.000000+00:00",
    "limit_dollars": null,
    "used_dollars": null,
    "remaining_dollars": null,
    "locked_reason": null
  },
  "seven_day": {
    "utilization": 34.0,
    "resets_at": "2030-01-06T17:00:00.000000+00:00",
    "limit_dollars": null,
    "used_dollars": null,
    "remaining_dollars": null,
    "locked_reason": null
  },
  "seven_day_opus": null,
  "extra_usage": {
    "is_enabled": false,
    "monthly_limit": null,
    "used_credits": null,
    "utilization": null
  },
  "limits": [
    {
      "kind": "session",
      "group": "session",
      "percent": 12,
      "severity": "normal",
      "resets_at": "2030-01-01T05:00:00.000000+00:00",
      "scope": null,
      "is_active": true
    },
    {
      "kind": "weekly_all",
      "group": "weekly",
      "percent": 34,
      "severity": "normal",
      "resets_at": "2030-01-06T17:00:00.000000+00:00",
      "scope": null,
      "is_active": true
    },
    {
      "kind": "weekly_scoped",
      "group": "weekly",
      "percent": 5,
      "severity": "normal",
      "resets_at": "2030-01-06T17:00:00.000000+00:00",
      "scope": {
        "model": {
          "id": null,
          "display_name": "Opus"
        },
        "surface": null
      },
      "is_active": true
    }
  ]
}
```

---

## `codex`: ChatGPT plan limits for Codex

- **Sign-in file:** `%CODEX_HOME%\auth.json` when `CODEX_HOME` is set, else
  `%USERPROFILE%\.codex\auth.json`; keys `tokens.access_token` and
  `tokens.account_id`. A file that only holds `OPENAI_API_KEY` (API-key login)
  has no plan limits, and Detect says so.
- **Request:** `GET https://chatgpt.com/backend-api/wham/usage`
  - `Authorization: Bearer <access_token>`
  - `ChatGPT-Account-Id: <account_id>`
  - `User-Agent: codex-cli`
- **Rows:** `rate_limit.primary_window` and `rate_limit.secondary_window`
  (label from `limit_window_seconds`: 18000 is "5-hour limit", 604800 is
  "7-day limit"; bar from `used_percent`; reset from `reset_at`, a Unix time,
  or `reset_after_seconds`), plus `code_review_rate_limit.primary_window` as
  "Code review, 7-day limit" when present.
- **Stability:** unofficial. Endpoint used by the Codex CLI, may change.
- **Docs:** none for this endpoint. Codex CLI: https://github.com/openai/codex
- **Sample reply:**

<!-- sample:codex -->
```json
{
  "user_id": "user-REDACTED",
  "account_id": "account-REDACTED",
  "email": "someone@example.com",
  "plan_type": "plus",
  "rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 42,
      "limit_window_seconds": 18000,
      "reset_after_seconds": 3600,
      "reset_at": 1893474000
    },
    "secondary_window": {
      "used_percent": 17,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 86400,
      "reset_at": 1893556800
    }
  },
  "code_review_rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 3,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 604800,
      "reset_at": 1894075200
    },
    "secondary_window": null
  },
  "credits": {
    "has_credits": false,
    "unlimited": false,
    "balance": "0"
  }
}
```

---

## `antigravity`: Antigravity per-model quotas

- **Token file:** written by T3 Code's Antigravity provider at
  `%USERPROFILE%\.t3\userdata\providers\antigravity\<id>\antigravity-acp\acp_token.json`
  with `refresh_token`, `client_id`, `client_secret` and `token_uri`. Detect
  searches that folder and picks the newest file; if there is none it says
  so plainly. The kind is hidden when `%USERPROFILE%\.t3` does not exist.
  The OAuth client details come from that file; none are built into the
  widget.
- **Requests:**
  1. `POST https://oauth2.googleapis.com/token` (form body
     `grant_type=refresh_token`, `refresh_token`, `client_id`,
     `client_secret`). The access token is kept in memory until it expires.
     A `token_uri` that is not a Google host is ignored.
  2. `POST https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`
     with `Authorization: Bearer <access token>` and body
     `{"project": "aicode-consumers"}`. On HTTP 401 the token is renewed
     once and the call retried.
- **Rows:** one per entry of `models` that has `quotaInfo`, labelled with its
  `displayName`. Percent used is `(1 - remainingFraction) * 100`. A
  `quotaInfo` without `remainingFraction` means none is left (the reply
  omits zero values). `resetTime` is the countdown.
- **Stability:** unofficial. Internal Google Cloud Code endpoint, may change.
- **Docs:** none for this endpoint. Product page: https://antigravity.google/
- **Sample reply:**

<!-- sample:antigravity -->
```json
{
  "models": {
    "example-flash-tiered": {
      "displayName": "Gemini Flash",
      "quotaInfo": {
        "remainingFraction": 0.75,
        "resetTime": "2030-01-01T04:00:00Z"
      }
    },
    "example-sonnet-high": {
      "displayName": "Claude Sonnet (Thinking)",
      "quotaInfo": {
        "remainingFraction": 0.5,
        "resetTime": "2030-01-01T05:00:00Z"
      }
    },
    "example-used-up": {
      "displayName": "Example Model (used up)",
      "quotaInfo": {
        "resetTime": "2030-01-01T05:00:00Z"
      }
    },
    "example-internal": {
      "displayName": "Internal helper"
    }
  },
  "defaultAgentModelId": "example-flash-tiered"
}
```

---

## `nanogpt`: NanoGPT balance

- **Key:** a NanoGPT API key, saved in Credential Manager.
- **Request:** `POST https://nano-gpt.com/api/check-balance` with header
  `x-api-key: <key>` and body `{}`.
- **Rows:** one "Account balance" row from `usd_balance` (a string). NanoGPT
  is pay-per-use, so there is no bar or reset time.
- **Optional extra:** if you also use NanoGPT through T3 Code, enter that T3
  provider name and the row adds the number of runs T3 Code recorded for it.
  Leave it empty to skip.
- **Stability:** official.
- **Docs:** https://docs.nano-gpt.com/api-reference/endpoint/check-balance
- **Sample reply:**

<!-- sample:nanogpt -->
```json
{
  "usd_balance": "12.34567890",
  "nano_balance": "0.5",
  "nanoDepositAddress": "nano_1exampledepositaddress"
}
```

---

## `openrouter`: OpenRouter key credit and free requests

- **Key:** an OpenRouter API key, saved in Credential Manager.
- **Request:** `GET https://openrouter.ai/api/v1/key` with
  `Authorization: Bearer <key>`.
- **Rows:** when `data.limit` is set, "Key credit limit" (spent is
  `limit - limit_remaining`, money fields in US dollars, `limit_reset` shown
  in the text); otherwise "Spend (no key limit)" from `usage_monthly` and
  `usage`. Plus "Free model requests today" from
  `free_model_daily_requests` (`used` / `limit`) when its limit is above 0,
  resetting at the next UTC midnight.
- **Stability:** official.
- **Docs:** https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key
- **Sample reply** (the documented example with the label and amounts
  replaced):

<!-- sample:openrouter -->
```json
{
  "data": {
    "label": "my-key",
    "limit": 40,
    "limit_reset": "monthly",
    "limit_remaining": 30,
    "include_byok_in_limit": false,
    "usage": 10,
    "usage_daily": 1,
    "usage_weekly": 4,
    "usage_monthly": 10,
    "byok_usage": 0,
    "byok_usage_daily": 0,
    "byok_usage_weekly": 0,
    "byok_usage_monthly": 0,
    "is_free_tier": false,
    "free_model_daily_requests": {
      "used": 12,
      "limit": 50,
      "remaining": 38
    }
  }
}
```

---

## `deepseek`: DeepSeek balance

- **Key:** a DeepSeek API key, saved in Credential Manager.
- **Request:** `GET https://api.deepseek.com/user/balance` with
  `Authorization: Bearer <key>`.
- **Rows:** one "Balance (CUR)" row per entry of `balance_infos`
  (`total_balance`, with `granted_balance` in the text). The money fields
  are filled only for a USD balance; other currencies are shown as text.
  `is_available: false` adds "not enough for API calls".
- **Stability:** official.
- **Docs:** https://api-docs.deepseek.com/api/get-user-balance
- **Sample reply** (the documented example with the amounts replaced):

<!-- sample:deepseek -->
```json
{
  "is_available": true,
  "balance_infos": [
    {
      "currency": "CNY",
      "total_balance": "42.00",
      "granted_balance": "2.00",
      "topped_up_balance": "40.00"
    }
  ]
}
```

---

## `google_ai_studio_t3`: Google AI Studio requests counted by T3 Code

- **Source:** T3 Code's local database
  `%USERPROFILE%\.t3\userdata\statev2.sqlite`, opened read-only. The kind is
  hidden when that file does not exist. No key and no network.
- **What is read:** the runs table (`orchestration_v2_projection_runs`):
  each run's `provider_instance_id`, the model at
  `payload_json.modelSelection.model`, and `requested_at`.
- **Rows:** one per model used through that T3 provider in the last 30 days
  (busiest today first), with today's count. The daily window starts at
  08:00 UTC (midnight Pacific standard time).
- **Daily cap setting:** the per-model request cap for your tier from
  Google's rate-limit page. `0` (the default) shows the count only; a number
  shows a bar of count / cap.
- **Limits of this method:** it counts only requests made through T3 Code
  on this computer. Google has no endpoint that reports the remaining
  free-tier quota.
- **Stability:** local.
- **Docs:** https://ai.google.dev/gemini-api/docs/rate-limits

---

## `custom_json`: any JSON endpoint

- **Settings:** URL, method (GET or POST; POST sends `{}`), optional key
  (Credential Manager), the header that carries it (default
  `Authorization`), a prefix (default `Bearer `), and dot paths into the
  reply. Paths use dots and list indexes, for example `data.items.0.used`.
- **Rows:** one row named after the instance label.
  - `used` + `limit` paths: a bar of used / limit.
  - `balance` path: the remaining amount (alone, or added to the bar's text).
  - `unit` `USD` fills the money fields; any other text (for example
    `credits`) is shown after the numbers.
- **Errors:** HTTP 401/403 shows "The service rejected the key (HTTP 401)."
  A network failure shows "Could not reach <host>. Check your internet
  connection or the URL." Replies that are not JSON, missing paths, values
  that are not numbers and a limit of 0 each get their own message.
- **Stability:** user-defined.
- **Sample reply** (with `used_path` `data.used` and `limit_path`
  `data.limit` this gives 25%):

<!-- sample:custom_json -->
```json
{
  "data": {
    "used": 12.5,
    "limit": 50,
    "balance": 37.5,
    "items": [
      {
        "used": 3
      }
    ]
  }
}
```

---

## `manual_budget`: numbers you type

- **Settings:** monthly credit, your own cap (optional; used instead of the
  monthly credit when set), spent so far, currency text (default `USD`) and
  the reset day of the month (1 to 31; short months use their last day).
- **Rows:** one "Monthly budget" row: bar of spent / allowance, the amount
  left, and a countdown to the next reset day (midnight UTC). Amounts are in
  the currency you typed.
- **Network:** none.
- **Stability:** manual.

## `modal_budget`: Modal (manual budget)

Modal's billing data is available through its Python SDK
(`modal.billing.workspace_billing_report`) and the `modal billing report`
command, and Modal says it offers programmatic billing-report APIs to Team
and Enterprise workspaces. There is no documented plain HTTPS endpoint with a
key that a widget could call, so Modal is offered as a manual budget preset:
type your monthly credit and your spend from the Modal dashboard's usage
page. Same settings and rows as `manual_budget`.

- **Docs:** https://modal.com/docs/guide/billing and
  https://modal.com/docs/reference/modal.billing
- **Stability:** manual.
