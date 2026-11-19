"""Bollinger / deviation study for ETHOSLTD and KDDL (NSE). Reproduce: curl raw JSON (see REPORT.md), then `python analyze.py`.
Series: Yahoo "close" = back-adjusted for splits/rights/bonus by the provider, NOT for dividends (KDDL dividends <0.4% each; sensitivity run on adjclose).
All signals use data up to and including close of day t. Forward returns are measured from close t (indicator study)
or from open t+1 (trade sim)."""
import json, datetime as dt, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

OOS = {"ETHOSLTD": "2024-07-01", "KDDL": "2023-01-01"}   # fixed before looking at results
H = [5, 10, 20]; N = 20; K = 2.0; LOCK = 20; rng = np.random.default_rng(7)

def load(sym):
    j = json.load(open(f"{sym}.raw.json"))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "vol": q["volume"],
                       "adj": j["indicators"]["adjclose"][0]["adjclose"]},
                      index=pd.to_datetime([dt.datetime.fromtimestamp(t).date() for t in j["timestamp"]]))
    return df.dropna(subset=["close"])

def feats(df, col="close"):
    c = df[col]
    for n in (20, 50, 200):
        df[f"sma{n}"] = c.rolling(n).mean()
        df[f"sd{n}"] = c.rolling(n).std(ddof=0)          # population SD of the PRICE LEVEL (Bollinger convention)
        df[f"pct{n}"] = c / df[f"sma{n}"] - 1
        df[f"z{n}"] = (c - df[f"sma{n}"]) / df[f"sd{n}"]
    df["ub"] = df.sma20 + K * df.sd20; df["lb"] = df.sma20 - K * df.sd20
    df["pctb"] = (c - df.lb) / (df.ub - df.lb); df["bw"] = (df.ub - df.lb) / df.sma20
    return df

def signals(df):
    z = df.z20; zp = z.shift(1)
    s = {
      "R1 lower breach  (z20<=-2)": z <= -2,
      "R2 lower re-entry (prev z20<=-2, now >-2)": (zp <= -2) & (z > -2),
      "R3 upper breach  (z20>=+2)": z >= 2,
      "R4 upper re-entry (prev z20>=2, now <2)": (zp >= 2) & (z < 2),
      "R5 stretched above SMA50 (z50>=+2)": df.z50 >= 2,
      "R6 stretched below SMA50 (z50<=-2)": df.z50 <= -2,
    }
    return {k: v.fillna(False) for k, v in s.items()}

def dedupe(mask):
    idx = np.flatnonzero(mask.values); keep = []; last = -10**9
    for i in idx:
        if i - last >= LOCK: keep.append(i); last = i
    return keep

def fwd(df, h):  # close-to-close from signal close
    return df.close.shift(-h) / df.close - 1
def mae(df, h):  # worst close within h days vs signal close
    c = df.close.values; out = np.full(len(c), np.nan)
    for i in range(len(c) - h): out[i] = c[i + 1:i + h + 1].min() / c[i] - 1
    return pd.Series(out, index=df.index)

def summarize(df, ix, h, base):
    r = fwd(df, h).iloc[ix].dropna(); m = mae(df, h).iloc[ix].dropna()
    if len(r) == 0: return dict(n=0)
    boots = [rng.choice(r.values, len(r)).mean() for _ in range(2000)] if len(r) > 1 else [np.nan]
    return dict(n=len(r), mean=r.mean(), med=r.median(), hit=(r > 0).mean(), worst=r.min(), mae=m.mean(),
                base=base, excess=r.mean() - base, lo=np.percentile(boots, 5), hi=np.percentile(boots, 95))

def table(df, sigs, lo=None, hi=None):
    rows = []
    d = df if lo is None else df[(df.index >= lo) & (df.index < hi)]
    for name, m in sigs.items():
        mm = m.loc[d.index]; ix_local = dedupe(mm)
        ix = [df.index.get_loc(d.index[i]) for i in ix_local]
        for h in H:
            bs = fwd(df, h).loc[d.index].dropna()
            s = summarize(df, ix, h, bs.mean()); s.update(rule=name, h=h, raw=int(mm.sum())); rows.append(s)
    return pd.DataFrame(rows)

def costs_roundtrip(price):  # delivery equity, NSE: STT .1% both sides, stamp .015% buy, exch .00297%x2, SEBI ~0, GST18% on txn, DP Rs15.34+GST on sell, brokerage 0
    pct = 0.001 * 2 + 0.00015 + 0.0000297 * 2 * 1.18
    return pct  # + DP charge handled below as fixed

