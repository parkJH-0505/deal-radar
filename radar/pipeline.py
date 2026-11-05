"""Daily pipeline: collect -> resolve -> score -> write candidates.

Outputs (committed by the GitHub Action):
  data/candidates/YYYY-MM-DD.json   today's ranked candidates (with reasons)
  data/candidates/latest.json       same, stable path for the judgment step
  data/watchlist.json               every company ever surfaced + score history
  data/runs/YYYY-MM-DD.md           human-readable run log
"""
from __future__ import annotations

import datetime as dt
import re
import os
import time
from pathlib import Path

import yaml

from .entity import bizno6, name_similarity, norm_name
from .http import HttpError, log
from . import conversion as CV
from .sources import foodsafety as FS
from . import momentum as MO
from .score import age_years, excluded, score
from .sources import dart as D
from .sources import localdata as L
from .sources import nps_monthly as NM
from .sources.nps import Nps
from .store import DATA, ROOT, STATE, JsonlCache, read_json, write_json

KST = dt.timezone(dt.timedelta(hours=9))


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "theses.yaml").read_text(encoding="utf-8"))


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


# ======================================================================= DART

def run_dart(key: str, cfg: dict, today: dt.date, meta: dict, notes: list[str]) -> list[dict]:
    dc = cfg["sources"]["dart"]
    index = D.SeenIndex(STATE / "dart_audit_seen.tsv")
    start = dt.date.fromisoformat(dc["backfill_start"])
    yesterday = today - dt.timedelta(days=1)

    if len(index) == 0:
        log("[dart] empty index -> full backfill")
        n = D.backfill(key, index, start, yesterday)
        notes.append(f"DART 감사보고서 이력 백필: {start}~{yesterday}, {n:,}건 / 회사 {len(index):,}곳")
    else:
        since = dt.date.fromisoformat(meta.get("dart_last_day", str(yesterday - dt.timedelta(days=7))))
        since = min(since, yesterday) - dt.timedelta(days=3)  # late-filing overlap
        n = D.backfill(key, index, since, yesterday)
        notes.append(f"DART 증분: {since}~{yesterday}, 감사보고서 {n:,}건")
    index.save()
    meta["dart_last_day"] = str(yesterday)

    reliable_from = (start + dt.timedelta(days=365)).strftime("%Y%m%d")
    horizon = (today - dt.timedelta(days=dc["first_audit_horizon_days"])).strftime("%Y%m%d")
    floor = max(reliable_from, horizon)
    first_timers = [c for c, d in index.first.items() if d >= floor]
    log(f"[dart] first-time auditees since {floor}: {len(first_timers)}")

    companies = JsonlCache(STATE / "dart_companies.jsonl", "corp_code")
    budget = dc["max_company_lookups_per_run"]
    pending = [c for c in first_timers if c not in companies]
    for code in pending[:budget]:
        try:
            info = D.company(key, code)
        except D.DartError as e:
            log(f"[dart] stop company lookups: {e}")
            break
        except HttpError as e:
            log(f"[dart] company {code} failed: {e}")
            continue
        keep = {k: (info or {}).get(k) for k in
                ("corp_code", "corp_name", "est_dt", "induty_code", "bizr_no", "jurir_no",
                 "adres", "hm_url", "ceo_nm", "acc_mt", "corp_cls")}
        keep["corp_code"] = code
        companies.put(keep)
        if len(companies._new) % 200 == 0:
            companies.flush()
    companies.flush()
    left = max(0, len(pending) - budget)
    if left:
        notes.append(f"DART 회사정보 조회 남은 {left:,}곳은 다음 실행으로 이월")

    pats = cfg["global"]["exclude_name_patterns"]
    max_age = cfg["global"]["max_company_age_years"]
    ind_x = tuple(cfg["global"].get("exclude_industry_prefixes", []))
    fins = JsonlCache(STATE / "dart_financials.jsonl", "rcept_no")
    out, docs = [], 0
    for code in first_timers:
        info = companies.get(code)
        if not info:
            continue
        name = info.get("corp_name") or index.names.get(code, "")
        age = age_years(info.get("est_dt"), today)
        if age is None or age > max_age:
            continue
        if excluded(name, pats) or excluded(index.names.get(code, ""), pats):
            continue
        if str(info.get("induty_code") or "").startswith(ind_x):
            continue
        last_dt, last_no = index.last.get(code, ("", ""))
        fin = None
        if last_no:
            cached = fins.get(last_no)
            if cached is not None and cached.get("v") == D.FIN_PARSER_VERSION:
                fin = cached.get("fin")
            elif docs < dc["max_documents_per_run"]:
                text = D.audit_text(key, last_no)
                fin = D.extract_financials(text)
                if fin is not None:
                    fin["excerpt"] = D.income_excerpt(text)
                fins.put({"rcept_no": last_no, "corp_code": code, "fin": fin,
                          "v": D.FIN_PARSER_VERSION})
                docs += 1
                if docs % 50 == 0:
                    fins.flush()
        out.append({
            "key": f"dart:{code}",
            "name": name,
            "name_norm": norm_name(name),
            "sources": ["dart"],
            "corp_code": code,
            "bizno": info.get("bizr_no"),
            "est_dt": info.get("est_dt"),
            "age_years": age,
            "age_basis": "설립일(DART)",
            "induty_code": info.get("induty_code"),
            "ceo": info.get("ceo_nm"),
            "addr": info.get("adres"),
            "homepage": info.get("hm_url"),
            "dart_first_audit": index.first[code],
            "latest_audit": {"rcept_dt": last_dt, "rcept_no": last_no,
                             "url": D.VIEW.format(rcept_no=last_no) if last_no else None},
            "financials": fin or {},
        })
    fins.flush()
    notes.append(f"DART 후보(설립 {max_age}년 이하·첫 외감): {len(out)}곳, 감사보고서 신규 파싱 {docs}건")
    return out


