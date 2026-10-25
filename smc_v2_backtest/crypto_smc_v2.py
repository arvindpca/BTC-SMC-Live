"""
SMC V2 -- 15M | CoinDCX (crypto futures) | BTC HTF | 24H POC | Trailing Strategy
NSE image wali strategy ka crypto version (NIFTY -> BTC, stocks -> coins).

Flow:  1H BTC trend (EMA20 + HH/HL structure)  ->  24H rolling volume-profile POC
       ->  15M SMC: Liquidity Sweep -> Displacement(>=1 ATR) -> MSS -> FVG/OB -> Retest -> Confirmation
       ->  entry next 15M open  ->  structure stop  ->  5 exit variants
Filters (4) x Exits (5) = 20 variants.

Risk: 0.5% per trade | max 5 new entries/day | no duplicate position per coin | daily loss limit 1%.
Fees (CoinDCX futures standard tier): taker 0.075% / maker 0.025% per side + 0.02% slippage. Default = taker 0.075%.
      Summary me maker 0.025% (best case) aur 0.10% (stress) par bhi PF dikhta hai. Asli fee CoinDCX fees page se confirm karo.
Capital: --capital USDT me (1 lakh INR ~ 1100 USDT, apne rate se badlo). % result capital se nahi badalta.
Data: local CSV (<SYMBOL>_15m_cache.csv, jo pehle download ho chuke hain). BTCUSDT zaroori hai, warna error.

Run:  python crypto_smc_v2.py --fetch                  (BTC + PAXG; PAXG ka 15m CSV na ho to Binance se download)
      python crypto_smc_v2.py --poc-min-fill 0.8       (PAXG ke candle gaps ke liye POC rule thoda dheela)
paxg_backtest.py isi folder me hona chahiye.
Disclaimer: educational. Backtest future ki guarantee nahi hai. Leverage me poori capital doob sakti hai.
"""
import argparse
import heapq
import os
import warnings

import numpy as np
import pandas as pd

import paxg_backtest as base

warnings.filterwarnings("ignore")

FILTERS = {
    "ALL_FILTERS": dict(mkt=True, coin=True, poc=True),      # BTC + coin HTF + 24H POC
    "BTC_POC": dict(mkt=True, coin=False, poc=True),         # BTC + 24H POC   (NIFTY_POC ka crypto naam)
    "POC_ONLY": dict(mkt=False, coin=False, poc=True),
    "NO_TREND_NO_POC": dict(mkt=False, coin=False, poc=False),
}
EXITS = ["FIXED_1.5R", "FIXED_2R", "FIXED_3R", "TRAILING", "HYBRID"]
FEE_LO, FEE_HI = 0.00025, 0.0010


# ------------------------------------------------------------------ data ----
def load_coin(sym, a):
    path = os.path.join(a.data_dir, f"{sym}_15m_cache.csv")
    if not os.path.exists(path):
        if a.fetch:
            print(f"{sym}: 15m download ho raha hai (Binance, {a.days} din)...")
            base.fetch_binance(sym, "15m", a.days).to_csv(path, index=False)
        elif sym == a.market:
            raise SystemExit(f"ERROR: {path} nahi mila. {a.market} ka 15m CSV zaroori hai (market filter ke liye). "
                             f"Pehle crypto_orb.py chalao ya --fetch lagao.")
        else:
            print(f"{sym}: {path} nahi mila, skip (download ke liye --fetch).")
            return None
    return base._clean(pd.read_csv(path)).set_index("time")


def htf(df, rule):
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def to15(idx, s):
    return s.astype(float).reindex(idx, method="ffill").fillna(0).values > 0.5


def market_flags(df):
    """BTC 1H: bullish = close>EMA20 + HH&HL ; bearish = close<EMA20 + LH&LL. Sirf poori ho chuki 1H candle."""
    h1 = htf(df, "1h")
    e20 = base.ema(h1.close, 20)
    hi, lo = h1.high, h1.low
    ph = (hi == hi.rolling(5, center=True).max()).values
    pl = (lo == lo.rolling(5, center=True).min()).values
    hv, lv = hi.values, lo.values
    n = len(h1)
    shl, shp, sll, slp = (np.full(n, np.nan) for _ in range(4))
    H, L = [], []
    for j in range(n):
        p = j - 2
        if p >= 2:
            if ph[p]:
                H.append(hv[p])
            if pl[p]:
                L.append(lv[p])
        if len(H) >= 2:
            shl[j], shp[j] = H[-1], H[-2]
        if len(L) >= 2:
            sll[j], slp[j] = L[-1], L[-2]
    cl = h1.close.values
    bull = (cl > e20.values) & (shl > shp) & (sll > slp)
    bear = (cl < e20.values) & (shl < shp) & (sll < slp)
    return (pd.Series(bull, index=h1.index).astype(float).shift(1).fillna(0),
            pd.Series(bear, index=h1.index).astype(float).shift(1).fillna(0))


