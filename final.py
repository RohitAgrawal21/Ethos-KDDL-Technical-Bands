"""Final call for ONE stock (python final.py KDDL): parameter scorecard + strategy sims + analogs -> BUY/HOLD/SELL + confidence 0-100.
Rules fixed before looking at the outcome:
 * Call is driven by the trend-state rule (strategy A) because it is the only simulated rule that held up (see strategy.py);
   direction can only flip to SELL when A's exit fires (close<SMA50 or SMA50<SMA200). HOLD only when A is flat but SMA50>SMA200 still holds (mixed).
 * Confidence = shrunk hit-rate (K=10 pseudo-obs at 50%) of 20-session positive returns for the most specific matching analog set with n>=15
   (uptrend & z50>=2 & 3-day gain>=12%), dedupe >=10 sessions apart. For SELL it is the share of negative 20-session returns after the same exit rule."""
import sys, json, numpy as np, pandas as pd
import analyze as A, strategy as S

sym = sys.argv[1] if len(sys.argv) > 1 else "KDDL"; df = S.prep(sym); oos = A.OOS[sym]
n = len(df); f5, f10, f20 = (A.fwd(df, h) for h in (5, 10, 20)); L = df.iloc[-1]
df["vr3"] = df.volr.rolling(3).max()
up = (df.close > df.sma50) & (df.sma50 > df.sma200)
def r(x): return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 5)