# ================================================================== LOCALDATA

def run_localdata(key: str, cfg: dict, today: dt.date, notes: list[str],
                  meta: dict | None = None) -> list[dict]:
    lc = cfg["sources"]["localdata"]
    meta = meta if meta is not None else {}
    synced = meta.setdefault("localdata_synced", {})
    sigs = []
    for ind in lc["industries"]:
        slug = ind["slug"]
        last = synced.get(slug)
        since = str(dt.date.fromisoformat(last) - dt.timedelta(days=3)) if last else None
        try:
            rows, mode = L.sync(key, slug, STATE / f"localdata_{slug}.jsonl.gz", since)
        except (HttpError, RuntimeError) as e:
            notes.append(f"인허가 {ind['label']} 수집 실패: {e}")
            continue
        synced[slug] = today.isoformat()
        s = L.expansion_signals(rows, today=today, window_days=lc["window_days"],
                                industry_label=ind["label"])
        for x in s:
            x["thesis"] = ind.get("thesis")
        sigs.extend(s)
        notes.append(f"인허가 {ind['label']}: {mode} → 보유 {len(rows):,}건, 증설·다거점 신호 {len(s)}건")
    return sigs


# ====================================================================== MERGE

def merge(dart_cands: list[dict], loc_sigs: list[dict], cfg: dict, today: dt.date) -> list[dict]:
    by_norm = {c["name_norm"]: c for c in dart_cands if c["name_norm"]}
    out = list(dart_cands)
    max_age = cfg["global"]["max_company_age_years"]
    pats = cfg["global"]["exclude_name_patterns"]
    for s in loc_sigs:
        hit = by_norm.get(s["name_norm"])
        if not hit:
            for c in dart_cands:
                if name_similarity(c["name"], s["name"]) >= 0.92:
                    hit = c
                    break
        if hit:
            hit.setdefault("localdata", []).append(s)
            if "localdata" not in hit["sources"]:
                hit["sources"].append("localdata")
            continue
        if excluded(s["name"], pats):
            continue
        age = age_years(s.get("first_permit"), today)
        if age is not None and age > max_age:
            continue  # established operator expanding: not our target profile
        out.append({
            "key": f"loc:{s['name_norm']}",
            "name": s["name"],
            "name_norm": s["name_norm"],
            "sources": ["localdata"],
            "age_years": age,
            "age_basis": "첫 인허가일(대리지표)",
            "addr": (s.get("new_permits") or [{}])[0].get("addr"),
            "localdata": [s],
            "financials": {},
        })
    return out


# ================================================================ NPS MONTHLY

