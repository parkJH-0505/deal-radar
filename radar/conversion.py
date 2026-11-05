"""개인사업자 → 법인 전환 감지.

A sole proprietorship that has proven itself without outside capital and
incorporates is at the moment it starts to need capital. In the 국민연금 data the
same business shows up twice: as a 개인사업장 (form 2) that fades out, and as a
new 법인 (form 1) with the same name in the same place that starts around then.

Without this, the 법인 looks like '0 → N명 in a year' (fake growth) and like a
brand-new company (wrong age). With it we get the real start date and a growth
rate computed on the combined head-count.

Match rule (deliberately strict; the judgment step verifies):
  same normalized name, same 광역시도, and (same 도로명 or same 업종명);
  the 개인 site was last seen no later than 3 months after the 법인 started and
  has shrunk to ≤ 30% of its peak (or vanished) by the latest month.
"""
from __future__ import annotations

from collections import defaultdict

from .sources.nps_monthly import _ym_minus


def _ym_of(ymd: str) -> str:
    return ymd[:6]


def _months_between(a: str, b: str) -> int:
    return (int(b[:4]) * 12 + int(b[4:6])) - (int(a[:4]) * 12 + int(a[4:6]))


def detect(yms: list[str], data: dict, indiv: dict, *, lookback_months: int = 24) -> dict[str, dict]:
    """corp panel key -> conversion info, for 법인 started within `lookback_months`."""
    if not yms or not indiv:
        return {}
    last = yms[-1]
    since = _ym_minus(last, lookback_months)
    iyms = sorted(indiv)

    # index 개인 sites by normalized name: nn -> key -> {ym: row}
    by_nn: dict[str, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    for ym in iyms:
        for k, r in indiv[ym].items():
            by_nn[k.split("|", 1)[1]][k][ym] = r

    out: dict[str, dict] = {}
    for ck, cr in data[last].items():
        start = _ym_of(cr["adopt"])
        if start < since:
            continue
        nn = ck.split("|", 1)[1]
        best = None
        for ik, hist in by_nn.get(nn, {}).items():
            any_row = next(iter(hist.values()))
            if any_row.get("sido") != cr.get("sido"):
                continue
            same_road = bool(cr.get("addr")) and any_row.get("addr") == cr.get("addr")
            same_ind = bool(cr.get("induty_name")) and any_row.get("induty_name") == cr.get("induty_name")
            if not (same_road or same_ind):
                continue
            seen = sorted(hist)
            if seen[0] > _ym_minus(start, -3):  # 개인 must predate (or overlap) the 법인 start
                continue
            peak = max(r["headcount"] for r in hist.values())
            now_hc = hist.get(last, {}).get("headcount", 0)
            if now_hc > 0.3 * peak:
                continue  # still running as a 개인 → a separate business, not a conversion
            info = {
                "indiv_key": ik, "indiv_name": any_row["name"],
                "indiv_adopt": min(r["adopt"] for r in hist.values()),
                "indiv_peak_headcount": peak,
                "indiv_last_seen": seen[-1] if now_hc == 0 else last,
                "corp_start": cr["adopt"],
                "months_since_conversion": max(0, _months_between(start, last)),
                "match": "도로명+업종" if (same_road and same_ind) else ("도로명" if same_road else "업종"),
                "indiv_series": {ym: hist[ym]["headcount"] for ym in seen},
            }
            if best is None or peak > best["indiv_peak_headcount"]:
                best = info
        if best:
            out[ck] = best
    return out


def combined_growth(series_corp: dict[str, int], series_indiv: dict[str, int], last: str) -> dict:
    """Head-count growth on 법인 + 개인 combined (the real business)."""
    def tot(ym):
        return series_corp.get(ym, 0) + series_indiv.get(ym, 0)
    out = {}
    now = tot(last)
    for lag in (6, 12):
        then = tot(_ym_minus(last, lag))
        if then > 0:
            out[f"growth_{lag}m"] = round((now - then) / then, 3)
    out["combined_headcount_12m_ago"] = tot(_ym_minus(last, 12))
    return out
