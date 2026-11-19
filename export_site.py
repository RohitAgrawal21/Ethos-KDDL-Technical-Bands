"""Export everything the dashboard needs to site/data.js (window.DATA). Run after analyze.py inputs exist: python export_site.py"""
import json, os, numpy as np, pandas as pd, datetime as dt
import analyze as A, score as SC

def clean(v):
    if isinstance(v, (np.floating, float)): return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)): return int(v)
    return v

out = {"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M IST"), "params": {"N": A.N, "K": A.K, "lock": A.LOCK, "horizons": A.H, "oos": A.OOS}, "stocks": {}}
for sym in ("ETHOSLTD", "KDDL"):
    df = A.feats(A.load(sym)); sigs = A.signals(df)
    cols = ["open", "high", "low", "close", "vol", "sma20", "sma50", "sma200", "ub", "lb", "z20", "z50", "pct20", "pct50", "pctb", "bw"]
    d = {"dates": [x.strftime("%Y-%m-%d") for x in df.index]}
    for c in cols: d[c] = [clean(v) for v in df[c].values]
    events = {k: [df.index[i].strftime("%Y-%m-%d") for i in A.dedupe(m)] for k, m in sigs.items()}
    rawdays = {k: [df.index[i].strftime("%Y-%m-%d") for i in np.flatnonzero(m.values)] for k, m in sigs.items()}
    tabs = {}
    for lab, lo, hi in (("Full", None, None), ("In-sample", "1900-01-01", A.OOS[sym]), ("Out-of-sample", A.OOS[sym], "2100-01-01")):
        t = A.table(df, sigs, lo, hi)
        tabs[lab] = [{k: clean(v) for k, v in r.items()} for r in t.to_dict("records") if r.get("n", 0)]
    sims = {}
    for k in list(sigs)[:4:1]:
        if k.startswith(("R1", "R2", "R3")):
            for lab, lo in (("Full", "1900"), ("Out-of-sample", A.OOS[sym])):
                m = sigs[k].copy(); m[(m.index < lo)] = False
                sims[f"{k}|{lab}"] = {a: clean(b) for a, b in A.trade_sim(df, m).items()}
    L = df.iloc[-1]
    status = {c: clean(L[c]) for c in ["close", "sma20", "sma50", "sma200", "ub", "lb", "z20", "z50", "z200", "pct20", "pct50", "pctb", "bw"]}
    status["date"] = df.index[-1].strftime("%Y-%m-%d"); status["vol"] = int(L.vol)
    status["conditions"] = {"A: upper-band breach (z20 >= 2)": bool(L.z20 >= 2), "B: lower-band breach (z20 <= -2)": bool(L.z20 <= -2), "C: stretch above SMA50 (z50 >= 2)": bool(L.z50 >= 2)}
    out["stocks"][sym] = {"series": d, "events": events, "rawdays": rawdays, "tables": tabs, "sims": sims, "status": status}
out["scores"] = {}
for sym in ("ETHOSLTD", "KDDL"):
    sc = SC.run(sym); df = A.feats(A.load(sym)); L = df.iloc[-1]; roc = L.close / df.close.iloc[-21] - 1
    checks = [("Close above SMA20", bool(L.close > L.sma20)), ("Close above SMA50", bool(L.close > L.sma50)), ("Close above SMA200", bool(L.close > L.sma200)),
              ("SMA20 above SMA50", bool(L.sma20 > L.sma50)), ("20-day return positive", bool(roc > 0))]
    n = sum(v for _, v in checks); trend = "Uptrend" if n >= 4 else "Leaning up" if n == 3 else "Leaning down" if n == 2 else "Downtrend"
    stretch = "Stretched up" if (L.z20 >= 2 or L.z50 >= 2) else "Stretched down" if (L.z20 <= -2 or L.z50 <= -2) else "Not stretched"
    sc["posture"] = {"trend_points": n, "trend": trend, "checks": checks, "stretch": stretch}
    out["scores"][sym] = sc
os.makedirs("site", exist_ok=True)
open("site/data.js", "w", encoding="utf-8").write("window.DATA=" + json.dumps(out, separators=(",", ":")) + ";")
print("wrote site/data.js", os.path.getsize("site/data.js") // 1024, "KB")
