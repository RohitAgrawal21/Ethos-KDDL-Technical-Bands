"""Strategy simulations for ONE stock (default KDDL). Strategies fixed in advance (no parameter search): see STRATS.
Execution: signal at close t -> trade at open t+1. Costs per side: slippage 0.15%, STT 0.1%, exch 0.0035%, (+stamp 0.015% on buy); DP Rs18 on sell on Rs5 lakh.
After-tax approx: 20% on each profitable round trip (short-term; no loss offset => conservative)."""
import sys, numpy as np, pandas as pd
import analyze as A

SLIP = .0015; BUY = SLIP + .001 + .000035 + .00015; SELL = SLIP + .001 + .000035 + 18 / 500000
RF = .065

def prep(sym):
    df = A.feats(A.load(sym)); c = df.close
    df["roc60"] = c / c.shift(60) - 1; df["roc3"] = c / c.shift(3) - 1; df["roc20"] = c / c.shift(20) - 1
    df["hi50"] = c.shift(1).rolling(50).max(); df["lo50"] = c.shift(1).rolling(50).min()
    df["volr"] = df.vol / df.vol.shift(1).rolling(20).mean()
    df["sma200up"] = df.sma200 > df.sma200.shift(20)
    return df

def run_pos(df, entry, exit_, start=0):
    """stateful: enter when entry true, leave when exit_ true. returns position series decided at each close."""
    pos = np.zeros(len(df)); p = 0
    for i in range(len(df)):
        if i >= start and not np.isnan(df.sma200.iloc[i]):
            if p == 0 and entry.iloc[i]: p = 1
            elif p == 1 and exit_.iloc[i]: p = 0
        pos[i] = p
    return pd.Series(pos, index=df.index)

def equity(df, pos):
    o, c = df.open.values, df.close.values; n = len(df); r = np.zeros(n); pv = pos.values; trades = []; ent = None
    for d in range(2, n):
        a, b = pv[d - 1], pv[d - 2]
        if a == 1 and b == 1: r[d] = c[d] / c[d - 1] - 1
        elif a == 1 and b == 0: r[d] = c[d] / o[d] - 1; r[d] = (1 + r[d]) * (1 - BUY) - 1; ent = o[d] * (1 + BUY)
        elif a == 0 and b == 1:
            r[d] = o[d] / c[d - 1] - 1; r[d] = (1 + r[d]) * (1 - SELL) - 1
            if ent: trades.append(o[d] * (1 - SELL) / ent - 1); ent = None
    return pd.Series(r, index=df.index), trades

def stats(r, trades, pos, lo, hi, tax=True):
    m = (r.index >= lo) & (r.index < hi); x = r[m]; yrs = max(len(x) / 247, 1e-9)
    eq = (1 + x).cumprod(); cagr = eq.iloc[-1] ** (1 / yrs) - 1; dd = (eq / eq.cummax() - 1).min()
    ex = x - RF / 247; sh = ex.mean() / x.std() * np.sqrt(247) if x.std() > 0 else np.nan
    return dict(CAGR=cagr, MaxDD=dd, Sharpe=sh, Invested=float(pos[m].mean()), Total=eq.iloc[-1] - 1)

STRATS = {
 "A Trend: long while close>SMA50>SMA200": (lambda d: (d.close > d.sma50) & (d.sma50 > d.sma200), lambda d: (d.close < d.sma50) | (d.sma50 < d.sma200)),
 "B Trend+stretch exit: as A, but step aside when z50>=2.5, re-enter z50<1.5": (lambda d: (d.close > d.sma50) & (d.sma50 > d.sma200) & (d.z50 < 1.5), lambda d: (d.close < d.sma50) | (d.sma50 < d.sma200) | (d.z50 >= 2.5)),
 "C Dip-buy in uptrend: buy z20<=-1 above SMA200, exit z20>=+1 or <SMA200": (lambda d: (d.close > d.sma200) & (d.z20 <= -1), lambda d: (d.z20 >= 1) | (d.close < d.sma200)),
 "D Mean reversion: buy z20<=-2, exit z20>=0": (lambda d: d.z20 <= -2, lambda d: d.z20 >= 0),
 "E Breakout: buy close>50d high with volume>=2x, exit close<SMA20": (lambda d: (d.close > d.hi50) & (d.volr >= 2), lambda d: d.close < d.sma20),
}

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "KDDL"; df = prep(sym); oos = A.OOS[sym]
    bh = pd.Series(1.0, index=df.index); bh.iloc[:200] = 0
    rows = []
    allpos = {"Buy&Hold (from bar 200)": bh}
    for k, (en, ex) in STRATS.items(): allpos[k] = run_pos(df, en(df), ex(df))
    for k, pos in allpos.items():
        r, tr = equity(df, pos); tr = np.array(tr)
        for lab, lo, hi in (("FULL", "2016", "2100"), ("IS<" + oos[:4], "2016", oos), ("OOS>=" + oos[:4], oos, "2100")):
            s = stats(r, tr, pos, lo, hi); rows.append((k, lab, s))
        print(f"\n{k}: trades={len(tr)} hit={np.mean(tr > 0)*100 if len(tr) else 0:.0f}% avg trade={np.mean(tr)*100 if len(tr) else 0:+.1f}% worst={np.min(tr)*100 if len(tr) else 0:+.1f}% | NOW pos={int(pos.iloc[-1])}")
        for kk, lab, s in rows[-3:]: print(f"   {lab:10s} CAGR {s['CAGR']*100:6.1f}%  MaxDD {s['MaxDD']*100:6.1f}%  Sharpe {s['Sharpe']:5.2f}  invested {s['Invested']*100:3.0f}%  total {s['Total']*100:8.0f}%")
    # Analogs of today's setup
    L = df.iloc[-1]
    print("\nTODAY:", {k: round(float(L[k]), 3) for k in ["close", "z20", "z50", "roc3", "roc20", "roc60", "volr"]}, "sma200up", bool(L.sma200up))
    up = (df.close > df.sma200) & (df.sma50 > df.sma200)
    def ev(mask, name):
        ix = A.dedupe(mask & up if name != "all" else mask); out = []
        for h in (5, 10, 20):
            f = A.fwd(df, h); r = f.iloc[ix].dropna(); b = f[up].dropna().mean()
            out.append(f"{h}d n={len(r)} mean={r.mean()*100:+.1f}% med={r.median()*100:+.1f}% hit={(r>0).mean()*100:.0f}% worst={r.min()*100:+.1f}% base(uptrend)={b*100:+.1f}%")
        print(f"\n[{name}]  dates:", [df.index[i].strftime('%y-%m-%d') for i in ix][:25]); [print("  ", o) for o in out]
    ev((df.z50 >= 2), "uptrend & z50>=2 (stretched)")
    ev((df.roc3 >= .12), "uptrend & 3-day gain >=12% (spike)")
    ev((df.roc3 >= .12) & (df.z50 >= 2), "spike AND stretched")
    ev((df.volr >= 3) & (df.z20 >= 1.5), "uptrend & volume>=3x & z20>=1.5 (high-volume thrust)")
    # what followed 2 sessions after a >=12% 3d spike (like today = day 3)
    sp = df.index[(df.roc3 >= .12) & up];
    print("\nspike days list:", [d.strftime('%y-%m-%d') for d in sp])
