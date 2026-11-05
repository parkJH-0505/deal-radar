import datetime as dt

import yaml

from radar import conversion as CV
from radar.score import score


def _r(key, name, adopt, hc, sido="11", addr="서울특별시 성동구 성수이로", ind="떡류 제조업"):
    return {"key": key, "name": name, "adopt": adopt, "headcount": hc, "sido": sido,
            "addr": addr, "induty_name": ind}


def test_detects_sole_proprietor_turning_into_corporation():
    yms = ["202509", "202603", "202609"]
    corp = {
        "202509": {},
        "202603": {"123|떡공방": _r("123|떡공방", "주식회사 떡공방", "20260201", 18)},
        "202609": {"123|떡공방": _r("123|떡공방", "주식회사 떡공방", "20260201", 26),
                   # same name, different city and industry → not a match
                   "999|우리집": _r("999|우리집", "(주)우리집", "20260101", 12, sido="26", ind="커피 전문점",
                                  addr="부산광역시 해운대구 a")},
    }
    indiv = {
        "202509": {"555|떡공방": _r("555|떡공방", "떡공방", "20190315", 15),
                   "777|우리집": _r("777|우리집", "우리집", "20180101", 9)},
        "202603": {"555|떡공방": _r("555|떡공방", "떡공방", "20190315", 3),
                   "777|우리집": _r("777|우리집", "우리집", "20180101", 9)},
        "202609": {"777|우리집": _r("777|우리집", "우리집", "20180101", 9)},
    }
    conv = CV.detect(yms, corp, indiv)
    assert set(conv) == {"123|떡공방"}
    c = conv["123|떡공방"]
    assert c["indiv_adopt"] == "20190315" and c["indiv_peak_headcount"] == 15
    g = CV.combined_growth({"202603": 18, "202609": 26}, c["indiv_series"], "202609")
    assert g["growth_12m"] == round((26 - 15) / 15, 3)      # real growth, not "0 → 26"

    cfg = yaml.safe_load(open("config/theses.yaml", encoding="utf-8"))
    pts, why = score({"sources": ["nps"], "age_years": 7.6, "age_basis": "개인사업 시작(국민연금 적용일)",
                      "conversion": {k: v for k, v in c.items() if k != "indiv_series"},
                      "nps": {"headcount": 26, "growth_12m": g["growth_12m"]}}, cfg, dt.date(2026, 11, 5))
    assert any("법인 전환" in r for r in why)
    assert not any(r.startswith("업력") for r in why)        # 7.6년 실질 업력: 젊음 가점 없음