# ---------- 1. parameter scorecard ----------
P = [
 ("Long-term trend", "Close above a rising 200-day average", (df.close > df.sma200) & df.sma200up, f"Close {L.close/L.sma200-1:+.0%} vs SMA200 (₹{L.sma200:,.0f}), SMA200 rising"),
 ("Medium-term trend", "Close above 50-day average and 50-day above 200-day", up, f"Close {L.close/L.sma50-1:+.1%} vs SMA50 (₹{L.sma50:,.0f}); SMA50 above SMA200"),
 ("Short-term trend", "20-day average above 50-day and close above 20-day", (df.sma20 > df.sma50) & (df.close > df.sma20), f"SMA20 ₹{L.sma20:,.0f} vs SMA50 ₹{L.sma50:,.0f}; close {L.close/L.sma20-1:+.1%} vs SMA20"),
 ("Momentum", "60-day and 20-day returns both positive", (df.roc60 > 0) & (df.roc20 > 0), f"20-day {L.roc20:+.0%}, 60-day {L.roc60:+.0%}"),
 ("Stretch (SD / Bollinger)", "Close 2+ standard deviations above its 50-day average (z50 >= 2)", df.z50 >= 2, f"z50 = {L.z50:+.2f}, z20 = {L.z20:+.2f} (inside the +2SD band by {abs(L.close-L.ub):,.0f})"),
 ("Volume thrust", "Volume at least 3x its 20-day average within the last 3 sessions", df.vr3 >= 3, f"Peak 3-day volume {df.vr3.iloc[-1]:.0f}x normal"),
]
score_rows = []
valid = f20.notna() & df.sma200.notna()
for name, rule, mask, today in P:
    m = mask.fillna(False) & valid; o = ~mask.fillna(False) & valid
    row = {"name": name, "rule": rule, "today": today, "on_now": bool(mask.iloc[-1]), "n_days": int(m.sum()), "n_eff": int(m.sum() // 20)}
    for h, f in (("5", f5), ("20", f20)):
        row[f"in{h}"] = r(f[m].mean()); row[f"out{h}"] = r(f[o].mean()); row[f"hit_in{h}"] = r((f[m] > 0).mean()); row[f"hit_out{h}"] = r((f[o] > 0).mean())
    mo = m & (df.index >= oos); oo = o & (df.index >= oos)
    row["in20_oos"] = r(f20[mo].mean()); row["out20_oos"] = r(f20[oo].mean())
    ex20 = row["in20"] - row["out20"]; row["excess20"] = r(ex20); row["excess5"] = r(row["in5"] - row["out5"])
    row["verdict"] = "Bullish" if ex20 >= .01 else "Bearish" if ex20 <= -.01 else "Neutral"
    row["short_term"] = "Bullish" if row["excess5"] >= .005 else "Bearish" if row["excess5"] <= -.005 else "Neutral"
    score_rows.append(row)

# ---------- 2. strategies ----------
strat_rows = []; curves = {}
bh = pd.Series(1.0, index=df.index); bh.iloc[:200] = 0
allpos = {"Buy & hold": bh}
for k, (en, ex) in S.STRATS.items(): allpos[k] = S.run_pos(df, en(df), ex(df))
for k, pos in allpos.items():
    rr, tr = S.equity(df, pos); tr = np.array(tr)
    d = {"name": k, "trades": int(len(tr)), "hit": r((tr > 0).mean()) if len(tr) else None, "avg_trade": r(tr.mean()) if len(tr) else None, "worst_trade": r(tr.min()) if len(tr) else None, "now_long": bool(pos.iloc[-1])}
    for lab, lo, hi in (("full", "2016", "2100"), ("is", "2016", oos), ("oos", oos, "2100")):
        s = S.stats(rr, tr, pos, lo, hi); d[lab] = {a: r(b) for a, b in s.items()}
    strat_rows.append(d)
    eq = (1 + rr[200:]).cumprod(); curves[k] = eq
wk = curves["Buy & hold"].resample("W").last().index
ek = {k: [r(v) for v in c.reindex(wk, method="ffill").values] for k, c in curves.items()}
dd = {k: [r(v) for v in (c / c.cummax() - 1).reindex(wk, method="ffill").values] for k, c in curves.items()}

# ---------- 3. analogs for today's setup ----------
setup = up & (df.z50 >= 2) & (df.roc3 >= .12); ix = A.dedupe(setup.iloc[:-0 or None])
ix = [i for i in ix]; ev = []
for i in ix:
    ev.append({"date": df.index[i].strftime("%Y-%m-%d"), "r5": r(f5.iloc[i]), "r10": r(f10.iloc[i]), "r20": r(f20.iloc[i]), "mae20": r(df.close.iloc[i + 1:i + 21].min() / df.close.iloc[i] - 1) if i + 21 <= n else None})
lab = [e for e in ev if e["r20"] is not None]; hits = sum(e["r20"] > 0 for e in lab); N = len(lab); K = 10
conf_buy = (hits + .5 * K) / (N + K)
oosl = [e for e in lab if e["date"] >= oos]; is_l = [e for e in lab if e["date"] < oos]
up_base = f20[up & valid]
analog = {"n": N, "hits": int(hits), "hit": r(hits / N), "mean20": r(np.mean([e["r20"] for e in lab])), "median20": r(np.median([e["r20"] for e in lab])), "worst20": r(min(e["r20"] for e in lab)),
          "mean5": r(np.mean([e["r5"] for e in lab if e["r5"] is not None])), "base20": r(up_base.mean()), "base_hit20": r((up_base > 0).mean()),
          "oos_n": len(oosl), "oos_hit": r(np.mean([e["r20"] > 0 for e in oosl])) if oosl else None, "is_n": len(is_l), "is_hit": r(np.mean([e["r20"] > 0 for e in is_l])) if is_l else None,
          "avg_mae20": r(np.mean([e["mae20"] for e in lab if e["mae20"] is not None])), "events": ev}

# ---------- 4. decision ----------
A_long = bool(allpos[list(S.STRATS)[0]].iloc[-1] == 1); trend_ok = bool(L.sma50 > L.sma200)
if A_long: call = "BUY"; confidence = conf_buy
elif not trend_ok: call = "SELL"; confidence = None
else: call = "HOLD"; confidence = None
if call == "SELL":   # share of negative 20d returns after A's exit (close<SMA50 & SMA50<SMA200)
    m = (df.close < df.sma50) & (df.sma50 < df.sma200); ixs = A.dedupe(m); ret = f20.iloc[ixs].dropna(); confidence = ((ret < 0).sum() + 5) / (len(ret) + 10)
bull = sum(x["verdict"] == "Bullish" for x in score_rows if x["on_now"]); bear = sum(x["verdict"] == "Bearish" for x in score_rows if x["on_now"])
out = {"sym": sym, "asof": df.index[-1].strftime("%Y-%m-%d"), "close": r(L.close), "call": call, "confidence": r(confidence), "bull": bull, "bear": bear,
       "levels": {"sma20": r(L.sma20), "sma50": r(L.sma50), "sma200": r(L.sma200), "ub": r(L.ub), "lb": r(L.lb), "exit_close_below": r(L.sma50), "risk_to_exit": r(L.sma50 / L.close - 1)},
       "scorecard": score_rows, "strategies": strat_rows, "analog": analog, "oos_start": oos,
       "curves": {"dates": [d.strftime("%Y-%m-%d") for d in wk], "equity": ek, "drawdown": dd}}
import os; os.makedirs("site", exist_ok=True)
open(f"site/final_{sym.lower()}.js", "w", encoding="utf-8").write(f"window.FINAL=window.FINAL||{{}};window.FINAL['{sym}']=" + json.dumps(out, separators=(",", ":")) + ";")
print(call, f"confidence={confidence*100:.0f}%", "bullish params on:", bull, "bearish:", bear)
print("analog:", {k: v for k, v in analog.items() if k != "events"})
for x in score_rows: print(f"{x['name']:26s} on={x['on_now']!s:5s} n={x['n_days']:4d}(eff~{x['n_eff']:3d}) 5d in/out={x['in5']*100:+.1f}/{x['out5']*100:+.1f} 20d in/out={x['in20']*100:+.1f}/{x['out20']*100:+.1f} hit20 {x['hit_in20']*100:.0f}/{x['hit_out20']*100:.0f} oos20 {x['in20_oos']*100:+.1f}/{x['out20_oos']*100:+.1f} -> {x['verdict']} (5d {x['short_term']})")
