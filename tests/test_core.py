import datetime as dt

import yaml

from radar.entity import bizno6, name_similarity, norm_name, sido
from radar.http import find_items, find_key, pick
from radar.score import age_years, score
from radar.sources import localdata as L
from radar.sources.dart import SeenIndex, extract_financials, month_windows
from radar.sources.nps import growth_metrics
from radar.store import ROOT

CFG = yaml.safe_load((ROOT / "config" / "theses.yaml").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 11, 5)


def test_norm_name():
    assert norm_name("(주)맛있는공장") == "맛있는공장"
    assert norm_name("주식회사 해담식품 제2공장") == "해담식품"
    assert norm_name("해담식품 음성공장") == "해담식품"
    assert norm_name("맛있는공장") == "맛있는공장"  # brand, not a branch
    assert norm_name("농업회사법인 ㈜해담") == "해담"
    assert name_similarity("(주)키이스케이프", "주식회사 키이스케이프") == 1.0
    assert bizno6("123-45-67890") == "123456"
    assert sido("경기도 화성시 향남읍") == "경기"
    assert sido("전북특별자치도 익산시") == "전북"


def test_find_items_envelopes():
    a = {"response": {"header": {"resultCode": "00"},
                      "body": {"items": {"item": [{"a": 1}, {"a": 2}]}, "totalCount": 2}}}
    assert find_items(a) == [{"a": 1}, {"a": 2}]
    assert find_key(a, "totalCount") == 2
    single = {"response": {"body": {"items": {"item": {"a": 1}}}}}
    assert find_items(single) == [{"a": 1}]
    assert pick({"BPLC_NM": "x"}, "bplcNm") == "x"


def test_localdata_expansion():
    rows = [
        {"BPLC_NM": "(주)해담식품", "LCPMT_YMD": "2022-03-02", "SALS_STTS_NM": "영업",
         "ROAD_NM_ADDR": "경기도 화성시 a"},
        {"BPLC_NM": "주식회사 해담식품 제2공장", "LCPMT_YMD": "2026-08-10",
         "SALS_STTS_NM": "영업", "ROAD_NM_ADDR": "충청북도 음성군 b"},
        {"BPLC_NM": "혼자신규", "LCPMT_YMD": "2026-09-01", "SALS_STTS_NM": "영업"},
        {"BPLC_NM": "폐업회사", "LCPMT_YMD": "2026-09-01", "SALS_STTS_NM": "폐업"},
    ]
    sig = L.expansion_signals([L.normalize(r) for r in rows], today=TODAY,
                              window_days=180, industry_label="식품제조가공업")
    assert len(sig) == 1 and sig[0]["kind"] == "expansion"
    assert sig[0]["name_norm"] == "해담식품" and sig[0]["sido"] == ["경기", "충북"]


def test_extract_financials():
    txt = """
    포 괄 손 익 계 산 서
    제 5 기 2025년 1월 1일부터 (단위 : 원)
    과 목 | 주석 | 제 5(당)기 | 제 4(전)기
    매출액 | 22 | 31,250,000,000 | 18,400,000,000
    매출원가 | | 24,000,000,000 | 14,000,000,000
    영업이익 | | 3,600,000,000 | 1,100,000,000
    당기순이익 | | 2,900,000,000 | 800,000,000
    """
    f = extract_financials(txt)
    assert f["revenue"] == 31_250_000_000 and f["revenue_prev"] == 18_400_000_000
    assert f["op_income"] == 3_600_000_000
    f2 = extract_financials("손익계산서 (단위: 천원)\n매출액 | 12,500,000 | 9,000,000\n영업손실 | (300,000) | 100,000\n")
    assert f2["revenue"] == 12_500_000_000 and f2["op_income"] == -300_000_000


def test_seen_index_roundtrip(tmp_path):
    p = tmp_path / "seen.tsv"
    ix = SeenIndex(p)
    assert ix.add({"corp_code": "001", "rcept_dt": "20250401", "rcept_no": "a", "corp_name": "가"})
    assert not ix.add({"corp_code": "001", "rcept_dt": "20260331", "rcept_no": "b", "corp_name": "가"})
    ix.save()
    ix2 = SeenIndex(p)
    assert ix2.first["001"] == "20250401" and ix2.last["001"] == ("20260331", "b")
    w = list(month_windows(dt.date(2026, 1, 15), dt.date(2026, 3, 2)))
    assert w[0] == (dt.date(2026, 1, 15), dt.date(2026, 1, 31)) and w[-1][1] == dt.date(2026, 3, 2)


def test_growth_and_score():
    series = [{"ym": f"m{i}", "headcount": h} for i, h in
              enumerate([90, 85, 80, 70, 66, 62, 60, 58, 55, 52, 50, 48, 45])]
    g = growth_metrics(series, {"monthly_bill": 90 * 400_000})
    assert g["growth_12m"] == 1.0 and g["bill_per_head"] == 400_000
    c = {"sources": ["dart", "localdata"], "dart_first_audit": "20260331",
         "age_years": age_years("20210610", TODAY), "induty_code": "10799",
         "financials": {"revenue": 31e9, "revenue_prev": 18e9, "op_income": 3.6e9},
         "localdata": [{"kind": "expansion", "industry": "식품제조가공업", "new_sites": 1}],
         "nps": {"headcount": 90, **g}}
    pts, why = score(c, CFG, TODAY)
    assert pts >= 100, (pts, why)
    assert any("처음 감사보고서" in w for w in why)
