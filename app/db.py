import sqlite3
from pathlib import Path
from typing import Optional
import json
from datetime import datetime
from zoneinfo import ZoneInfo


DB_PATH = Path("/data/state.db")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
        """)
        conn.commit()
    finally:
        conn.close()


def kv_get(key: str) -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else None
    finally:
        conn.close()


def kv_set(key: str, value: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_paused() -> bool:
    return (kv_get("paused") or "0") == "1"


def set_paused(paused: bool) -> None:
    kv_set("paused", "1" if paused else "0")


def get_last_uid() -> int:
    return int(kv_get("last_uid") or "0")


def set_last_uid(uid: int) -> None:
    kv_set("last_uid", str(uid))


def get_uidvalidity() -> Optional[str]:
    return kv_get("uidvalidity")


def set_uidvalidity(v: str) -> None:
    kv_set("uidvalidity", v)


def _channel_key(channel_id: str) -> str:
    return f"tg_source_last_id:{channel_id}"


def get_tg_source_last_id(channel_id: str) -> int:
    return int(kv_get(_channel_key(channel_id)) or "0")


def set_tg_source_last_id(channel_id: str, message_id: int) -> None:
    kv_set(_channel_key(channel_id), str(message_id))


def _today_key(timezone: str) -> str:
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def add_daily_stats(timezone: str, total_delta: int, other_delta: int, claim_deltas: dict[str, int]) -> None:
    date_key = _today_key(timezone)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT v FROM kv WHERE k = ?",
            ("daily_stats",),
        ).fetchone()

        stats = {
            "date": date_key,
            "total": 0,
            "other": 0,
            "claims": {},
        }
        if row and row["v"]:
            try:
                loaded = json.loads(row["v"])
                if isinstance(loaded, dict):
                    stats.update(loaded)
            except Exception:
                # ignore malformed persisted value and overwrite with clean structure
                pass

        if stats.get("date") != date_key:
            stats = {"date": date_key, "total": 0, "other": 0, "claims": {}}

        stats["total"] = int(stats.get("total", 0)) + int(total_delta)
        stats["other"] = int(stats.get("other", 0)) + int(other_delta)

        claims = stats.get("claims") or {}
        if not isinstance(claims, dict):
            claims = {}
        for claim_id, delta in claim_deltas.items():
            claims[claim_id] = int(claims.get(claim_id, 0)) + int(delta)
        stats["claims"] = claims

        conn.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            ("daily_stats", json.dumps(stats, ensure_ascii=True)),
        )
        conn.commit()
    finally:
        conn.close()


def get_today_daily_stats(timezone: str) -> dict:
    date_key = _today_key(timezone)
    row = kv_get("daily_stats")
    if not row:
        return {"date": date_key, "total": 0, "other": 0, "claims": {}}

    try:
        stats = json.loads(row)
    except Exception:
        return {"date": date_key, "total": 0, "other": 0, "claims": {}}

    if not isinstance(stats, dict) or stats.get("date") != date_key:
        return {"date": date_key, "total": 0, "other": 0, "claims": {}}

    claims = stats.get("claims") or {}
    if not isinstance(claims, dict):
        claims = {}

    normalized_claims = {}
    for claim_id, cnt in claims.items():
        normalized_claims[str(claim_id)] = int(cnt)

    return {
        "date": date_key,
        "total": int(stats.get("total", 0)),
        "other": int(stats.get("other", 0)),
        "claims": normalized_claims,
    }