def run_nps_monthly(cfg: dict, today: dt.date, notes: list[str]):
    nm = cfg["sources"]["nps_monthly"]
    months_dir = DATA / "nps" / "months"
    adopt_after = (today - dt.timedelta(days=int(nm["store_max_age_years"] * 365.25))).strftime("%Y%m%d")
    notes += NM.update_archive(months_dir, want_months=nm["want_months"],
                               max_downloads=nm["max_downloads_per_run"], adopt_after=adopt_after,
                               min_headcount=nm["store_min_headcount"],
                               exclude=cfg["global"]["exclude_name_patterns"])
    yms, data = NM.build_panel(months_dir)
    if not yms:
        return yms, data, []
    sc = nm["scan"]
    found = NM.scan(yms, data, today=today, max_age_years=cfg["global"]["max_company_age_years"],
                    min_headcount=sc["min_headcount"], min_growth_12m=sc["min_growth_12m"],
                    min_growth_6m=sc["min_growth_6m"])
    notes.append(f"국민연금 패널 {yms[0]}~{yms[-1]} ({len(yms)}개월), 고용 급성장 후보 {len(found)}곳")
    return yms, data, found


class PanelIndex:
    """Find a company's panel row from (bizno, name)."""

    def __init__(self, yms, data):
        self.yms, self.data = yms, data
        self.by_b6: dict[str, list[str]] = {}
        self.by_norm: dict[str, list[str]] = {}
        if yms:
            for key in data[yms[-1]]:
                b6, nn = key.split("|", 1)
                self.by_b6.setdefault(b6, []).append(key)
                self.by_norm.setdefault(nn, []).append(key)

    def lookup(self, name: str, bizno: str | None = None) -> str | None:
        b6 = bizno6(bizno)
        if b6:
            best = max(((name_similarity(name, k.split("|", 1)[1]), k) for k in self.by_b6.get(b6, [])),
                       default=(0, None))
            if best[0] >= 0.85:
                return best[1]
        keys = self.by_norm.get(norm_name(name), [])
        return keys[0] if len(keys) == 1 else None  # ambiguous names: no guess

    def metrics(self, key: str) -> dict | None:
        return NM.metrics_for(key, self.yms, self.data)


def merge_nps(cands: list[dict], found: list[dict], pidx: PanelIndex, cfg: dict,
              today: dt.date) -> list[dict]:
    # 1) attach panel metrics to DART / 인허가 candidates
    claimed = set()
    for c in cands:
        k = pidx.lookup(c["name"], c.get("bizno"))
        if k:
            m = pidx.metrics(k)
            if m:
                c["nps"] = m
                c["nps_key"] = k
                claimed.add(k)
    # 2) add NPS-only growth candidates (or mark existing ones as multi-source)
    by_key = {c.get("nps_key"): c for c in cands if c.get("nps_key")}
    pats = cfg["global"]["exclude_name_patterns"]
    for f in found:
        k = f.get("panel_key") or f["key"][4:]
        if k in by_key:
            if "nps" not in by_key[k]["sources"]:
                by_key[k]["sources"].append("nps")
            continue
        if excluded(f["name"], pats) or str(f.get("induty_code")) == "999999":
            continue
        adopt = f["nps"].get("adopt")
        a = age_years(adopt, today)
        if a is None or a > cfg["global"]["max_company_age_years"]:
            continue
        cands.append({
            "key": f["key"], "name": f["name"], "name_norm": norm_name(f["name"]),
            "sources": ["nps"], "bizno6": f["bizno6"],
            "age_years": a, "age_basis": "국민연금 적용일(대리지표)",
            "addr": f["addr"], "industry": f["induty_name"], "induty_code": f["induty_code"],
            "nps": f["nps"], "nps_key": k,
            "financials": {},
        })
    return cands


# ======================================================================= FOOD

