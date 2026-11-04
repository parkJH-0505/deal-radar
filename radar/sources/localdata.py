"""행정안전부 인허가 조회서비스 (apis.data.go.kr/1741000/<slug>).

Each industry is its own service (slug) and needs its own 활용신청 on
data.go.kr; the same personal key works for all of them once approved.

Signals we derive (per normalised company/brand name):
- expansion:     an established operator (permit older than the window) obtained
                 a NEW factory-scale permit (>= FACTORY_M2) inside the window -> 공장 증설
- chain_rollout: >= 3 new sites of the same brand inside the window, any size
                 -> 소형 제조매장 체인 확장 (프랜차이즈형 성장 신호)
- multi_new:     two new factory-scale permits inside the window, no older site
Single first permits (brand-new entrants) are too noisy to report alone.

The national table (~96k rows for 식품제조가공업) mixes real factories with
69㎡ bakery-cafes, so site area (LCTN_AREA / FCLT_TOTAL_SCL) gates "factory".
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from ..entity import norm_name, sido
from ..http import digits, find_items, find_key, get, log, parse_body, pick, to_int

BASE = "https://apis.data.go.kr/1741000/{slug}/{op}"

CLOSED_WORDS = ("폐업", "취소", "말소", "휴업")
FACTORY_M2 = 200.0
CHAIN_MIN_NEW = 3


def _area(rec: dict) -> float | None:
    vals = []
    for k in ("LCTN_AREA", "FCLT_TOTAL_SCL"):
        v = pick(rec, k)
        try:
            if v not in (None, "", "None"):
                vals.append(float(v))
        except (TypeError, ValueError):
            pass
    return max(vals) if vals else None


def fetch_all(key: str, slug: str, *, page_size: int = 1000, max_pages: int = 400,
              extra: dict | None = None) -> list[dict]:
    out: list[dict] = []
    page = 1
    total = None
    while page <= max_pages:
        params = {"serviceKey": key, "pageNo": page, "numOfRows": page_size,
                  "returnType": "json"}
        if extra:
            params.update(extra)
        body = parse_body(get(BASE.format(slug=slug, op="info"), params, pause=0.1))
        _raise_on_error(body)
        items = find_items(body) or []
        if total is None:
            total = to_int(find_key(body, "totalCount", "total_count"))
            log(f"[localdata:{slug}] totalCount={total}")
        out.extend(items)
        if not items or (total is not None and len(out) >= total):
            break
        page += 1
    log(f"[localdata:{slug}] fetched {len(out)} rows in {page} page(s)")
    return out


def _row_key(r: dict) -> str:
    return str(r.get("mng_no") or f"{r['name']}|{r['permit_date']}|{r['addr']}")


def load_store(path) -> dict[str, dict]:
    import gzip
    import json
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {_row_key(r): r for r in map(json.loads, f) if r}


def save_store(path, store: dict[str, dict]) -> None:
    import gzip
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        for k in sorted(store):
            gz.write((json.dumps(store[k], ensure_ascii=False) + "\n").encode())


def sync(key: str, slug: str, path, since: str | None) -> tuple[list[dict], str]:
    """Keep a local copy of one 인허가 업종. The API caps a page at 100 rows, so the
    first run pulls everything (~1k pages) and later runs only rows updated since
    `since` (YYYY-MM-DD, via DAT_UPDT_PNT)."""
    store = load_store(path)
    if store and since:
        raw = fetch_all(key, slug, page_size=100, max_pages=2000,
                        extra={"cond[DAT_UPDT_PNT::GTE]": since})
        mode = f"증분({since}~) {len(raw):,}건"
    else:
        raw = fetch_all(key, slug, page_size=100, max_pages=2000)
        store = {}
        mode = f"전체 {len(raw):,}건"
    for r in raw:
        n = normalize(r)
        store[_row_key(n)] = n
    save_store(path, store)
    return list(store.values()), mode


def _raise_on_error(body) -> None:
    code = find_key(body, "resultCode", "result_code")
    if code not in (None, "00", "0", "000", "INFO-000", 0):
        msg = find_key(body, "resultMsg", "result_msg", "message")
        raise RuntimeError(f"localdata error {code}: {msg}")


def normalize(rec: dict) -> dict:
    status = str(pick(rec, "SALS_STTS_NM", "DTL_SALS_STTS_NM", "TRD_STATE_NM",
                      "DTL_STATE_NM", default="") or "")
    close = digits(pick(rec, "CLSBIZ_YMD", "DCB_YMD", "CLS_YMD"))
    addr = pick(rec, "ROAD_NM_ADDR", "RDN_WHL_ADDR", "LOTNO_ADDR", "SITE_WHL_ADDR",
                default="")
    name = pick(rec, "BPLC_NM", "BPLCNM", default="")
    return {
        "mng_no": pick(rec, "MNG_NO", "OPN_SVC_MNG_NO", "MGT_NO", "OPN_MNG_NO"),
        "name": name,
        "name_norm": norm_name(name),
        "permit_date": digits(pick(rec, "LCPMT_YMD", "APV_PERM_YMD"))[:8],
        "status_code": pick(rec, "SALS_STTS_CD", "TRD_STATE_GBN"),
        "status": status,
        "closed": bool(close) or any(w in status for w in CLOSED_WORDS),
        "addr": addr,
        "sido": sido(addr),
        "items": pick(rec, "FBCTN_HNDL_ITEM_CN", default=""),
        "area_m2": _area(rec),
        "updated": pick(rec, "DAT_UPDT_PNT", "LAST_MDFCN_PNT", "UPDATE_DT"),
    }


def _factory(site: dict) -> bool:
    a = site.get("area_m2")
    return a is None or a >= FACTORY_M2


def expansion_signals(rows: list[dict], *, today: dt.date, window_days: int,
                      industry_label: str) -> list[dict]:
    cutoff = (today - dt.timedelta(days=window_days)).strftime("%Y%m%d")
    by_co: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["name_norm"] and len(r["name_norm"]) >= 2:
            by_co[r["name_norm"]].append(r)

    signals = []
    for name_norm, sites in by_co.items():
        active = [s for s in sites if not s["closed"]]
        new = [s for s in active if s["permit_date"] and s["permit_date"] >= cutoff]
        old = [s for s in sites if s["permit_date"] and s["permit_date"] < cutoff]
        if not new:
            continue
        new_factory = [s for s in new if _factory(s)]
        kind = None
        if len(new) >= CHAIN_MIN_NEW:
            kind = "chain_rollout"
        elif old and new_factory:
            kind = "expansion"
        elif len(new_factory) >= 2:
            kind = "multi_new"
        if not kind:
            continue
        first = min((s["permit_date"] for s in sites if s["permit_date"]), default="")
        # display name: the earliest-permitted site (usually the HQ, not "제2공장")
        main = min(sites, key=lambda s: (s["permit_date"] or "99999999", len(s["name"])))
        signals.append({
            "source": "localdata",
            "kind": kind,
            "name": main["name"],
            "name_norm": name_norm,
            "industry": industry_label,
            "active_sites": len(active),
            "new_sites": len(new),
            "first_permit": first,
            "new_permits": [{"date": s["permit_date"], "addr": s["addr"],
                             "area_m2": s["area_m2"], "name": s["name"]} for s in new][:8],
            "max_new_area_m2": max((s["area_m2"] or 0) for s in new),
            "sido": sorted({s["sido"] for s in active if s["sido"]}),
        })
    log(f"[localdata] {industry_label}: {len(signals)} expansion-type signals")
    return signals
