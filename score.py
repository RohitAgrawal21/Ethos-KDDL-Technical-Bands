"""Composite technical score per stock: 6 parameters -> P(close 10 sessions ahead > close today), walk-forward (no look-ahead).
For date t a parameter's probability uses ONLY labelled outcomes known at t (observations s <= t-H) in the same state, shrunk to the
base rate with K pseudo-observations. Composite = logit(base) + mean_i(logit(p_i)-logit(base)); equal weights (fixed in advance).
Bin edges: fixed for z-scores / band-width percentile; full-sample quintiles for spread, %vs200, 20d momentum (edge-only look-ahead, noted)."""
import numpy as np, pandas as pd
import analyze as A

H = 10; K = 20; MIN_HIST = 150; EDGE = 0.05
PARAMS = {  # key: (label, plain meaning)
 "z20": ("Price vs 20-day average (and +/-1, +/-2 SD bands)", "Where the close sits relative to SMA20, in standard deviations. Covers the 20-DMA, +1SD, +2SD and Bollinger bands (%B is the same number)."),
 "z50": ("Price vs 50-day average", "Same idea on the 50-day window: medium-term stretch."),
 "spr": ("20-day vs 50-day average (trend)", "SMA20 / SMA50 - 1. Positive = short-term average above medium-term (uptrend)."),
 "p200": ("Price vs 200-day average (long trend)", "Close / SMA200 - 1. Long-term trend context."),
 "roc": ("20-day momentum", "Close / close 20 sessions ago - 1."),
 "bwp": ("Volatility regime (band width)", "Bollinger bandwidth ranked against the past 250 sessions: low = squeeze, high = already expanded."),
}
FIXED = {"z20": [-2, -1, 0, 1, 2], "z50": [-2, -1, 0, 1, 2], "bwp": [.2, .4, .6, .8]}

def logit(p): p = np.clip(p, 1e-4, 1 - 1e-4); return np.log(p / (1 - p))
def sig(x): return 1 / (1 + np.exp(-x))