def merge_food(cands, key, fc, cfg, today, notes) -> list[dict]:
    sig, note = FS.run(key, haccp_key=os.environ.get("FOODSAFETY_KEY_HACCP", "").strip(), today=today, window_days=fc.get("window_days", 180),
                       min_new_items=fc.get("min_new_items", 15), min_types=fc.get("min_types", 3))
    notes.append(note)
    by_norm = {}
    for c in cands:
        if c.get("name_norm"):
            by_norm.setdefault(c["name_norm"], c)
    pats = cfg["global"]["exclude_name_patterns"]
    max_age = cfg["global"]["max_company_age_years"]
    added = 0
    for s in sig:
        hit = by_norm.get(s["name_norm"])
        if hit:
            hit["food"] = s
            if "food" not in hit["sources"]:
                hit["sources"].append("food")
            continue
        if not (s["sku_burst"] or len(s["client_brands"]) >= 3 or s["haccp_new"]):
            continue
        if excluded(s["name"], pats):
            continue
        age = age_years(f"{s['license_year']}0701", today) if s["license_year"] else None
        if age is not None and age > max_age:
            continue
        cands.append({"key": f"food:{s['lcns_no']}", "name": s["name"], "name_norm": s["name_norm"],
                      "sources": ["food"], "age_years": age, "age_basis": "식품 인허가 연도(대리지표)",
                      "industry": "식품제조가공업", "induty_code": "10", "food": s, "financials": {}})
        by_norm[s["name_norm"]] = cands[-1]
        added += 1
    notes.append(f"식품안전나라 단독 후보 {added}곳")
    return cands


# ================================================================= CONVERSION

def attach_conversions(cands, yms, pdata, cfg, today, notes) -> list[dict]:
    """개인사업자 → 법인 전환: real start date + growth on the combined head-count."""
    if not yms:
        return cands
    indiv = NM.build_indiv(DATA / "nps" / "indiv")
    if not indiv:
        notes.append("법인 전환 감지: 개인사업장 이력 없음 (다음 국민연금 백필 후 활성)")
        return cands
    cc = cfg.get("conversion", {})
    conv = CV.detect(yms, pdata, indiv, lookback_months=cc.get("lookback_months", 24))
    last = yms[-1]

    def apply(c: dict, ck: str) -> None:
        info = conv[ck]
        n = c.setdefault("nps", NM.metrics_for(ck, yms, pdata) or {})
        corp = {p["ym"]: p["headcount"] for p in n.get("series", [])}
        g = CV.combined_growth(corp, info["indiv_series"], last)
        for k in ("growth_6m", "growth_12m"):
            if k in g:
                n[k] = g[k]
        for k in ("growth_12m_from_zero", "growth_6m_from_zero", "max_jump_share"):
            n.pop(k, None)  # the jump is the transfer from the 개인 site, not hiring
        n["combined_headcount_12m_ago"] = g["combined_headcount_12m_ago"]
        c["conversion"] = {k: v for k, v in info.items() if k != "indiv_series"}
        c["age_years"] = age_years(info["indiv_adopt"], today)
        c["age_basis"] = "개인사업 시작(국민연금 적용일)"

    by_key = {c.get("nps_key"): c for c in cands if c.get("nps_key")}
    pats = cfg["global"]["exclude_name_patterns"]
    added = 0
    for ck in conv:
        if ck in by_key:
            apply(by_key[ck], ck)
            continue
        r = pdata[last][ck]
        if excluded(r["name"], pats) or str(r.get("induty_code")) == "999999" \
                or (r.get("induty_name") or "") in ("해당없음", "") \
                or NM.CYCLICAL_INDUSTRY.search(r.get("induty_name") or ""):
            continue
        c = {"key": "nps:" + ck.replace("|", ":"), "name": r["name"], "name_norm": norm_name(r["name"]),
             "sources": ["nps"], "bizno6": r["bizno6"], "addr": r["addr"],
             "industry": r["induty_name"], "induty_code": r["induty_code"],
             "nps_key": ck, "financials": {}}
        apply(c, ck)
        g12 = c["nps"].get("growth_12m")
        if (c["nps"].get("headcount") or 0) >= cc.get("min_headcount", 10) and \
                (g12 is None or g12 >= cc.get("min_combined_growth_12m", 0.3)):
            cands.append(c)
            added += 1
    notes.append(f"법인 전환 감지: 최근 {cc.get('lookback_months', 24)}개월 전환 {len(conv)}곳, "
                 f"신규 후보 {added}곳")
    return cands


# =================================================================== MOMENTUM

