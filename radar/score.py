"""Rule-based, reproducible scoring. Every point carries a reason string so the
judgment step (and the user) can see exactly why a company surfaced."""
from __future__ import annotations

import datetime as dt
import re


def age_years(est_dt: str | None, today: dt.date) -> float | None:
    if not est_dt or len(est_dt) < 8:
        return None
    try:
        d = dt.datetime.strptime(est_dt[:8], "%Y%m%d").date()
    except ValueError:
        return None
    return round((today - d).days / 365.25, 1)


def excluded(name: str, patterns: list[str]) -> bool:
    return any(re.search(p, name or "") for p in patterns)


def score(c: dict, cfg: dict, today: dt.date) -> tuple[int, list[str]]:
    w = cfg["weights"]
    pts, why = 0, []

    def add(k: str, reason: str):
        nonlocal pts
        v = w.get(k, 0)
        if v:
            pts += v
            why.append(f"{reason} ({v:+d})")

    src = set(c.get("sources", []))

    if c.get("dart_first_audit"):
        add("dart_first_audit", f"{c['dart_first_audit'][:4]}년 처음 감사보고서 제출")

    age = c.get("age_years")
    if age is not None:
        if age <= 3:
            add("age_le_3y", f"설립 {age}년")
        elif age <= cfg["global"]["max_company_age_years"]:
            add("age_le_6y", f"설립 {age}년")

    fin = c.get("financials") or {}
    rev, prev = fin.get("revenue"), fin.get("revenue_prev")
    if rev and rev >= 10_000_000_000:
        add("revenue_ge_100b_krw", f"매출 {rev / 1e8:,.0f}억(추정)")
    if rev and prev and prev > 0:
        g = (rev - prev) / prev
        if g >= 10:  # 10배+ 는 대개 전기가 설립 첫 부분연도 → 성장률로 쓰지 않는다
            why.append(f"매출 성장 {g:.0%} (전기 부분연도 추정, 점수 제외)")
        elif g >= 0.5:
            add("revenue_growth_ge_50pct", f"매출 성장 {g:.0%}")
        elif g >= 0.25:
            add("revenue_growth_ge_25pct", f"매출 성장 {g:.0%}")
    op = fin.get("op_income")
    if rev and op is not None and rev > 0:
        m = op / rev
        if m >= 0.10:
            add("op_margin_ge_10pct", f"영업이익률 {m:.0%}")
        elif m < 0:
            add("op_margin_negative", f"영업적자 {m:.0%}")

    for sig in c.get("localdata", []):
        if sig["kind"] == "expansion":
            add("localdata_expansion",
                f"{sig['industry']} 신규 인허가 {sig['new_sites']}건 (기존 사업장 보유, 증설)")
        elif sig["kind"] == "chain_rollout":
            add("localdata_chain_rollout",
                f"{sig['industry']} 같은 브랜드 신규 매장 {sig['new_sites']}곳 (체인 확장)")
        elif sig["kind"] == "multi_new":
            add("localdata_multi_new", f"{sig['industry']} 신규 공장급 인허가 {sig['new_sites']}건")

    nps = c.get("nps") or {}
    g12 = nps.get("growth_12m")
    if g12 is not None:
        if g12 >= 1.0:
            add("headcount_growth_12m_ge_100pct", f"고용 12개월 {g12:+.0%}")
        elif g12 >= 0.5:
            add("headcount_growth_12m_ge_50pct", f"고용 12개월 {g12:+.0%}")
        elif g12 >= 0.25:
            add("headcount_growth_12m_ge_25pct", f"고용 12개월 {g12:+.0%}")
    elif nps.get("growth_12m_from_zero") and (nps.get("headcount") or 0) >= 60:
        add("headcount_new_scaleup", f"12개월 전 미가입 → 현재 {nps['headcount']}명")
    if (nps.get("headcount") or 0) >= 30:
        add("headcount_ge_30", f"국민연금 가입자 {nps['headcount']}명")
    bpc = nps.get("bill_per_head_change_12m")
    if bpc is not None and bpc >= 0.10:
        add("bill_per_head_rising", f"1인당 고지금액 12개월 {bpc:+.0%}")

    # 설립 3년 이내인데 처음부터 큰 회사 = 대기업 분할·합작·사업양수 법인일 공산이 크다.
    if age is not None and age <= 3:
        big_rev = rev and rev >= 150_000_000_000
        transplanted = nps.get("growth_12m_from_zero") and (nps.get("headcount") or 0) >= 150
        if big_rev or transplanted:
            add("likely_spinoff", "설립 직후부터 대형(매출 1,500억+ 또는 0→150명+): 분할·합작 의심")

    induty = str(c.get("induty_code") or "")
    for th in cfg.get("theses", {}).values():
        if any(induty.startswith(p) for p in th.get("dart_industry_prefixes", [])):
            add("thesis_industry_match", f"테제 업종: {th['label']}")
            break

    if len(src) >= 2:  # discovery sources: dart, localdata, nps (panel scan)
        add("multi_source_bonus", "복수 소스 동시 포착: " + ", ".join(sorted(src)))

    return pts, why
