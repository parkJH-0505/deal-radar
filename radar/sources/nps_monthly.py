"""국민연금 가입 사업장 월간 전체 CSV (data.go.kr fileData 15083277).

Every month data.go.kr publishes a ~115MB CP949 CSV of ALL workplaces
(법인 3인+, 개인 10인+). Each monthly version has its own uddi and can be
downloaded with the same 3-call chain the website uses:

  POST /tcs/dss/selectFileDataDownload.do  (publicDataDetailPk=<uddi>) -> atchFileId
  POST /cmm/cmm/check-limit.json                                        -> needCaptcha?
  GET  /cmm/cmm/fileDownload.do?atchFileId=..&fileDetailSn=1            -> CSV

We keep a compact per-month aggregate of YOUNG corporations only
(data/nps/months/YYYYMM.tsv.gz, ~1MB each) and derive head-count growth
from those, so the very first run can already compute 12-month growth.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import re
import time
from collections import defaultdict
from pathlib import Path

import requests

from ..entity import norm_name
from ..http import log

PK = "15083277"
PAGE = f"https://www.data.go.kr/data/{PK}/fileData.do"
BASE = "https://www.data.go.kr"

# Temporary construction-site workplaces dominate name searches
# ("○○건설/일용/△△ 신축공사 중 철골공사"); they are never targets.
TEMP_SITE = re.compile(r"/일용/|/상용/|공사\s*중|신축공사|증축|현장|건설공사")

FIELDS = ["key", "name", "bizno6", "adopt", "induty_code", "induty_name", "sido",
          "addr", "headcount", "bill", "acq", "lost", "sites"]


class CaptchaRequired(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (deal-radar research)", "Referer": PAGE})
    return s


def list_versions(s: requests.Session | None = None) -> list[dict]:
    """All monthly versions, newest first: [{uddi, label, ym}]  (ym = data month)."""
    s = s or _session()
    html = s.get(PAGE, timeout=60).text
    cur = re.search(r"uddi:[0-9a-f\-]{36}", html).group(0)
    title_date = re.search(r"가입 사업장 내역_(\d{8})", html)
    hist = s.post(f"{BASE}/tcs/dss/selectHistAndCsvData.do",
                  data={"publicDataPk": PK, "publicDataDetailPk": cur}, timeout=60).text
    out = [{"uddi": cur, "label": title_date.group(1) if title_date else ""}]
    for m in re.finditer(r'data-public-pk="(uddi:[0-9a-f\-]{36})"[^>]*>\s*[^<]*?_(\d{8})\s*<', hist):
        out.append({"uddi": m.group(1), "label": m.group(2)})
    seen, uniq = set(), []
    for v in out:
        if v["uddi"] in seen:
            continue
        seen.add(v["uddi"])
        uniq.append(v)
    uniq.sort(key=lambda v: v["label"], reverse=True)
    return uniq


def _download(s: requests.Session, uddi: str) -> bytes:
    r = s.post(f"{BASE}/tcs/dss/selectFileDataDownload.do",
               data={"publicDataPk": PK, "publicDataDetailPk": uddi, "fileDetailSn": "1",
                     "publicDataTyCode": "PR0051"}, timeout=60)
    j = r.json()
    atch, sn = j.get("atchFileId"), j.get("fileDetailSn") or 1
    if not atch:
        raise RuntimeError(f"no atchFileId for {uddi}: {str(j)[:200]}")
    lim = s.post(f"{BASE}/cmm/cmm/check-limit.json",
                 data={"atchFileId": atch, "fileDetailSn": sn}, timeout=60)
    if lim.status_code == 403 or (lim.ok and lim.json().get("needCaptcha")):
        raise CaptchaRequired("data.go.kr download limit reached (captcha)")
    d = s.get(f"{BASE}/cmm/cmm/fileDownload.do",
              params={"atchFileId": atch, "fileDetailSn": sn, "dataNm": "nps"}, timeout=600)
    d.raise_for_status()
    if len(d.content) < 1_000_000:
        raise RuntimeError(f"download too small ({len(d.content)} bytes) for {uddi}")
    return d.content


def _col(header: list[str], *needles: str) -> int:
    for i, h in enumerate(header):
        if any(n in h for n in needles):
            return i
    raise KeyError(needles)


def aggregate(raw: bytes, *, adopt_after: str, min_headcount: int, exclude: list[str]) -> tuple[str, list[dict]]:
    """CSV bytes -> (YYYYMM, company-level rows for young corporations)."""
    text = raw.decode("cp949", errors="replace")
    rd = csv.reader(io.StringIO(text))
    header = next(rd)
    c = {
        "ym": _col(header, "자료생성년월"), "name": _col(header, "사업장명"),
        "biz": _col(header, "사업자등록번호"), "state": _col(header, "가입상태"),
        "road": _col(header, "도로명"), "sido": _col(header, "광역시도코드"),
        "form": _col(header, "사업장형태"), "icode": _col(header, "업종코드") ,
        "iname": _col(header, "업종코드명"), "adopt": _col(header, "적용일자"),
        "hc": _col(header, "가입자수"), "bill": _col(header, "고지금액"),
        "acq": _col(header, "신규취득"), "lost": _col(header, "상실"),
    }
    excl = [re.compile(p) for p in exclude]
    agg: dict[str, dict] = {}
    ym = ""
    for row in rd:
        if len(row) < len(header) - 2:
            continue
        ym = ym or re.sub(r"\D", "", row[c["ym"]])[:6]
        if row[c["state"]].strip() != "1" or row[c["form"]].strip() != "1":
            continue  # registered corporations only
        adopt = re.sub(r"\D", "", row[c["adopt"]])[:8]
        if not adopt or adopt < adopt_after:
            continue
        name = row[c["name"]].strip()
        if TEMP_SITE.search(name) or any(x.search(name) for x in excl):
            continue
        b6 = re.sub(r"\D", "", row[c["biz"]])[:6]
        nn = norm_name(name)
        if not nn:
            continue
        key = f"{b6}|{nn}"
        hc = _int(row[c["hc"]])
        a = agg.get(key)
        if a is None:
            agg[key] = {"key": key, "name": name, "bizno6": b6, "adopt": adopt,
                        "induty_code": row[c["icode"]].strip(), "induty_name": row[c["iname"]].strip(),
                        "sido": row[c["sido"]].strip(), "addr": row[c["road"]].strip(),
                        "headcount": hc, "bill": _int(row[c["bill"]]), "acq": _int(row[c["acq"]]),
                        "lost": _int(row[c["lost"]]), "sites": 1}
        else:
            a["headcount"] += hc
            a["bill"] += _int(row[c["bill"]])
            a["acq"] += _int(row[c["acq"]])
            a["lost"] += _int(row[c["lost"]])
            a["sites"] += 1
            a["adopt"] = min(a["adopt"], adopt)
    rows = [a for a in agg.values() if a["headcount"] >= min_headcount]
    return ym, rows


def _int(s: str) -> int:
    try:
        return int(re.sub(r"[^\d\-]", "", s) or 0)
    except ValueError:
        return 0


def save_month(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, delimiter="\t", extrasaction="ignore")
    w.writeheader()
    for r in sorted(rows, key=lambda r: r["key"]):
        w.writerow(r)
    # mtime=0 keeps the gzip byte-identical across re-runs (clean git diffs)
    with open(path, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))


def load_month(path: Path) -> dict[str, dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        out = {}
        for r in rd:
            for k in ("headcount", "bill", "acq", "lost", "sites"):
                r[k] = int(r[k] or 0)
            out[r["key"]] = r
        return out


def update_archive(months_dir: Path, *, want_months: int, max_downloads: int,
                   adopt_after: str, min_headcount: int, exclude: list[str],
                   pause_s: float = 15.0) -> list[str]:
    """Download missing monthly versions (newest first). Returns notes."""
    notes: list[str] = []
    s = _session()
    try:
        versions = list_versions(s)
    except Exception as e:  # noqa: BLE001
        return [f"국민연금 월간 파일 목록 조회 실패: {e}"]
    have = {p.name[:6] for p in months_dir.glob("*.tsv.gz")}
    # data month is the month BEFORE the publish label (20261022 -> 202609)
    for v in versions:
        y, m = int(v["label"][:4]), int(v["label"][4:6])
        v["ym"] = f"{y if m > 1 else y - 1:04d}{(m - 1) or 12:02d}"
    targets = [v for v in versions[:want_months] if v["ym"] not in have]
    done = 0
    for v in targets:
        if done >= max_downloads:
            notes.append(f"국민연금 월간 파일: 이번 실행 한도 도달, {len(targets) - done}개월은 다음 실행에서")
            break
        t0 = time.time()
        try:
            raw = _download(s, v["uddi"])
        except CaptchaRequired as e:
            notes.append(f"국민연금 월간 파일: {e} → 다음 실행에서 이어받음")
            break
        except Exception as e:  # noqa: BLE001
            notes.append(f"국민연금 {v['label']} 다운로드 실패: {e}")
            continue
        ym, rows = aggregate(raw, adopt_after=adopt_after, min_headcount=min_headcount,
                             exclude=exclude)
        if ym != v["ym"]:
            log(f"[nps-monthly] note: label {v['label']} holds data month {ym} (expected {v['ym']})")
        save_month(months_dir / f"{ym}.tsv.gz", rows)
        have.add(ym)
        done += 1
        log(f"[nps-monthly] {v['label']} -> {ym}: {len(rows):,} young corps ({time.time() - t0:.0f}s)")
        time.sleep(pause_s)
    if done:
        notes.append(f"국민연금 월간 파일 {done}개월 신규 반영 (보유 {len(have)}개월)")
    return notes


# ------------------------------------------------------------------ panel

def build_panel(months_dir: Path) -> tuple[list[str], dict[str, dict[str, dict]]]:
    yms = sorted(p.name[:6] for p in months_dir.glob("*.tsv.gz"))
    data = {ym: load_month(months_dir / f"{ym}.tsv.gz") for ym in yms}
    return yms, data


def _ym_minus(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[4:6])
    t = y * 12 + (m - 1) - k
    return f"{t // 12:04d}{t % 12 + 1:02d}"


def metrics_for(key: str, yms: list[str], data: dict) -> dict | None:
    if not yms or key not in data[yms[-1]]:
        return None
    last = yms[-1]
    cur = data[last][key]
    out = {"ym": last, "headcount": cur["headcount"], "monthly_bill": cur["bill"],
           "sites": cur["sites"], "adopt": cur["adopt"], "industry": cur["induty_name"],
           "series": []}
    for k in range(0, 13):
        ym = _ym_minus(last, k)
        if ym in data and key in data[ym]:
            out["series"].append({"ym": ym, "headcount": data[ym][key]["headcount"]})
    hc = cur["headcount"]
    for lag in (3, 6, 12):
        ym = _ym_minus(last, lag)
        if ym in data:
            past = data[ym].get(key)
            if past and past["headcount"] > 0:
                out[f"growth_{lag}m"] = round((hc - past["headcount"]) / past["headcount"], 3)
            elif past is None and ym in data:
                out[f"growth_{lag}m_from_zero"] = True  # not enrolled then (or < threshold)
    if hc:
        out["bill_per_head"] = round(cur["bill"] / hc)
        past12 = data.get(_ym_minus(last, 12), {}).get(key)
        if past12 and past12["headcount"]:
            prev_bph = past12["bill"] / past12["headcount"]
            out["bill_per_head_change_12m"] = round(out["bill_per_head"] / prev_bph - 1, 3) if prev_bph else None
    return out


def scan(yms: list[str], data: dict, *, today: dt.date, max_age_years: float,
         min_headcount: int, min_growth_12m: float, min_growth_6m: float) -> list[dict]:
    """Young corporations whose head-count is compounding."""
    if not yms:
        return []
    last = yms[-1]
    cutoff = (today - dt.timedelta(days=int(max_age_years * 365.25))).strftime("%Y%m%d")
    out = []
    for key, r in data[last].items():
        if r["adopt"] < cutoff or r["headcount"] < min_headcount:
            continue
        m = metrics_for(key, yms, data)
        g12, g6 = m.get("growth_12m"), m.get("growth_6m")
        if (g12 is not None and g12 >= min_growth_12m) or (g6 is not None and g6 >= min_growth_6m) \
                or (m.get("growth_12m_from_zero") and r["headcount"] >= min_headcount * 2):
            out.append({"key": f"nps:{key}", "name": r["name"], "bizno6": r["bizno6"],
                        "nps": m, "sido_code": r["sido"], "addr": r["addr"],
                        "induty_name": r["induty_name"], "induty_code": r["induty_code"]})
    log(f"[nps-monthly] scan {last}: {len(out)} growth candidates")
    return out