def attach_momentum(cands, yms, pdata, cfg, today, notes) -> None:
    mc = cfg.get("momentum", {})
    rows = MO.industry_momentum(yms, pdata, today=today,
                                max_age_years=cfg["global"]["max_company_age_years"],
                                min_firms=mc.get("min_firms", 40), top_n=mc.get("top_n", 15))
    out = DATA / "momentum"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.md").write_text(MO.report_md(rows, yms[-1]), encoding="utf-8")
    write_json(out / "latest.json", {"ym": yms[-1], "rows": rows})
    hot = {r["industry"]: r for r in rows if r["hot"]}
    n = 0
    for c in cands:
        ind = c.get("industry") or (c.get("nps") or {}).get("industry")
        if ind in hot:
            r = hot[ind]
            c["industry_momentum"] = {k: r[k] for k in ("industry", "rank", "formation_yoy",
                                                        "cohort_growth_12m", "young_firms")}
            n += 1
    notes.append(f"업종 모멘텀 {yms[-1]}: 상위 {len(hot)}개 업종, 해당 후보 {n}곳 "
                 f"(1위 {rows[0]['industry'] if rows else '-'})")


# ================================================================= JUDGMENTS

_KEY = re.compile(r"\|\s*((?:dart|nps|loc|food):[^\s|]+)\s*\|")
_GRADE_CELL = re.compile(r"^\s*([A-F])\s*(?:급)?\s*$")
_GRADE_HEAD = re.compile(r"^([A-F])\s*급?(?:\s|$|[·(:-])")
GRADES = "ABCDEF"
CANDIDATE_GRADES = "AB"  # 시트 '후보'에 올라가는 등급


def _grade_of(line: str, section: str) -> str:
    """Grade from a '등급' cell (A–F) in the row, else from an 'A급…' heading;
    legacy files: '생존' → B, '탈락' → D."""
    for cell in line.strip().strip("|").split("|")[1:]:
        m = _GRADE_CELL.match(cell)
        if m:
            return m.group(1)
    m = _GRADE_HEAD.match(section)
    if m:
        return m.group(1)
    return "D" if "탈락" in section else "B"


def load_judgments(folder) -> dict[str, dict]:
    """Keys the morning judgment already reviewed, from data/judgments/YYYY-MM-DD.md.
    Each row carries a grade A–F (A·B = 후보, C–F = 보류·탈락); the latest file wins."""
    out: dict[str, dict] = {}
    if not folder.exists():
        return out
    for f in sorted(folder.glob("*.md")):
        date, section = f.stem[:10], ""
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            m = _KEY.match(line.strip())
            if m:
                g = _grade_of(line, section)
                out[m.group(1)] = {"date": date, "grade": g,
                                   "verdict": "keep" if g in CANDIDATE_GRADES else "reject"}
    return out


# ======================================================================= MAIN