def coin_flags(df):
    h1 = htf(df, "1h")
    e20 = base.ema(h1.close, 20)
    return ((h1.close > e20).astype(float).shift(1).fillna(0), (h1.close < e20).astype(float).shift(1).fillna(0))


def confirmed_swings(h, l, k=3):
    hs, ls = pd.Series(h), pd.Series(l)
    ph = (hs == hs.rolling(2 * k + 1, center=True).max()).values
    pl = (ls == ls.rolling(2 * k + 1, center=True).min()).values
    n = len(h)
    sh, sl = np.full(n, np.nan), np.full(n, np.nan)
    p = np.flatnonzero(ph)
    p = p[p + k < n]
    sh[p + k] = h[p]
    p = np.flatnonzero(pl)
    p = p[p + k < n]
    sl[p + k] = l[p]
    return pd.Series(sl).ffill().values, pd.Series(sh).ffill().values


def rolling_poc(h, l, c, v, ts, win=96, nb=40, min_fill=1.0):
    """Rolling 24H volume profile POC (time-based window). Window me kam se kam min_fill x 96 candles
    na hon (gap) to NaN -> POC wala setup reject. Default 1.0 = poora 24H data (image wala rule)."""
    n = len(h)
    tp = (h + l + c) / 3
    step = 15 * 60 * 10**9
    need = int(np.ceil(min_fill * win))
    poc = np.full(n, np.nan)
    starts = np.searchsorted(ts, ts - step * (win - 1), side="left")
    for i in range(win - 1, n):
        i0 = starts[i]
        if i - i0 + 1 < need:
            continue
        s = slice(i0, i + 1)
        lo, hi = l[s].min(), h[s].max()
        if hi <= lo:
            continue
        hist, edges = np.histogram(tp[s], bins=nb, range=(lo, hi), weights=v[s])
        k = hist.argmax()
        poc[i] = (edges[k] + edges[k + 1]) / 2
    return poc


# ------------------------------------------------------------ SMC detect ----
def detect_long(o, h, l, c, atr, swl, swh, p):
    """Bullish SMC sequence: Sweep -> Displacement -> MSS -> FVG/OB -> Retest -> Confirmation.
    Short ke liye same function negated prices par chalta hai."""
    n = len(o)
    out = []
    st, sw_t, sw_low, mss, disp_t, ref_used, last_ref = 0, -1, 0.0, 0.0, -1, np.nan, np.nan
    zlo = zhi = 0.0
    tm = tt = 0
    for t in range(30, n - 1):
        a = atr[t]
        if not a > 0:
            continue
        if st == 0:
            ref = swl[t - 1]
            if ref == ref and l[t] < ref and c[t] > ref and ref != last_ref:      # 1. liquidity sweep
                shr = swh[t]
                if shr == shr and shr > c[t]:
                    st, sw_t, sw_low, mss, disp_t, ref_used = 1, t, l[t], shr, -1, ref
            continue
        if st == 1:
            if c[t] < sw_low or t - sw_t > p["sweep_win"]:
                st = 0
                continue
            if l[t] < sw_low:
                sw_low = l[t]
            if disp_t < 0 and c[t] > o[t] and (c[t] - o[t]) >= p["disp_atr"] * a:  # 2. displacement
                disp_t = t
            if disp_t >= 0 and c[t] > mss:                                          # 3. MSS
                zone = None
                for k in range(max(disp_t, sw_t + 1), t):                           # 4a. FVG
                    if l[k + 1] > h[k - 1]:
                        zone = (h[k - 1], l[k + 1])
                        break
                if zone is None:                                                    # 4b. OB (last bearish candle)
                    for k in range(disp_t - 1, sw_t - 1, -1):
                        if c[k] < o[k]:
                            zone = (l[k], h[k])
                            break
                if zone is not None and zone[1] < c[t]:
                    st, zlo, zhi, tm = 2, zone[0], zone[1], t
                else:
                    st = 0
            continue
        if st == 2:
            if c[t] < sw_low or t - tm > p["retest_win"]:
                st = 0
                continue
            if l[t] <= zhi:                                                         # 5. retest
                st, tt = 3, t
        if st == 3:
            if c[t] < sw_low or t > tt + p["confirm_win"]:
                st = 0
            elif c[t] > o[t] and c[t] > zhi:                                        # 6. confirmation close
                out.append((t, sw_low - 0.1 * a, zlo, zhi, sw_low))
                last_ref, st = ref_used, 0
    return out


