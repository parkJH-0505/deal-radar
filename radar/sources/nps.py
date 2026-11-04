"""국민연금 가입 사업장 내역 V2 (apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2).

Used for ENRICHMENT of candidates found elsewhere (DART / 인허가):
  search(name, bizno6) -> seq  ->  detail(seq): 가입자수, 당월고지금액
  period(seq, ym): 해당 월 신규취득/상실  -> 과거 인원 역산

Parameter spelling (camelCase vs snake_case) differs between V1 and V2 docs,
so the style is auto-detected once per run and cached.

Head-count back-calculation:  H[m-1] = H[m] - acquired[m] + lost[m]
"""
from __future__ import annotations

import datetime as dt

from ..entity import bizno6 as _b6, name_similarity
from ..http import HttpError, find_items, find_key, get, log, parse_body, pick, to_int

BASE = "https://apis.data.go.kr/B552015/NpsBplcInfoInqireServiceV2"

STYLES = {
    "camel": {"name": "wkplNm", "bizno": "bzowrRgstNo", "seq": "seq", "ym": "dataCrtYm"},
    "snake": {"name": "wkpl_nm", "bizno": "bzowr_rgst_no", "seq": "seq", "ym": "data_crt_ym"},
}


class Nps:
    def __init__(self, key: str):
        self.key = key
        self.style: str | None = None

    # -------------------------------------------------------------- low level
    def _q(self, op: str, **kw) -> list[dict]:
        style = STYLES[self.style or "camel"]
        params = {"serviceKey": self.key, "pageNo": 1, "numOfRows": kw.pop("rows", 100),
                  "dataType": "json", "type": "json"}
        for k, v in kw.items():
            if v not in (None, ""):
                params[style.get(k, k)] = v
        body = parse_body(get(f"{BASE}/{op}", params, pause=0.05))
        code = find_key(body, "resultCode")
        if code not in (None, "00", "0"):
            raise RuntimeError(f"NPS {op} {code}: {find_key(body, 'resultMsg')}")
        return find_items(body) or []

    def detect_style(self, probe_name: str = "삼성전자") -> str:
        for st in ("camel", "snake"):
            self.style = st
            try:
                items = self._q("getBassInfoSearchV2", name=probe_name, rows=5)
            except (HttpError, RuntimeError) as e:
                log(f"[nps] style {st} failed: {e}")
                continue
            if items and any(probe_name in str(pick(i, "wkplNm", default="")) for i in items):
                log(f"[nps] parameter style = {st}")
                return st
        self.style = "camel"
        log("[nps] could not confirm parameter style; defaulting to camel")
        return self.style

    # ------------------------------------------------------------- high level
    def find(self, name: str, bizno: str | None = None) -> dict | None:
        """Best matching workplace for a company name (+ optional bizno)."""
        b6 = _b6(bizno)
        try:
            items = self._q("getBassInfoSearchV2", name=_short(name), rows=100)
        except (HttpError, RuntimeError) as e:
            log(f"[nps] search '{name}' failed: {e}")
            return None
        best, best_s = None, 0.0
        for it in items:
            nm = pick(it, "wkplNm", default="")
            s = name_similarity(name, nm)
            ib = _b6(pick(it, "bzowrRgstNo", default=""))
            if b6 and ib:
                if ib != b6:
                    continue
                s += 0.3
            # prefer the latest snapshot / largest workplace
            if s > best_s:
                best, best_s = it, s
        if best is None or best_s < 0.85:
            return None
        return {
            "seq": pick(best, "seq"),
            "name": pick(best, "wkplNm"),
            "bizno6": _b6(pick(best, "bzowrRgstNo", default="")),
            "ym": pick(best, "dataCrtYm"),
            "match_score": round(best_s, 2),
            "raw": best,
        }

    def detail(self, seq) -> dict:
        items = self._q("getDetailInfoSearchV2", seq=seq, rows=1)
        it = items[0] if items else {}
        return {
            "headcount": to_int(pick(it, "jnngpCnt")),
            "monthly_bill": to_int(pick(it, "crrmmNtcAmt")),
            "industry": pick(it, "vldtVlKrnNm", "wkplIntpNm"),
            "adopted": pick(it, "adptDt"),
            "addr": pick(it, "wkplRoadNmDtlAddr"),
        }

    def headcount_series(self, seq, current: int | None, months: int = 12,
                         end_ym: str | None = None) -> list[dict]:
        """Back out monthly head-count from 취득/상실 flows."""
        if current is None:
            return []
        ym = end_ym or (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y%m")
        series = [{"ym": ym, "headcount": current}]
        h = current
        for _ in range(months):
            try:
                items = self._q("getPdAcctoSttusInfoSearchV2", seq=seq, ym=ym, rows=1)
            except (HttpError, RuntimeError):
                break
            if not items:
                break
            it = items[0]
            acq = to_int(pick(it, "nwAcqzrCnt")) or 0
            lost = to_int(pick(it, "lssJnngpCnt")) or 0
            h = h - acq + lost
            ym = _prev_ym(ym)
            series.append({"ym": ym, "headcount": max(h, 0), "acq": acq, "lost": lost})
        return series

    def enrich(self, name: str, bizno: str | None = None, months: int = 12) -> dict | None:
        m = self.find(name, bizno)
        if not m:
            return None
        try:
            d = self.detail(m["seq"])
        except (HttpError, RuntimeError) as e:
            log(f"[nps] detail failed for {name}: {e}")
            return {"match": {k: v for k, v in m.items() if k != "raw"}}
        series = self.headcount_series(m["seq"], d["headcount"], months, m.get("ym"))
        return {"match": {k: v for k, v in m.items() if k != "raw"}, **d,
                "series": series, **growth_metrics(series, d)}


def growth_metrics(series: list[dict], d: dict) -> dict:
    out: dict = {}
    if not series:
        return out
    now = series[0]["headcount"] or 0
    by_lag = {i: s["headcount"] for i, s in enumerate(series)}
    for lag in (3, 6, 12):
        past = by_lag.get(lag)
        if past:
            out[f"growth_{lag}m"] = round((now - past) / past, 3)
    if d.get("monthly_bill") and now:
        # 1인당 월 고지금액 (보험료 → 보수 수준의 대리지표; 상한 존재)
        out["bill_per_head"] = round(d["monthly_bill"] / now)
    return out


def _short(name: str) -> str:
    from ..entity import norm_name
    n = norm_name(name, strip_branch=False)
    return n[:20] if n else name


def _prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:6])
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}{m:02d}"
