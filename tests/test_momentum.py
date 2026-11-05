import datetime as dt

from radar.momentum import industry_momentum


def _r(name, adopt, hc, ind):
    return {"name": name, "adopt": adopt, "headcount": hc, "induty_name": ind}


def test_hot_industry_needs_both_formation_and_hiring():
    now, ago = {}, {}
    for i in range(50):  # hot: more new firms than a year ago AND the cohort is hiring
        ago[f"a{i}"] = _r(f"a{i}", "20230101", 10, "뜨는업")
        now[f"a{i}"] = _r(f"a{i}", "20230101", 16, "뜨는업")
    for i in range(12):
        now[f"an{i}"] = _r(f"an{i}", "20260601", 6, "뜨는업")
    for i in range(3):
        ago[f"ap{i}"] = _r(f"ap{i}", "20250601", 6, "뜨는업")
    for i in range(50):  # flat: same formation, no hiring
        ago[f"b{i}"] = _r(f"b{i}", "20230101", 10, "평범업")
        now[f"b{i}"] = _r(f"b{i}", "20230101", 10, "평범업")
    for i in range(3):
        now[f"bn{i}"] = _r(f"bn{i}", "20260601", 6, "평범업")
        ago[f"bp{i}"] = _r(f"bp{i}", "20250601", 6, "평범업")
    rows = industry_momentum(["202509", "202609"], {"202509": ago, "202609": now},
                             today=dt.date(2026, 11, 5), min_firms=40, top_n=1)
    top = rows[0]
    assert top["industry"] == "뜨는업" and top["hot"]
    assert top["cohort_growth_12m"] == 0.6 and top["formation_yoy"] > 0.5
    assert not next(r for r in rows if r["industry"] == "평범업")["hot"]
