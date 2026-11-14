"""Read-only access to the T3 Code state database.

T3 Code (a desktop coding app) records every model run in a local SQLite file,
``%USERPROFILE%\\.t3\\userdata\\statev2.sqlite``, table
``orchestration_v2_projection_runs``. Two provider kinds use it to count
requests per model. The file is always opened read-only and nothing in it is
ever changed.

Schema note: the table keeps the model inside ``payload_json``
(``$.modelSelection.model``) and the run time in ``requested_at``. The query
also accepts plain ``model`` / ``started_at`` columns in case a later version
adds them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from providers.base import ProviderError, home_dir

RUNS_TABLE = "orchestration_v2_projection_runs"
_MODEL_JSON_EXPR = "json_extract(payload_json, '$.modelSelection.model')"


def default_db_path() -> Path:
    """Return ``%USERPROFILE%\\.t3\\userdata\\statev2.sqlite``."""
    return home_dir() / ".t3" / "userdata" / "statev2.sqlite"


def _read_only_uri(db_path: Path) -> str:
    """Build a ``file:...?mode=ro`` URI for ``sqlite3.connect``."""
    resolved = db_path.resolve().as_posix()
    return f"file:{quote(resolved, safe='/:')}?mode=ro"


def _iso_utc(moment: datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDTHH:MM:SSZ``."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a stored timestamp (ISO text or epoch number) into UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _model_from_payload(raw_payload: Any) -> str | None:
    """Extract ``modelSelection.model`` from a stored ``payload_json``."""
    try:
        data = json.loads(raw_payload or "{}")
    except (TypeError, ValueError):
        return None
    selection = data.get("modelSelection") if isinstance(data, dict) else None
    model = selection.get("model") if isinstance(selection, dict) else None
    return str(model) if model else None


def count_runs(
    db_path: Path,
    provider_instance_id: str,
    since: datetime | None = None,
) -> dict[str, int]:
    """Count runs per model for one T3 provider, optionally since a moment.

    Raises ``ProviderError`` (NOT_FOUND or BAD_RESPONSE) when the database is
    missing or cannot be read.
    """
    if not db_path.is_file():
        raise ProviderError(ProviderError.NOT_FOUND, "The T3 Code database was not found.")

    try:
        connection = sqlite3.connect(_read_only_uri(db_path), uri=True, timeout=2.0)
    except sqlite3.Error:
        raise ProviderError(ProviderError.BAD_RESPONSE, "The T3 Code database could not be opened.") from None

    try:
        connection.execute("PRAGMA query_only = ON")
        columns = {
            str(row[1]): str(row[2] or "").upper()
            for row in connection.execute(f"PRAGMA table_info({RUNS_TABLE})").fetchall()
        }
        if not columns:
            raise ProviderError(ProviderError.BAD_RESPONSE, "The T3 Code database has no run history table.")

        model_expr = "model" if "model" in columns else _MODEL_JSON_EXPR
        time_column = next(
            (name for name in ("started_at", "requested_at", "completed_at") if name in columns), ""
        )

        where = "provider_instance_id = ?"
        params: list[Any] = [provider_instance_id]
        if since is not None and time_column:
            numeric = any(t in columns[time_column] for t in ("INT", "REAL", "NUM", "DOUBLE", "FLOAT"))
            if numeric:
                where += f" AND {time_column} >= ?"
                params.append(since.timestamp())
            else:
                where += f" AND julianday({time_column}) >= julianday(?)"
                params.append(_iso_utc(since))

        try:
            cursor = connection.execute(
                f"SELECT {model_expr} AS model, COUNT(*) FROM {RUNS_TABLE} WHERE {where} GROUP BY model",
                params,
            )
            return {str(model): int(count) for model, count in cursor.fetchall() if model}
        except sqlite3.OperationalError:
            # SQLite without the JSON1 extension: parse the payload in Python.
            if "payload_json" not in columns:
                raise
            select_time = time_column or "NULL"
            counts: dict[str, int] = {}
            for moment_raw, payload in connection.execute(
                f"SELECT {select_time}, payload_json FROM {RUNS_TABLE} WHERE provider_instance_id = ?",
                (provider_instance_id,),
            ):
                if since is not None:
                    moment = _parse_timestamp(moment_raw)
                    if moment is None or moment < since:
                        continue
                model = _model_from_payload(payload)
                if model:
                    counts[model] = counts.get(model, 0) + 1
            return counts
    except ProviderError:
        raise
    except sqlite3.Error:
        raise ProviderError(ProviderError.BAD_RESPONSE, "The T3 Code database could not be read.") from None
    finally:
        connection.close()
