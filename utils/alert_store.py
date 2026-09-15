"""Persistent store for sent regulatory alert deduplication."""

import json
import os
import re
import threading
from typing import Dict

_lock = threading.Lock()


def _db_path() -> str:
    return os.getenv("ALERTS_DB_PATH", os.path.join("data", "alerts_sent.json"))


def _normalize_key(company: str, headline: str) -> str:
    c = re.sub(r"\s+", " ", company.strip().lower())
    h = re.sub(r"\s+", " ", headline.strip().lower())[:200]
    return f"{c}_{h}"


def _load() -> Dict[str, bool]:
    path = _db_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: Dict[str, bool]) -> None:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def was_alert_sent(company: str, headline: str) -> bool:
    key = _normalize_key(company, headline)
    with _lock:
        return _load().get(key, False)


def save_alert(company: str, headline: str) -> None:
    key = _normalize_key(company, headline)
    with _lock:
        data = _load()
        data[key] = True
        _save(data)