def daily(today: dt.date | None = None) -> dict:
    t0 = time.time()
    today = today or today_kst()
    cfg = load_cfg()
    meta_path = STATE / "meta.json"
    meta = read_json(meta_path, {})
    notes: list[str] = []
    gkey = os.environ.get("DATA_GO_KR_KEY", "").strip()
    dkey = os.environ.get("DART_KEY", "").strip()

    dart_c: list[dict] = []
    if cfg["sources"]["dart"]["enabled"] and dkey:
        try:
            dart_c = run_dart(dkey, cfg, today, meta, notes)
        except (D.DartError, HttpError) as e:
            notes.append(f"DART 단계 중단: {e}")
    elif not dkey:
        notes.append("DART_KEY 미설정 → DART 단계 건너뜀")

    loc: list[dict] = []
    if cfg["sources"]["localdata"]["enabled"] and gkey:
        loc = run_localdata(gkey, cfg, today, notes, meta)
    elif not gkey:
        notes.append("DATA_GO_KR_KEY 미설정 → 인허가·국민연금 건너뜀")

    cands = merge(dart_c, loc, cfg, today)

    # 식품안전나라: 다품종(신규 품목 속도), OEM 고객 브랜드, HACCP 신규 지정
    fkey = os.environ.get("FOODSAFETY_KEY", "").strip()
    fc = cfg["sources"].get("foodsafety", {})
    if fc.get("enabled") and fkey:
        cands = merge_food(cands, fkey, fc, cfg, today, notes)
    elif fc.get("enabled"):
        notes.append("FOODSAFETY_KEY 미설정 → 식품안전나라(품목제조보고·HACCP) 건너뜀")

    # 국민연금 월간 패널: discovery (고용 급성장) + head-count series for everyone
    if cfg["sources"]["nps_monthly"]["enabled"]:
        try:
            yms, pdata, found = run_nps_monthly(cfg, today, notes)
            cands = merge_nps(cands, found, PanelIndex(yms, pdata), cfg, today)
        except Exception as e:  # noqa: BLE001 - a portal change must not kill the run
            notes.append(f"국민연금 월간 단계 오류: {type(e).__name__}: {e}")
        else:
            try:
                cands = attach_conversions(cands, yms, pdata, cfg, today, notes)
            except Exception as e:  # noqa: BLE001 - bonus signal only
                notes.append(f"법인 전환 감지 오류: {type(e).__name__}: {e}")
            try:
                attach_momentum(cands, yms, pdata, cfg, today, notes)
            except Exception as e:  # noqa: BLE001 - bonus signal only
                notes.append(f"업종 모멘텀 계산 오류: {type(e).__name__}: {e}")

    for c in cands:
        c["score"], c["reasons"] = score(c, cfg, today)

    # NPS API fallback (current head-count only) for candidates not in the panel
    nc = cfg["sources"]["nps"]
    if nc["enabled"] and gkey and any("nps" not in c for c in cands):
        nps = Nps(gkey)
        nps.style = meta.get("nps_style") or nps.detect_style()
        meta["nps_style"] = nps.style
        cache = read_json(STATE / "nps_cache.json", {})
        ranked = sorted([c for c in cands if "nps" not in c], key=lambda c: -c["score"])
        looked = 0
        for c in ranked:
            ck = c["key"]
            hit = cache.get(ck)
            fresh = hit and hit.get("_asof", "")[:7] == today.strftime("%Y-%m")
            if not fresh:
                if looked >= nc["max_lookups_per_run"]:
                    continue
                e = nps.enrich(c["name"], c.get("bizno"), nc["months"])
                looked += 1
                cache[ck] = {**(e or {}), "_asof": today.isoformat()}
                hit = cache[ck]
            if hit and hit.get("headcount") is not None:
                c["nps"] = {k: v for k, v in hit.items() if k != "_asof"}
                c["score"], c["reasons"] = score(c, cfg, today)
        write_json(STATE / "nps_cache.json", cache)
        notes.append(f"국민연금 조회 {looked}곳 (파라미터 방식: {nps.style})")

    g = cfg["global"]
    def added(c):  # 동점 해소: 12개월 동안 실제로 늘어난 인원 (작은 비율 놀음 방지)
        n = c.get("nps") or {}
        hc, g12 = n.get("headcount") or 0, n.get("growth_12m")
        return hc - hc / (1 + g12) if g12 and g12 > -1 else 0
    picked = sorted([c for c in cands if c["score"] >= g["min_score_to_report"]],
                    key=lambda c: (-c["score"], -added(c)))

    # watchlist bookkeeping: new vs changed
    wl_path = DATA / "watchlist.json"
    wl = read_json(wl_path, {})
    reviewed = load_judgments(DATA / "judgments")
    resurface = {"C": 8, "D": 15, "E": 25, "F": None, **(g.get("grade_resurface") or {})}

    def kinds(reasons):  # "고용 12개월 +55% (+20)" -> "고용 개월 % ()": numbers drift daily
        return {re.sub(r"[\d,.+\-]+", "", r) for r in reasons}

    for c in picked:
        prev = wl.get(c["key"])
        # baseline = last score recorded BEFORE today, so same-day re-runs don't
        # swallow today's new candidates
        before = [h for h in (prev or {}).get("score_history", []) if h["date"] < today.isoformat()]
        # 판단 이력(data/judgments)이 진실의 원천이다.
        #  - 아직 판단받지 않은 회사 = new (임계점 넘은 대기 후보는 점수순으로 매일 소진)
        #  - C~E 등급 = 점수가 판단 당시보다 등급별 기준(C 8 · D 15 · E 25) 이상 오를 때만 재상정
        #  - F 등급 = 결격 확정, 다시 올리지 않음
        #  - A·B 등급 = 신호가 새로 생기거나 10점 이상 오르면 changed
        verdict = reviewed.get(c["key"])
        base = before[-1]["score"] if before else c["score"]
        if verdict:
            c["prev_grade"] = verdict["grade"]
            c["prev_judged"] = verdict["date"]
        if verdict is None:
            c["change"] = "new"
        elif verdict["verdict"] == "reject":
            need = resurface.get(verdict["grade"])
            jscore = next((h["score"] for h in reversed((prev or {}).get("score_history", []))
                           if h["date"] <= verdict["date"]), base)
            c["change"] = "changed" if need is not None and c["score"] - jscore >= need and \
                verdict["date"] < today.isoformat() else "rejected"
        elif not before:
            c["change"] = "same"
        elif c["score"] - base >= 10 or \
                kinds(c["reasons"]) - kinds(prev.get("reasons_before", prev.get("reasons", []))):
            c["change"] = "changed"
        else:
            c["change"] = "same"
        rec = prev or {"name": c["name"], "first_seen": today.isoformat(),
                       "score_history": [], "user_status": None}
        if rec.get("last_seen") and rec["last_seen"] < today.isoformat():
            rec["reasons_before"] = rec.get("reasons", [])
        rec.update(name=c["name"], last_seen=today.isoformat(), reasons=c["reasons"])
        if not rec["score_history"] or rec["score_history"][-1]["date"] != today.isoformat():
            rec["score_history"].append({"date": today.isoformat(), "score": c["score"]})
        wl[c["key"]] = rec
    write_json(wl_path, wl)

    # judgment queue: new/changed first, capped
    fresh = [c for c in picked if c["change"] in ("new", "changed")]
    # 소스별 최소 자리 보장: DART 첫 외감 후보가 점수 구조상 상위를 독식하므로
    # 국민연금 단독·인허가 단독 발견도 매일 판단 단계에 오르게 한다.
    cap = g["max_candidates_per_day"]
    quota = g.get("queue_quota", {})
    def lane(c):
        src = set(c.get("sources", []))
        if c.get("conversion"):
            return "conversion"
        if "dart" in src:
            return "dart"
        if "localdata" in src:
            return "localdata"
        return "food" if "food" in src else "nps"
    queue, used = [], set()
    for ln, n in quota.items():
        for c in [c for c in fresh if lane(c) == ln][:n]:
            queue.append(c); used.add(c["key"])
    for c in fresh:
        if len(queue) >= cap:
            break
        if c["key"] not in used:
            queue.append(c); used.add(c["key"])
    queue = sorted(queue[:cap], key=lambda c: -c["score"])
    backlog = sum(1 for c in picked if c["change"] == "new") - sum(1 for c in queue if c["change"] == "new")
    payload = {
        "date": today.isoformat(),
        "generated_at": dt.datetime.now(KST).isoformat(timespec="seconds"),
        "counts": {"dart": len(dart_c), "localdata_signals": len(loc),
                   "scored": len(cands), "above_threshold": len(picked),
                   "queue": len(queue), "backlog_unreviewed": max(0, backlog)},
        "notes": notes,
        "queue": queue,
        "above_threshold": [{"key": c["key"], "name": c["name"], "score": c["score"],
                             "change": c["change"]} for c in picked],
    }
    write_json(DATA / "candidates" / f"{today.isoformat()}.json", payload)
    write_json(DATA / "candidates" / "latest.json", payload)
    meta["last_run"] = today.isoformat()
    write_json(meta_path, meta)
    write_run_log(payload, time.time() - t0)
    log(f"[done] queue={len(queue)} above={len(picked)} in {time.time() - t0:.0f}s")
    return payload


def write_run_log(p: dict, secs: float) -> None:
    lines = [f"# 실행 로그 {p['date']}", "",
             f"- 소요: {secs / 60:.1f}분",
             f"- 집계: {p['counts']}", "", "## 메모"]
    lines += [f"- {n}" for n in p["notes"]]
    lines += ["", "## 판단 대기열"]
    for c in p["queue"]:
        lines.append(f"- **{c['name']}** {c['score']}점 [{c['change']}] — " + "; ".join(c["reasons"]))
    path = DATA / "runs" / f"{p['date']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