def trade_sim(df, mask, h=10, slip=0.0015, qty_value=100000.0):
    """Entry at next open*(1+slip), exit at close of day t+1+h-1 (hold h sessions incl. entry day) *(1-slip). Non-overlapping."""
    res = []
    for i in dedupe(mask):
        if i + h + 1 >= len(df): continue
        e = df.open.iloc[i + 1] * (1 + slip); x = df.close.iloc[i + h] * (1 - slip)
        gross = x / e - 1; cost = costs_roundtrip(e) + 17.0 / qty_value  # Rs15.34+18% GST ~ Rs18
        res.append(gross - cost)
    r = pd.Series(res, dtype=float)
    return dict(n=len(r), mean_net=r.mean() if len(r) else np.nan, hit=(r > 0).mean() if len(r) else np.nan,
                worst=r.min() if len(r) else np.nan, total=(1 + r).prod() - 1 if len(r) else np.nan)

def fmt(x, p=True): return "  n/a " if pd.isna(x) else (f"{x*100:6.2f}%" if p else f"{x:6.2f}")

def chart(df, sym, sigs):
    d = df.iloc[-260:]
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), sharex=True, gridspec_kw={"height_ratios": [3, 1.2, 1.2]})
    ax[0].plot(d.index, d.close, "k", lw=1.2, label="Close (split/rights-adj., dividend-unadj.)")
    ax[0].plot(d.index, d.sma20, "tab:blue", lw=1, label="SMA20"); ax[0].plot(d.index, d.sma50, "tab:orange", lw=1, label="SMA50")
    ax[0].fill_between(d.index, d.lb, d.ub, color="tab:blue", alpha=.12, label="Bollinger 20,2")
    for k, c, mk in (("R1 lower breach  (z20<=-2)", "red", "v"), ("R3 upper breach  (z20>=+2)", "green", "^")):
        m = sigs[k].loc[d.index]; ax[0].scatter(d.index[m], d.close[m], c=c, marker=mk, s=35, zorder=5, label=k[:14])
    ax[0].set_title(f"{sym}: price, SMA20/50, Bollinger(20,2) - last 260 sessions to {df.index[-1].date()}"); ax[0].legend(ncol=3, fontsize=8)
    ax[1].plot(d.index, d.z20, label="z20"); ax[1].plot(d.index, d.z50, label="z50", alpha=.7)
    for y in (-2, 2): ax[1].axhline(y, color="gray", ls="--", lw=.8)
    ax[1].legend(fontsize=8); ax[1].set_ylabel("deviation (SD)")
    ax[2].plot(d.index, d.pct20 * 100, label="% from SMA20"); ax[2].plot(d.index, d.pct50 * 100, label="% from SMA50")
    ax[2].axhline(0, color="gray", lw=.8); ax[2].legend(fontsize=8); ax[2].set_ylabel("%")
    plt.tight_layout(); plt.savefig(f"{sym}_bands.png", dpi=110); plt.close()

def chart_fwd(df, sym, sigs):
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
    for a, h in zip(ax, (10, 20)):
        allr = fwd(df, h).dropna() * 100; a.hist(allr, bins=40, alpha=.35, color="gray", density=True, label="all days (baseline)")
        for k, c in (("R1 lower breach  (z20<=-2)", "red"), ("R3 upper breach  (z20>=+2)", "green")):
            ix = dedupe(sigs[k]); r = fwd(df, h).iloc[ix].dropna() * 100
            a.scatter(r, np.full(len(r), 0.002 if c == "red" else 0.004), c=c, s=18, alpha=.7, label=f"{k[:2]} events (n={len(r)})")
        a.set_title(f"{sym}: {h}-day forward return"); a.set_xlabel("%"); a.legend(fontsize=8)
    plt.tight_layout(); plt.savefig(f"{sym}_forward.png", dpi=110); plt.close()