def build_data(sym, df, mkt_bull, mkt_bear, p):
    idx = df.index
    o, h, l, c, v = (df[k].values.astype(float) for k in ["open", "high", "low", "close", "volume"])
    ts = idx.values.astype("datetime64[ns]").astype("int64")
    atr = base.atr(df.reset_index(drop=True), 14).values
    swl, swh = confirmed_swings(h, l, 3)
    cb, cr = coin_flags(df)
    d = dict(sym=sym, n=len(df), o=o, h=h, l=l, c=c, atr=atr, ts=ts,
             day=idx.values.astype("datetime64[D]").astype("int64"), hour=idx.hour.values,
             poc=rolling_poc(h, l, c, v, ts, min_fill=p["poc_min_fill"]),
             mb=to15(idx, mkt_bull), mr=to15(idx, mkt_bear), cb=to15(idx, cb), cr=to15(idx, cr))
    longs = detect_long(o, h, l, c, atr, swl, swh, p)
    shorts = detect_long(-o, -l, -h, -c, atr, -swh, -swl, p)
    cands = []
    for t, stop, *_ in longs:
        poc_ok = d["poc"][t] == d["poc"][t]
        cands.append(dict(sym=sym, side=1, sig=t, stop=stop, f_mkt=d["mb"][t], f_coin=d["cb"][t],
                          f_poc=poc_ok and c[t] > d["poc"][t]))
    for t, stop, *_ in shorts:
        poc_ok = d["poc"][t] == d["poc"][t]
        cands.append(dict(sym=sym, side=-1, sig=t, stop=-stop, f_mkt=d["mr"][t], f_coin=d["cr"][t],
                          f_poc=poc_ok and c[t] < d["poc"][t]))
    return d, cands


# ------------------------------------------------------------- simulation ----
def simulate_trade(d, i0, side, stop, kind, a, eq, fee):
    o, h, l, c, atr, n, slip = d["o"], d["h"], d["l"], d["c"], d["atr"], d["n"], a.slippage
    entry = o[i0] * (1 + slip * side)
    dist = side * (entry - stop)
    if not dist > 0 or dist / entry < a.min_stop_pct or dist > a.max_stop_atr * atr[i0 - 1]:
        return None
    risk_amt = eq * a.risk
    qty = min(risk_amt / dist, eq * a.lev_cap / entry)
    jend = min(i0 + a.max_hold, n - 1)
    mult = {"FIXED_1.5R": 1.5, "FIXED_2R": 2.0, "FIXED_3R": 3.0}.get(kind)
    tgt = entry + side * mult * dist if mult else None
    cur, frac, armed, ext, fills = stop, 1.0, False, entry, []
    for j in range(i0, jend + 1):
        if (side == 1 and l[j] <= cur) or (side == -1 and h[j] >= cur):
            px = min(o[j], cur) if side == 1 else max(o[j], cur)
            fills.append((j, px * (1 - slip * side), frac, "TRAIL" if armed else "SL"))
            frac = 0.0
            break
        if tgt is not None:
            if (side == 1 and h[j] >= tgt) or (side == -1 and l[j] <= tgt):
                fills.append((j, tgt * (1 - slip * side), frac, "TP"))
                frac = 0.0
                break
        else:
            fav = h[j] if side == 1 else l[j]
            ext = max(ext, fav) if side == 1 else min(ext, fav)
            if not armed and side * (fav - entry) >= dist:                          # +1R: breakeven + ATR trail
                armed = True
                if kind == "HYBRID":
                    fills.append((j, (entry + side * dist) * (1 - slip * side), 0.5, "PARTIAL"))
                    frac = 0.5
                cur = max(cur, entry) if side == 1 else min(cur, entry)
            if armed:
                trl = ext - side * a.trail_atr * atr[j]
                cur = max(cur, trl) if side == 1 else min(cur, trl)
    if frac > 0:
        fills.append((jend, c[jend] * (1 - slip * side), frac, "TIME"))
    gross = sum(side * (px - entry) * f * qty for _, px, f, _ in fills)
    fees = fee * qty * entry + sum(fee * px * f * qty for _, px, f, _ in fills)
    last = max(fills, key=lambda x: x[0])
    return dict(exit_idx=last[0], reason=last[3], entry=entry, stop=stop, dist=dist, qty=qty,
                exit=sum(px * f for _, px, f, _ in fills), pnl=gross - fees, risk_amt=risk_amt,
                lev=qty * entry / eq, bars=last[0] - i0 + 1)


