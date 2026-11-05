import datetime as dt

from radar.sources import foodsafety as FS


def _rep(lcns, name, item, typ, d="20261001"):
    return {"LCNS_NO": lcns, "BSSH_NM": name, "PRDLST_NM": item, "PRDLST_DCNM": typ,
            "PRMS_DT": d, "INDUTY_CD_NM": "식품제조가공업"}


def test_client_brands_skip_sizes_and_own_name():
    names = ["유자오리엔탈드레싱(CJ푸드빌)", "갈릭소스(1kg)", "데리야끼소스(M)", "크림소스(해담식품)",
             "불고기소스(본죽)", "치즈소스(맘스터치)", "바질소스(냉동)", "로제소스(본죽)"]
    assert FS.client_brands(names, "주식회사 해담식품") == ["본죽", "CJ푸드빌", "맘스터치"]


def test_site_signals_sku_burst_and_haccp_only():
    reps = [_rep("2022051450995", "주식회사 다품종", f"소스{i}({b})", t)
            for i, (b, t) in enumerate([("본죽", "소스"), ("맘스터치", "드레싱"), ("CJ푸드빌", "복합조미식품")] * 6)]
    reps.append(_rep("2019000000001", "한가지만", "떡", "떡류"))
    hac = [{"LCNS_NO": "2023111111111", "BSSH_NM": "(주)새공장", "HACCP_APPN_DT": "20261020",
            "PRDLST_NM": "떡류", "INDUTY_CD_NM": "식품제조가공업"}]
    sig = {s["name"]: s for s in FS.site_signals(reps, hac, today=dt.date(2026, 11, 5),
                                                  min_new_items=15, min_types=3)}
    d = sig["주식회사 다품종"]
    assert d["sku_burst"] and d["new_items"] == 18 and d["item_types"] == 3
    assert set(d["client_brands"]) == {"본죽", "맘스터치", "CJ푸드빌"} and d["license_year"] == "2022"
    assert "한가지만" not in sig
    assert sig["(주)새공장"]["haccp_new"] == "20261020"


def test_first_haccp_ignores_added_items_cancelled_and_closed():
    rows = [{"LCNS_NO": "A", "HACCP_APPN_DT": "20200101"}, {"LCNS_NO": "A", "HACCP_APPN_DT": "20261001"},
            {"LCNS_NO": "B", "HACCP_APPN_DT": "20260901"}, {"LCNS_NO": "B", "HACCP_APPN_DT": "20261002"},
            {"LCNS_NO": "C", "HACCP_APPN_DT": "20260801", "ASGN_CANCL_DT": "20261001"},
            {"LCNS_NO": "D", "HACCP_APPN_DT": "20260801", "CLSBIZ_DT": "20261001"}]
    out = FS.first_haccp(rows, "20260509")
    assert [(h["LCNS_NO"], h["HACCP_APPN_DT"]) for h in out] == [("B", "20260901")]
