"""Diagnose 식품안전나라 signal quality on real data (keys masked in output).

Prints: SKU-burst / OEM-brand / HACCP counts, the most frequent 'client brands'
(to spot false positives), sample product names with parentheses, and how many
HACCP designations in the window are a site's FIRST ever vs an added item.
"""
from __future__ import annotations

import collections
import datetime as dt
import os

from .sources import foodsafety as FS

K1 = os.environ.get("FOODSAFETY_KEY", "")
K2 = os.environ.get("FOODSAFETY_KEY_HACCP", "")


def main() -> None:
    today = dt.date.today()
    since = today - dt.timedelta(days=180)
    reports, mode = FS.recent_reports(K1, since, today)
    print("reports", len(reports), mode)
    print("INDUTY", collections.Counter(r.get("INDUTY_CD_NM") for r in reports).most_common(8))

    # full HACCP history to tell first designation from an added item
    allh = FS.fetch(K2, "I0580", max_rows=400_000)
    print("I0580 total rows", len(allh), "fields", sorted(allh[0]) if allh else None)
    first: dict[str, str] = {}
    for h in allh:
        lc, d = h.get("LCNS_NO"), h.get("HACCP_APPN_DT") or ""
        if lc and d and (lc not in first or d < first[lc]):
            first[lc] = d
    s = since.strftime("%Y%m%d")
    win = [h for h in allh if (h.get("HACCP_APPN_DT") or "") >= s]
    lcw = {h["LCNS_NO"] for h in win if h.get("LCNS_NO")}
    firsts = {lc for lc in lcw if first.get(lc, "") >= s}
    print(f"HACCP in window: rows {len(win)}, sites {len(lcw)}, FIRST-ever sites {len(firsts)}")
    print("window INDUTY", collections.Counter(h.get("INDUTY_CD_NM") for h in win).most_common(6))

    sig = FS.site_signals(reports, win, today=today, min_new_items=15, min_types=3)
    print("signal sites", len(sig), "sku_burst", sum(x["sku_burst"] for x in sig),
          "brands>=3", sum(len(x["client_brands"]) >= 3 for x in sig),
          "brands>=1", sum(bool(x["client_brands"]) for x in sig),
          "haccp", sum(bool(x["haccp_new"]) for x in sig))
    bc = collections.Counter(b for x in sig for b in x["client_brands"])
    print("\nTOP 'brands' by #sites:", bc.most_common(80))
    print("\nnew_items distribution:", collections.Counter(min(x["new_items"] // 10 * 10, 200) for x in sig))
    print("\nsample burst sites:")
    for x in sorted(sig, key=lambda x: -x["new_items"])[:25]:
        print(" ", x["license_year"], x["name"], x["new_items"], x["item_types"], x["client_brands"][:6])
    print("\nsample brand sites (>=3):")
    for x in [x for x in sig if len(x["client_brands"]) >= 3][:40]:
        print(" ", x["license_year"], x["name"], x["new_items"], x["client_brands"][:8])
    paren = [r["PRDLST_NM"] for r in reports if "(" in (r.get("PRDLST_NM") or "")]
    print("\nparen product names", len(paren), "sample:", paren[:: max(1, len(paren) // 60)][:60])


if __name__ == "__main__":
    main()