def run_variant(cands, data, fcfg, kind, fee, a):
    eq, heap, cnt = a.capital, [], 0
    day_pnl, day_n, last_exit, rows = {}, {}, {}, []
    for c in cands:
        d = data[c["sym"]]
        i0 = c["sig"] + 1
        T = d["ts"][i0]
        while heap and heap[0][0] <= T:
            et, _, pnl, dy = heapq.heappop(heap)
            eq += pnl
            day_pnl[dy] = day_pnl.get(dy, 0.0) + pnl
        if (fcfg["mkt"] and not c["f_mkt"]) or (fcfg["coin"] and not c["f_coin"]) or (fcfg["poc"] and not c["f_poc"]):
            continue
        dy = d["day"][i0]
        if day_n.get(dy, 0) >= a.max_day or day_pnl.get(dy, 0.0) <= -a.daily_loss * eq:
            continue
        if i0 <= last_exit.get(c["sym"], -1):
            continue
        tr = simulate_trade(d, i0, c["side"], c["stop"], kind, a, eq, fee)
        if tr is None:
            continue
        cnt += 1
        xi = tr["exit_idx"]
        heapq.heappush(heap, (d["ts"][xi], cnt, tr["pnl"], d["day"][xi]))
        last_exit[c["sym"]] = xi
        day_n[dy] = day_n.get(dy, 0) + 1
        rows.append(dict(coin=c["sym"], side="LONG" if c["side"] == 1 else "SHORT",
                         entry_time=pd.Timestamp(T), exit_time=pd.Timestamp(d["ts"][xi]), entry=tr["entry"],
                         stop=tr["stop"], exit_avg=tr["exit"], reason=tr["reason"], bars=tr["bars"], pnl=tr["pnl"],
                         R=tr["pnl"] / tr["risk_amt"], lev=tr["lev"]))
    return pd.DataFrame(rows)


def pf_of(x):
    x = np.asarray(x, float)
    if len(x) == 0:
        return np.nan
    gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
    return gp / gl if gl > 0 else (np.inf if gp > 0 else np.nan)


def curve_of(tr, cap):
    if tr.empty:
        return pd.Series([cap], index=[pd.Timestamp("2000-01-01")])
    s = tr.sort_values("exit_time")
    return pd.Series(cap + s.pnl.cumsum().values, index=s.exit_time.values)


