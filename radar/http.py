"""HTTP helpers shared by every source.

Rules:
- API keys never appear in logs (mask_url masks serviceKey / crtfc_key).
- data.go.kr envelopes vary between services, so record extraction is
  structural (find the list of dicts) instead of path-based.
"""
from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Iterable

import requests

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "deal-radar/0.1 (+research)"

_KEY_RE = re.compile(r"(serviceKey|crtfc_key|ServiceKey)=[^&]+")


def log(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


def mask_url(url: str) -> str:
    return _KEY_RE.sub(r"\1=***", url)


class HttpError(RuntimeError):
    pass


def get(url: str, params: dict | None = None, *, retries: int = 3,
        timeout: int = 60, pause: float = 0.0) -> requests.Response:
    last = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if pause:
                time.sleep(pause)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (400, 401, 403, 404):
                break  # retrying won't help
        except requests.RequestException as e:  # network / timeout
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 ** attempt)
    raise HttpError(f"{mask_url(url)} -> {mask_url(str(last))}")


# ---------------------------------------------------------------- parsing

def parse_body(r: requests.Response) -> Any:
    """Return JSON-like python object from a JSON or XML response."""
    text = r.text.strip()
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    if text.startswith("<"):
        return _xml_to_obj(ET.fromstring(text))
    raise HttpError(f"Unparseable body: {text[:200]}")


def _xml_to_obj(el: ET.Element) -> Any:
    children = list(el)
    if not children:
        return (el.text or "").strip()
    out: dict[str, Any] = {}
    for c in children:
        v = _xml_to_obj(c)
        if c.tag in out:
            if not isinstance(out[c.tag], list):
                out[c.tag] = [out[c.tag]]
            out[c.tag].append(v)
        else:
            out[c.tag] = v
    return out


_ITEM_KEYS = ("item", "items", "row", "rows", "data", "list", "body", "response")


def find_items(obj: Any) -> list[dict] | None:
    """Find the record list inside an arbitrary data.go.kr envelope."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            # a list of envelopes? descend if each has a single nested list
            if all(_is_flat(x) for x in obj):
                return obj
            for x in obj:
                res = find_items(x)
                if res:
                    return res
            return obj
        return None
    if isinstance(obj, dict):
        for k in _ITEM_KEYS:
            if k in obj:
                v = obj[k]
                if isinstance(v, dict) and _is_flat(v) and v:
                    return [v]
                res = find_items(v)
                if res is not None:
                    return res
        for v in obj.values():
            if isinstance(v, (dict, list)):
                res = find_items(v)
                if res:
                    return res
    return None


def _is_flat(d: dict) -> bool:
    return not any(isinstance(v, (dict, list)) for v in d.values())


def find_key(obj: Any, *names: str) -> Any:
    """Recursive case/underscore-insensitive key lookup (e.g. totalCount)."""
    wanted = {_norm_key(n) for n in names}
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if _norm_key(k) in wanted and not isinstance(v, (dict, list)):
                    return v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _norm_key(k: str) -> str:
    return k.replace("_", "").lower()


def pick(rec: dict, *names: str, default: Any = None) -> Any:
    """Field getter tolerant to BPLC_NM / bplcNm / bplc_nm spellings."""
    idx = {_norm_key(k): v for k, v in rec.items()}
    for n in names:
        v = idx.get(_norm_key(n))
        if v not in (None, ""):
            return v
    return default


def digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def to_int(s: Any) -> int | None:
    d = re.sub(r"[^\d\-]", "", str(s or ""))
    try:
        return int(d)
    except ValueError:
        return None


def shape(obj: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """Compact structure summary for probe output (values truncated)."""
    if depth >= max_depth:
        return "…"
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1, max_depth), f"(len={len(obj)})"] if obj else []
    s = str(obj)
    return s if len(s) <= 40 else s[:37] + "..."


def chunked(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
