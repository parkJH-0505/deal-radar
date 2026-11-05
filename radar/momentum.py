"""Industry momentum from the 국민연금 panel: which 업종 are young companies
piling into, and are the young companies already in it hiring faster?

This is a cross-sectional signal: a single company's growth is noisy, but when
formation AND hiring accelerate across a whole 업종 at once, something outside
the companies changed (regulation, channel, supply chain, consumer shift).
That is the 'external momentum' the investment memos keep citing.

Two measures per industry (young = 국민연금 적용일 within max_age_years):
  formation_yoy  corps adopted in the last 6 months vs the same 6 months a year earlier
                 (YoY, so seasonality cancels)
  cohort_growth  head-count growth over 12 months of the corps present in both snapshots
                 (same firms, so it is hiring, not entry)
Hot = top `top_n` by the average percentile of the two, among industries
with at least `min_firms` young corps. Formation counts are smoothed with a
pseudo-count so tiny industries cannot win on 0→2. Purely a bonus signal.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .sources.nps_monthly import CYCLICAL_INDUSTRY, TEMP_SITE, _ym_minus

SKIP_INDUSTRY = {"BIZ_NO미존재사업장", "", "해당없음"}
SMOOTH = 5  # pseudo-count: an industry going 2→5 new firms should not outrank 40→60


def _shift(ymd: str, months: int) -> str:
    y, m = int(ymd[:4]), int(ymd[4:6])
    t = y * 12 + (m - 1) - months
    return f"{t // 12:04d}{t % 12 + 1:02d}{ymd[6:8] or '01'}"


def industry_momentum(yms: list[str], data: dict, *, today: dt.date, max_age_years: float = 5,
                      min_firms: int = 40, top_n: int = 15) -> list[dict]:
    if not yms:
        return []
    last = yms[-1]
    cur = data[last]
    year_ago = data.get(_ym_minus(last, 12))
    end = f"{last}31"
    win_now = (_shift(end, 6), end)                 # adopted in the last 6 months
    win_prev = (_shift(end, 18), _shift(end, 12))   # same window a year earlier
    cutoff = (today - dt.timedelta(days=int(max_age_years * 365.25))).strftime("%Y%m%d")

    st = defaultdict(lambda: {"firms": 0, "hc": 0, "new_now": 0, "new_prev": 0,
                              "coh_now": 0, "coh_then": 0})
    for key, r in cur.items():
        ind = r.get("induty_name") or ""
        if ind in SKIP_INDUSTRY or CYCLICAL_INDUSTRY.search(ind) or TEMP_SITE.search(r["name"]):
            continue
        a = r["adopt"]
        s = st[ind]
        if a >= cutoff:
            s["firms"] += 1
            s["hc"] += r["headcount"]
            if year_ago is not None and key in year_ago:
                s["coh_now"] += r["headcount"]
                s["coh_then"] += year_ago[key]["headcount"]
        if win_now[0] < a <= win_now[1]:
            s["new_now"] += 1
    # a year ago, measured on the year-ago snapshot (same ≥5명 storage rule, no survivor bias)
    for key, r in (year_ago or {}).items():
        ind = r.get("induty_name") or ""
        if ind in st and win_prev[0] < r["adopt"] <= win_prev[1] and not TEMP_SITE.search(r["name"]):
            st[ind]["new_prev"] += 1

    rows = []
    for ind, s in st.items():
        if s["firms"] < min_firms:
            continue
        rows.append({
            "industry": ind, "young_firms": s["firms"], "young_headcount": s["hc"],
            "new_6m": s["new_now"], "new_6m_year_ago": s["new_prev"],
            "formation_yoy": round((s["new_now"] + SMOOTH) / (s["new_prev"] + SMOOTH) - 1, 3),
            "cohort_growth_12m": round(s["coh_now"] / s["coh_then"] - 1, 3) if s["coh_then"] else None,
        })
    if not rows:
        return []

    def pct(vals):
        order = sorted(v for v in vals if v is not None)
        n = len(order)
        return lambda v: (sum(1 for x in order if x <= v) / n) if v is not None and n else 0.0

    pf = pct([r["formation_yoy"] for r in rows])
    pc = pct([r["cohort_growth_12m"] for r in rows])
    for r in rows:
        r["momentum"] = round((pf(r["formation_yoy"]) + pc(r["cohort_growth_12m"])) / 2, 3)
    rows.sort(key=lambda r: -r["momentum"])
    k = min(top_n, len(rows))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["hot"] = i < k
    return rows


def report_md(rows: list[dict], ym: str, top: int = 25) -> str:
    lines = [f"# 업종 모멘텀 ({ym} 국민연금 기준)", "",
             "젊은 법인(적용일 5년 이내)이 몰리고, 이미 있는 젊은 법인의 고용도 빨라지는 업종. "
             "신설 YoY = 최근 6개월 신규 가입 법인 수 / 1년 전 같은 기간, 코호트 고용 = 같은 회사들의 12개월 고용 증가.", "",
             "| 순위 | 업종 | 젊은 법인 | 신설 6개월 (1년 전) | 신설 YoY | 코호트 고용 12개월 | 점수 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows[:top]:
        cg = f"{r['cohort_growth_12m']:+.0%}" if r["cohort_growth_12m"] is not None else "-"
        mark = " 🔥" if r["hot"] else ""
        lines.append(f"| {r['rank']} | {r['industry']}{mark} | {r['young_firms']:,} | "
                     f"{r['new_6m']} ({r['new_6m_year_ago']}) | {r['formation_yoy']:+.0%} | {cg} | {r['momentum']:.2f} |")
    return "\n".join(lines) + "\n"