def auc(score, y):
    m = ~(np.isnan(score) | np.isnan(y)); s = pd.Series(score[m]); yy = y[m]
    n1 = yy.sum(); n0 = len(yy) - n1
    if n1 == 0 or n0 == 0: return np.nan
    r = s.rank().values; return float((r[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

def build(sym):
    df = A.feats(A.load(sym))
    df["spr"] = df.sma20 / df.sma50 - 1; df["p200"] = df.pct200; df["roc"] = df.close / df.close.shift(20) - 1
    df["bwp"] = df.bw.rolling(250, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)
    df["z20"] = df.z20; df["z50"] = df.z50
    r = df.close.shift(-H) / df.close - 1; y = (r > 0).astype(float).where(r.notna())
    cols = list(PARAMS); ok = df[cols].notna().all(axis=1)
    st = pd.DataFrame(index=df.index); edges = {}
    for c in cols:
        e = FIXED.get(c) or list(df.loc[ok, c].quantile([.2, .4, .6, .8]).values)
        edges[c] = [float(x) for x in e]; st[c] = np.where(ok, np.digitize(df[c].values, e), -1)
    return df, r, y, st, edges, ok

def walk(df, r, y, st, ok):
    n = len(df); idx = np.arange(n); cols = list(PARAMS)
    base = np.full(n, np.nan); P = {c: np.full(n, np.nan) for c in cols}; M = {c: np.full(n, np.nan) for c in cols}
    yv = y.values; rv = r.values
    for c in cols:
        cnt = {}; upc = {}; sm = {}
    # incremental: at t, add outcome of s=t-H (known now) to its state bucket
    cnt = {c: {} for c in cols}; upc = {c: {} for c in cols}; smr = {c: {} for c in cols}; tot = 0; tup = 0; tsr = 0.0
    for t in range(n):
        s = t - H
        if s >= 0 and ok.iloc[s] and not np.isnan(yv[s]):
            tot += 1; tup += yv[s]; tsr += rv[s]
            for c in cols:
                k = st[c].iloc[s]; cnt[c][k] = cnt[c].get(k, 0) + 1; upc[c][k] = upc[c].get(k, 0) + yv[s]; smr[c][k] = smr[c].get(k, 0.0) + rv[s]
        if tot >= MIN_HIST and ok.iloc[t]:
            b = tup / tot; mb = tsr / tot; base[t] = b
            for c in cols:
                k = st[c].iloc[t]; nn = cnt[c].get(k, 0); u = upc[c].get(k, 0); sr = smr[c].get(k, 0.0)
                P[c][t] = (u + K * b) / (nn + K); M[c][t] = (sr + K * mb) / (nn + K)
    comb = np.full(n, np.nan); valid = ~np.isnan(base)
    dev = np.vstack([logit(P[c]) - logit(base) for c in cols]); comb[valid] = sig(logit(base[valid]) + dev[:, valid].mean(axis=0))
    return base, P, M, comb

def run(sym):
    df, r, y, st, edges, ok = build(sym)
    base, P, M, comb = walk(df, r, y, st, ok); yv = y.values; rv = r.values
    lab = ~np.isnan(yv) & ~np.isnan(comb)
    oos0 = np.datetime64(A.OOS[sym]); isoos = (df.index.values >= oos0)
    res = {"auc_all": auc(comb, yv), "auc_oos": auc(np.where(isoos, comb, np.nan), yv), "auc_param": {c: auc(P[c], yv) for c in PARAMS},
           "n_lab": int(lab.sum()), "n_eff": int(lab.sum() // H), "base_rate": float(np.nanmean(yv[lab]))}
    # calibration terciles / quintiles of composite
    q = pd.qcut(pd.Series(comb[lab]), 5, labels=False, duplicates="drop"); cal = []
    for g in sorted(q.unique()):
        m = (q.values == g); cal.append({"bucket": int(g) + 1, "n": int(m.sum()), "p_mid": float(np.mean(comb[lab][m])), "hit": float(np.mean(yv[lab][m])), "mean_ret": float(np.mean(rv[lab][m])),
                                         "worst": float(np.min(rv[lab][m]))})
    res["calibration"] = cal
    # today
    t = len(df) - 1; row = {}
    for c in PARAMS:
        k = int(st[c].iloc[t]); e = edges[c]; lo = e[k - 1] if k > 0 else None; hi = e[k] if k < len(e) else None
        row[c] = {"label": PARAMS[c][0], "meaning": PARAMS[c][1], "value": float(df[c].iloc[t]), "state": k, "lo": lo, "hi": hi, "p_up": float(P[c][t]), "exp_ret": float(M[c][t]),
                  "edge_pp": float((P[c][t] - base[t]) * 100), "auc": res["auc_param"][c], "n_state": int(((st[c].values[:t - H + 1] == k) & ok.values[:t - H + 1]).sum())}
    pc = float(comb[t]); b = float(base[t]); d = pc - b
    lean = "Bullish bias" if d >= EDGE else ("Bearish bias" if d <= -EDGE else "Neutral")
    conf = "Moderate" if (res["auc_oos"] or 0) >= .58 and res["auc_all"] >= .58 else ("Low" if (res["auc_all"] or 0) >= .53 else "None demonstrated")
    res["today"] = {"date": df.index[t].strftime("%Y-%m-%d"), "p_comb": pc, "base": b, "edge_pp": d * 100, "lean": lean, "confidence": conf, "params": row}
    # downsampled series for chart
    res["series"] = {"dates": [x.strftime("%Y-%m-%d") for x in df.index], "p": [None if np.isnan(v) else round(float(v), 4) for v in comb],
                     "base": [None if np.isnan(v) else round(float(v), 4) for v in base]}
    res["edges"] = edges; res["H"] = H
    res["raw"] = {c: [None if np.isnan(v) else round(float(v), 5) for v in df[c].values] for c in ("spr", "p200", "roc", "bwp")}
    return res

if __name__ == "__main__":
    for s in ("ETHOSLTD", "KDDL"):
        o = run(s); t = o["today"]
        print(f"\n== {s} {t['date']}: P(up {H}d)={t['p_comb']*100:.1f}% vs base {t['base']*100:.1f}% edge {t['edge_pp']:+.1f}pp -> {t['lean']} (confidence: {t['confidence']})")
        print(f" walk-forward AUC all={o['auc_all']:.3f} oos={o['auc_oos']:.3f}  n_lab={o['n_lab']} n_eff~{o['n_eff']}  per-param AUC:", {k: round(v, 3) for k, v in o['auc_param'].items()})
        for c, p in t["params"].items(): print(f"  {c:4s} val={p['value']:8.3f} state={p['state']} P(up)={p['p_up']*100:5.1f}% edge={p['edge_pp']:+5.1f}pp expRet={p['exp_ret']*100:+.2f}% n_state={p['n_state']}")
        for b in o["calibration"]: print("  quintile", b)
