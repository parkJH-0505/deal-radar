"""Tiny persistence layer: JSON / JSONL files under data/ (committed to git)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE = DATA / "state"


def read_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
                    encoding="utf-8")


class JsonlCache:
    """Append-only key -> record cache (one JSON object per line)."""

    def __init__(self, path: Path, key: str):
        self.path, self.key = path, key
        self.data: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.data[str(rec[key])] = rec
        self._new: list[dict] = []

    def get(self, k: str):
        return self.data.get(str(k))

    def __contains__(self, k) -> bool:
        return str(k) in self.data

    def put(self, rec: dict) -> None:
        self.data[str(rec[self.key])] = rec
        self._new.append(rec)

    def flush(self) -> None:
        if not self._new:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for rec in self._new:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._new.clear()