def main():
    out = []
    P = lambda *a: out.append(" ".join(str(x) for x in a))
    for sym in ("ETHOSLTD", "KDDL"):
        df = feats(load(sym)); sigs = signals(df)
        P(f"\n===== {sym} =====  bars={len(df)} {df.index[0].date()}..{df.index[-1].date()}  missing-close dropped; gaps>5d:",
          [(a.date().isoformat(), b.date().isoformat()) for a, b in zip(df.index[:-1], df.index[1:]) if (b - a).days > 5])
        jumps = (df.close.pct_change().abs() > .18)
        P("daily |moves|>18%:", [(d.date().isoformat(), round(v * 100, 1)) for d, v in df.close.pct_change()[jumps].items()])
        df.to_csv(f"{sym}_indicators.csv")
        L = df.iloc[-1]
        P(f"LATEST {df.index[-1].date()} close={L.close:.1f} open={L.open:.1f} hi={L.high:.1f} lo={L.low:.1f} vol={int(L.vol)} (20d avg vol={int(df.vol.iloc[-21:-1].mean())})")
        P(f" SMA20={L.sma20:.1f} SMA50={L.sma50:.1f} SMA200={L.sma200:.1f} | %vs20={L.pct20*100:.2f} %vs50={L.pct50*100:.2f} %vs200={L.pct200*100:.2f}")
        P(f" z20={L.z20:.2f} z50={L.z50:.2f} z200={L.z200:.2f} | UB={L.ub:.1f} LB={L.lb:.1f} %B={L.pctb:.2f} bandwidth={L.bw*100:.1f}% (bw pctile in history {(df.bw<L.bw).mean()*100:.0f})")
        P(" last 8 closes/z20:", [(d.strftime('%m-%d'), round(c), round(z, 2)) for d, c, z in zip(df.index[-8:], df.close[-8:], df.z20[-8:])])
        P(" signal days (raw) all history:", {k: int(v.sum()) for k, v in sigs.items()})
        P(" days since last: ", {k: int(len(df) - 1 - np.flatnonzero(v.values)[-1]) if v.any() else None for k, v in sigs.items()})
        for label, lo, hi in (("FULL", None, None), ("IN-SAMPLE", "1900-01-01", OOS[sym]), ("OUT-OF-SAMPLE", OOS[sym], "2100-01-01")):
            t = table(df, sigs, lo, hi); P(f"\n--- {sym} {label} (events deduped: >= {LOCK} sessions apart). fwd return from signal close ---")
            P(f"{'rule':44s} {'h':>3s} {'raw':>4s} {'n':>3s} {'mean':>8s} {'median':>8s} {'hit':>6s} {'worst':>8s} {'avgMAE':>8s} {'base':>8s} {'excess':>8s} {'90%CIlo':>8s} {'hi':>8s}")
            for _, r in t.iterrows():
                if r.n == 0: continue
                P(f"{r.rule:44s} {int(r.h):3d} {int(r.raw):4d} {int(r.n):3d} {fmt(r['mean'])} {fmt(r.med)} {r.hit*100:5.0f}% {fmt(r.worst)} {fmt(r.mae)} {fmt(r.base)} {fmt(r.excess)} {fmt(r.lo)} {fmt(r.hi)}")
        P(f"\n--- {sym} trade sim: entry next open +0.15% slip, exit close of 10th session -0.15% slip, STT/stamp/exch/DP incl., pre-income-tax ---")
        for k in ("R1 lower breach  (z20<=-2)", "R2 lower re-entry (prev z20<=-2, now >-2)", "R3 upper breach  (z20>=+2)"):
            for lab, lo, hi in (("full", "1900", "2100"), ("OOS", OOS[sym], "2100")):
                m = sigs[k].copy(); m[(m.index < lo) | (m.index >= hi)] = False
                P(f" {k[:42]:42s} {lab:4s}", {a: (round(b, 4) if isinstance(b, float) else b) for a, b in trade_sim(df, m).items()})
        # sensitivity: adjusted series
        d2 = load(sym); d2["close_u"] = d2.close; d2["close"] = d2.adj; d2 = feats(d2)
        s2 = signals(d2); P(" sensitivity (adjclose) raw counts R1/R3:", int(s2['R1 lower breach  (z20<=-2)'].sum()), int(s2['R3 upper breach  (z20>=+2)'].sum()),
                           "vs unadjusted", int(sigs['R1 lower breach  (z20<=-2)'].sum()), int(sigs['R3 upper breach  (z20>=+2)'].sum()))
        chart(df, sym, sigs); chart_fwd(df, sym, sigs)
        # Alert condition status
        P(f"\n ALERT STATUS {sym}: lower breach now={bool(sigs['R1 lower breach  (z20<=-2)'].iloc[-1])} upper breach now={bool(sigs['R3 upper breach  (z20>=+2)'].iloc[-1])} z50>=2 now={bool(sigs['R5 stretched above SMA50 (z50>=+2)'].iloc[-1])} z50<=-2 now={bool(sigs['R6 stretched below SMA50 (z50<=-2)'].iloc[-1])}")
    open("results.txt", "w", encoding="utf-8").write("\n".join(out)); print("\n".join(out))


if __name__ == "__main__": main()
