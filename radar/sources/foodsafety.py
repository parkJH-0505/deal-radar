"""식품안전나라 open API: 품목제조보고(I1250) and HACCP 지정(I0580).

Why: the 식품 OEM thesis ('다품종 소량') is directly measurable here.
  - new 품목 reports per business site in the last N days  → SKU velocity (다품종)
  - parenthesised tags in product names, e.g. '유자오리엔탈드레싱(CJ푸드빌)'. On real data
    (2026-11 probe) these are mostly flavours/variants ('실온', '초코', '오리지널'), so they are
    passed to the judge as raw hints only, never scored
  - FIRST-ever HACCP designation of a site → usually precedes large-channel entry (쿠팡·컬리·대형마트)
  - LCNS_NO starts with the license year → business age proxy

Endpoint: http://openapi.foodsafetykorea.go.kr/api/{KEY}/{SVC}/json/{start}/{end}[/FIELD=value]
Max 1,000 rows per call. Keys are issued per service: FOODSAFETY_KEY (I1250), FOODSAFETY_KEY_HACCP (I0580).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from ..entity import norm_name
from ..http import HttpError, get, log

BASE = "http://openapi.foodsafetykorea.go.kr/api"
PAGE = 1000

# product-name parentheses that are NOT a client brand
_NOT_BRAND = re.compile(r"^(\d|[A-Za-z]{1,2}$|소|중|대|냉동|냉장|수출|내수|업소용|벌크|bulk|증정|리뉴얼|신|구|"
                        r"\S*(g|kg|ml|l|L|입|개|봉|팩|EA|ea|호|형|맛|향|용)$)")
_BRAND = re.compile(r"\(([^()]{2,20})\)")


def _rows(body: dict, svc: str) -> tuple[int, list[dict]]:
    blk = body.get(svc) or {}
    res = blk.get("RESULT") or {}
    code = res.get("CODE", "INFO-000")
    if code not in ("INFO-000", "INFO-200"):  # INFO-200 = no data
        raise RuntimeError(f"foodsafety {svc} {code}: {res.get('MSG')}")
    return int(blk.get("total_count") or 0), blk.get("row") or []


def fetch(key: str, svc: str, *, flt: str = "", max_rows: int = 200_000) -> list[dict]:
    out: list[dict] = []
    start, total = 1, None
    while total is None or start <= min(total, max_rows):
        url = f"{BASE}/{key}/{svc}/json/{start}/{start + PAGE - 1}" + (f"/{flt}" if flt else "")
        body = get(url, None, timeout=120, pause=0.2).json()
        total, rows = _rows(body, svc)
        if not rows:
            break
        out.extend(rows)
        start += PAGE
    log(f"[foodsafety] {svc} {flt or '-'}: {len(out):,} / {total or 0:,} rows")
    return out


def recent_reports(key: str, since: dt.date, today: dt.date) -> tuple[list[dict], str]:
    """품목제조보고 since `since`. The PRMS_DT filter behaves as '≥' on the sample endpoint;
    if the live API turns out to be exact-match, fall back to one call per day."""
    s = since.strftime("%Y%m%d")
    rows = fetch(key, "I1250", flt=f"PRMS_DT={s}")
    dates = {r.get("PRMS_DT") for r in rows}
    if rows and dates == {s}:
        mode, rows = "일별", []
        d = since
        while d <= today:
            rows += fetch(key, "I1250", flt=f"PRMS_DT={d:%Y%m%d}")
            d += dt.timedelta(days=1)
    else:
        mode = "이후 일괄"
    t = today.strftime("%Y%m%d")  # a few rows carry typo dates like 21030618
    return [r for r in rows if s <= (r.get("PRMS_DT") or "") <= t], mode


def first_haccp(rows: list[dict], since: str) -> list[dict]:
    """Sites whose FIRST-ever HACCP designation is on/after `since` (an added item at an
    already-certified site is routine), excluding cancelled designations and closed sites.
    Returns one row per site: the earliest designation."""
    first: dict[str, dict] = {}
    for h in rows:
        lc, d = h.get("LCNS_NO"), h.get("HACCP_APPN_DT") or ""
        if not lc or not d:
            continue
        if lc not in first or d < first[lc]["HACCP_APPN_DT"]:
            first[lc] = h
    return [h for h in first.values()
            if h["HACCP_APPN_DT"] >= since and not h.get("ASGN_CANCL_DT") and not h.get("CLSBIZ_DT")]


def recent_haccp(key: str, since: dt.date) -> list[dict]:
    """I0580 has no date filter, and a site gets one row per designated item, so read the
    whole register (~40k rows) and keep sites newly entering HACCP."""
    rows = fetch(key, "I0580", max_rows=400_000)
    out = first_haccp(rows, since.strftime("%Y%m%d"))
    log(f"[foodsafety] I0580 {len(rows):,} rows → first-ever HACCP since {since:%Y%m%d}: {len(out):,} sites")
    return out


def client_brands(product_names: list[str], own_name: str) -> list[str]:
    own = norm_name(own_name)
    seen: dict[str, int] = defaultdict(int)
    for n in product_names:
        for b in _BRAND.findall(n or ""):
            b = b.strip()
            if _NOT_BRAND.search(b) or norm_name(b) == own or own and own in norm_name(b):
                continue
            seen[b] += 1
    return sorted(seen, key=lambda b: -seen[b])


def site_signals(reports: list[dict], haccp: list[dict], *, today: dt.date,
                 min_new_items: int, min_types: int) -> list[dict]:
    """Aggregate by 인허가번호 (one manufacturing site)."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in reports:
        if (r.get("INDUTY_CD_NM") or "").startswith("식품제조가공업") and r.get("LCNS_NO"):
            by[r["LCNS_NO"]].append(r)
    hac = {h["LCNS_NO"]: h for h in haccp if h.get("LCNS_NO")}
    out = []
    for lcns, rs in by.items():
        types = {r.get("PRDLST_DCNM") for r in rs if r.get("PRDLST_DCNM")}
        brands = client_brands([r.get("PRDLST_NM", "") for r in rs], rs[0].get("BSSH_NM", ""))
        if len(rs) < min_new_items and lcns not in hac:
            continue
        name = rs[0].get("BSSH_NM", "")
        year = lcns[:4] if lcns[:4].isdigit() else ""
        out.append({
            "lcns_no": lcns, "name": name, "name_norm": norm_name(name),
            "license_year": year,
            "new_items": len(rs), "item_types": len(types), "types": sorted(types)[:10],
            "client_brands": brands[:10],
            "first_new": min(r["PRMS_DT"] for r in rs), "last_new": max(r["PRMS_DT"] for r in rs),
            "sku_burst": len(rs) >= min_new_items and len(types) >= min_types,
            "haccp_new": hac.get(lcns, {}).get("HACCP_APPN_DT"),
            "haccp_item": hac.get(lcns, {}).get("PRDLST_NM"),
        })
    for h in haccp:  # HACCP-only sites (no new 품목 in the window)
        if h.get("LCNS_NO") and h["LCNS_NO"] not in by and \
                (h.get("INDUTY_CD_NM") or "").startswith("식품제조가공업"):
            lc = h["LCNS_NO"]
            out.append({"lcns_no": lc, "name": h.get("BSSH_NM", ""), "name_norm": norm_name(h.get("BSSH_NM", "")),
                        "license_year": lc[:4] if lc[:4].isdigit() else "", "new_items": 0, "item_types": 0,
                        "types": [], "client_brands": [], "sku_burst": False,
                        "haccp_new": h.get("HACCP_APPN_DT"), "haccp_item": h.get("PRDLST_NM")})
    return out


def run(key: str, *, haccp_key: str = "", today: dt.date, window_days: int, min_new_items: int,
        min_types: int) -> tuple[list[dict], str]:
    """식품안전나라는 서비스마다 인증키가 따로 나온다: I1250은 key, I0580은 haccp_key."""
    since = today - dt.timedelta(days=window_days)
    try:
        reports, mode = recent_reports(key, since, today)
    except (HttpError, RuntimeError, ValueError) as e:
        return [], f"식품안전나라 품목제조보고 수집 실패: {e}"
    haccp: list[dict] = []
    if haccp_key:
        try:
            haccp = recent_haccp(haccp_key, since)
        except (HttpError, RuntimeError, ValueError) as e:
            log(f"[foodsafety] HACCP 수집 실패: {e}")
    sig = site_signals(reports, haccp, today=today, min_new_items=min_new_items, min_types=min_types)
    note = (f"식품안전나라: 최근 {window_days}일 품목제조보고 {len(reports):,}건({mode}), HACCP 첫 지정 {len(haccp):,}곳 "
            f"→ 다품종·HACCP 신호 {len(sig):,}곳")
    return sig, note