# ------------------------------------------------------------------ main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "PAXGUSDT"])
    ap.add_argument("--poc-min-fill", type=float, default=1.0,
                    help="24H window me kam se kam itna hissa candles ho (1.0 = poora; PAXG gaps ke liye 0.8 try karo)")
    ap.add_argument("--market", default="BTCUSDT", help="HTF trend filter wala coin (NIFTY ki jagah)")
    ap.add_argument("--data-dir", default=".")
    ap.add_argument("--out", default="SMC_V2_CRYPTO_OUTPUT")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--capital", type=float, default=1100.0, help="USDT (1 lakh INR ~ 1100 USDT)")
    ap.add_argument("--risk", type=float, default=0.005)
    ap.add_argument("--daily-loss", type=float, default=0.01)
    ap.add_argument("--max-day", type=int, default=5)
    ap.add_argument("--fee", type=float, default=0.00075)
    ap.add_argument("--slippage", type=float, default=0.0002)
    ap.add_argument("--lev-cap", type=float, default=5.0)
    ap.add_argument("--min-stop-pct", type=float, default=0.003, help="fee se bachne ke liye minimum stop distance")
    ap.add_argument("--max-stop-atr", type=float, default=4.0)
    ap.add_argument("--trail-atr", type=float, default=2.0)
    ap.add_argument("--max-hold", type=int, default=96, help="15m bars (96 = 24 ghante)")
    ap.add_argument("--disp-atr", type=float, default=1.0)
    ap.add_argument("--entry-hours", nargs=2, type=int, default=[0, 24], help="UTC entry window, jaise 7 20")
    ap.add_argument("--train-frac", type=float, default=0.6)
    a = ap.parse_args()
    p = dict(sweep_win=12, retest_win=20, confirm_win=3, disp_atr=a.disp_atr, poc_min_fill=a.poc_min_fill)
    os.makedirs(a.out, exist_ok=True)

    syms = list(dict.fromkeys([a.market] + a.symbols))
    frames = {s: load_coin(s, a) for s in syms}
    frames = {s: f for s, f in frames.items() if f is not None}
    manifest = []
    for s, f in frames.items():
        exp = int((f.index[-1] - f.index[0]) / pd.Timedelta("15min")) + 1
        manifest.append(dict(symbol=s, rows=len(f), start=f.index[0], end=f.index[-1],
                             missing_candles=exp - len(f), source=f"{s}_15m_cache.csv"))
    pd.DataFrame(manifest).to_csv(os.path.join(a.out, "00_DATA_MANIFEST.csv"), index=False)
    print(pd.DataFrame(manifest).to_string(index=False))

    mb, mr = market_flags(frames[a.market])
    data, cands = {}, []
    print("\nSMC setups dhoond raha hoon (har coin 20-60 second)...")
    for s, f in frames.items():
        data[s], cs = build_data(s, f, mb, mr, p)
        cands += cs
        n = len(cs)
        print(f"{s}: {n} raw SMC setups | BTC-trend ok {sum(x['f_mkt'] for x in cs)} | "
              f"coin-HTF ok {sum(x['f_coin'] for x in cs)} | POC ok {sum(x['f_poc'] for x in cs)}")
    h0, h1 = a.entry_hours
    cands = [c for c in cands if h0 <= data[c["sym"]]["hour"][c["sig"] + 1] < h1]
    cands.sort(key=lambda c: (data[c["sym"]]["ts"][c["sig"] + 1], c["sym"]))
    if not cands:
        print("Koi setup nahi mila. --disp-atr kam karke dekho.")
        return

    t_lo = min(d["ts"][0] for d in data.values())
    t_hi = max(d["ts"][-1] for d in data.values())
    cut = pd.Timestamp(int(t_lo + a.train_frac * (t_hi - t_lo)))
    months = (t_hi - t_lo) / 1e9 / 86400 / 30.4
    print(f"\nPeriod {pd.Timestamp(t_lo).date()} -> {pd.Timestamp(t_hi).date()} ({months:.1f} mahine) | "
          f"TRAIN/TEST cut {cut.date()} | fee {a.fee * 100:.3f}% | risk {a.risk * 100:g}% | "
          f"min stop {a.min_stop_pct * 100:g}% | lev cap {a.lev_cap:g}x")

    rows, curves = [], {}
    for fname, fcfg in FILTERS.items():
        for kind in EXITS:
            tr = run_variant(cands, data, fcfg, kind, a.fee, a)
            lo_tr = run_variant(cands, data, fcfg, kind, FEE_LO, a)
            hi_tr = run_variant(cands, data, fcfg, kind, FEE_HI, a)
            name = f"{fname}__{kind}"
            tr.to_csv(os.path.join(a.out, f"trades_{name}.csv"), index=False)
            cv = curve_of(tr, a.capital)
            curves[name] = cv
            if tr.empty:
                rows.append(dict(variant=name, filters=fname, exit=kind, trades=0))
                continue
            te, te_hi = tr[tr.entry_time >= cut], hi_tr[hi_tr.entry_time >= cut]
            dd = ((cv / cv.cummax()) - 1).min() * 100
            percoin = [pf_of(g.pnl.values) for _, g in tr.groupby("coin")]
            rows.append(dict(variant=name, filters=fname, exit=kind, trades=len(tr),
                             trades_per_month=len(tr) / months, win_pct=(tr.pnl > 0).mean() * 100,
                             pf=pf_of(tr.pnl.values), exp_R=tr.R.mean(),
                             net_return_pct=(cv.iloc[-1] / a.capital - 1) * 100, max_dd_pct=dd,
                             avg_lev=tr.lev.mean(), pf_train=pf_of(tr[tr.entry_time < cut].pnl.values),
                             pf_test=pf_of(te.pnl.values), test_trades=len(te),
                             pf_fee_lo=pf_of(lo_tr.pnl.values), pf_fee_hi=pf_of(hi_tr.pnl.values),
                             test_pf_fee_hi=pf_of(te_hi.pnl.values),
                             coins_pf_gt1=int(np.nansum(np.array(percoin) > 1))))
    s = pd.DataFrame(rows)
    s.to_csv(os.path.join(a.out, "01_VARIANT_SUMMARY.csv"), index=False)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axs = plt.subplots(len(FILTERS), len(EXITS), figsize=(18, 10), sharex=True)
        for i, fname in enumerate(FILTERS):
            for j, kind in enumerate(EXITS):
                ax, cv = axs[i, j], curves[f"{fname}__{kind}"]
                ax.plot(cv.index, cv.values, lw=1)
                ax.axvline(cut, color="r", ls="--", lw=0.7)
                ax.set_title(f"{fname}\n{kind}", fontsize=8)
                ax.tick_params(labelsize=6)
        fig.suptitle("Equity curves (closed trades). Red line = TRAIN | TEST", fontsize=11)
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "02_equity_curves.png"), dpi=110)
        ok = s[s.trades > 0]
        fig2, ax2 = plt.subplots(figsize=(14, 5))
        x = np.arange(len(ok))
        ax2.bar(x - 0.2, ok.pf.clip(upper=3), 0.4, label="PF full")
        ax2.bar(x + 0.2, ok.pf_test.clip(upper=3), 0.4, label="PF TEST")
        ax2.axhline(1, color="k", lw=1)
        ax2.set_xticks(x)
        ax2.set_xticklabels(ok.variant, rotation=70, ha="right", fontsize=7)
        ax2.legend()
        ax2.set_title("Profit factor (1.0 = barabari)")
        fig2.tight_layout()
        fig2.savefig(os.path.join(a.out, "03_pf_comparison.png"), dpi=110)
    except ImportError:
        print("matplotlib nahi mila, charts skip.")

    ok = s[s.trades > 0]
    show = ["variant", "trades", "win_pct", "pf", "exp_R", "net_return_pct", "max_dd_pct", "pf_train", "pf_test",
            "test_trades", "pf_fee_hi", "coins_pf_gt1"]
    print("\n===== 20 VARIANTS (fee {:.3f}%) -- PF full-period se sorted =====".format(a.fee * 100))
    print(ok.sort_values("pf", ascending=False)[show].round(2).to_string(index=False))
    print("\n--- Filter ka asar (median) ---")
    print(ok.groupby("filters")[["pf", "pf_test"]].median().round(2).to_string())
    print("\n--- Exit ka asar (median) ---")
    print(ok.groupby("exit")[["pf", "pf_test"]].median().round(2).to_string())
    good = ok[(ok.test_pf_fee_hi >= 1.15) & (ok.test_trades >= 60) & (ok.coins_pf_gt1 >= 2)]
    print("\n===== VERDICT =====")
    if len(good):
        print(f"{len(good)}/20 variants TEST period me fee 0.10% par bhi chale (PF>=1.15, 60+ trades, 2+ coins):")
        print(good[["variant", "trades", "pf", "pf_test", "test_pf_fee_hi", "net_return_pct", "max_dd_pct"]].round(2).to_string(index=False))
        print("Ye sanket hai, guarantee nahi. Pehle paper trade.")
    else:
        print("Koi variant TEST period me fee 0.10% par nahi chala (PF>=1.15, 60+ trades, 2+ coins).")
        print("Matlab is dor me ye SMC V2 crypto version fees ke baad bharosemand edge nahi dikha raha.")
    print(f"\nOutput folder: {a.out}  (00_DATA_MANIFEST, 01_VARIANT_SUMMARY, trades_*.csv, charts)")
    print("Dhyan: SMC ke pieces (sweep/MSS/FVG/OB) ki definitions meri hain; funding rate aur liquidation ignore hain.")


if __name__ == "__main__":
    main()
