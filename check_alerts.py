"""Daily post-close check of the research watch conditions in REPORT.md. Prints JSON: {"asof":..,"triggers":[..],"errors":[..]}.
Does not notify by itself. State: alert_state.json (armed flag + last fired date per stock/condition).
First run initialises conditions that are ALREADY met as disarmed (no alert), so only new episodes fire."""
import json, os, sys, datetime as dt, urllib.request
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "alert_state.json")
CONDS = {"A_upper_breach": ("z20", lambda z: z >= 2), "B_lower_breach": ("z20", lambda z: z <= -2), "C_stretch_sma50": ("z50", lambda z: z >= 2)}
LOCKOUT = 20

def fetch(sym):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS?range=2y&interval=1d"
    j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    df = pd.DataFrame({"close": q["close"], "vol": q["volume"]}, index=pd.to_datetime([dt.datetime.fromtimestamp(t).date() for t in j["timestamp"]])).dropna()
    # drop today's bar if the session is not complete (before 15:45 IST on a weekday); Yahoo returns the in-progress bar
    now = dt.datetime.now();
    if df.index[-1].date() == now.date() and now.time() < dt.time(15, 45): df = df.iloc[:-1]
    for n in (20, 50):
        df[f"z{n}"] = (df.close - df.close.rolling(n).mean()) / df.close.rolling(n).std(ddof=0)
    return df

def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else None; first = state is None; state = state or {}
    out = {"triggers": [], "errors": [], "asof": None, "status": {}}
    for sym in ("ETHOSLTD", "KDDL"):
        try: df = fetch(sym)
        except Exception as e: out["errors"].append(f"{sym}: {e}"); continue
        last = df.iloc[-1]; day = df.index[-1].date().isoformat(); out["asof"] = day
        volr = float(last.vol / df.vol.iloc[-21:-1].mean())
        out["status"][sym] = {"date": day, "close": round(float(last.close), 1), "z20": round(float(last.z20), 2), "z50": round(float(last.z50), 2), "vol_ratio": round(volr, 1)}
        for name, (metric, test) in CONDS.items():
            s = state.setdefault(f"{sym}.{name}", {"armed": True, "last_fired": None}); z = float(last[metric]); met = bool(test(z))
            if first and met: s.update(armed=False, last_fired=day); continue          # already in an episode at setup
            if s["armed"] and met and s["last_fired"] != day:
                s.update(armed=False, last_fired=day)
                out["triggers"].append(f"{sym} {name}: {metric}={z:.2f} close={last.close:.1f} vol x{volr:.1f} (date {day}). Watch signal for review only, not a buy/sell instruction.")
            elif not s["armed"] and abs(z) < 1:
                lf = s["last_fired"]; gap = (df.index[-1].date() - dt.date.fromisoformat(lf)).days if lf else 99
                if gap >= 28: s["armed"] = True                                         # ~20 sessions
    json.dump(state, open(STATE, "w"), indent=1)
    print(json.dumps(out)); sys.exit(1 if out["errors"] and not out["status"] else 0)

if __name__ == "__main__": main()
