"""
PAXG DAY-TRADE (CoinDCX futures B-PAXG_USDT, 15m): session range breakouts, har trade usi din band.

 S1  London breakout of Asia range : range 00:00-07:00 UTC, entry 07:00-12:00, time-exit 16:00 UTC
 S2  NY-open range breakout        : range 12:00 + L min, entry upto 17:00 UTC,    time-exit 20:00 UTC
 Dono: sirf Mon-Fri, din me max 1 trade, long + short, close se breakout -> agli candle ke open par entry.
 Stop = range ka doosra kinara (opp) ya range ka beech (mid). Target = RR x stop. Option: daily EMA20 trend filter.

SAKHT TEST: config pehle 60% (TRAIN) se chuna jaata hai, phir baaki 40% (TEST) par bina badle dekha jaata hai.
Fee scenarios: 0% (gross) | maker 0.025% | taker 0.075% har taraf (CoinDCX futures standard) + 0.03% slippage.

Run:  python paxg_intraday_v3.py                    (data: results/data/PAXGUSDT_15m_cache.csv)
      python paxg_intraday_v3.py --csv PAXGUSDT_15m_cache.csv --slippage 0.0005
Disclaimer: educational. Backtest future ki guarantee nahi hai. Leverage me poori capital doob sakti hai.
"""
import argparse
import itertools

import numpy as np
import pandas as pd

FEES = {"gross": 0.0, "maker": 0.00025, "taker": 0.00075}
MIN_DIST, MAX_DIST = 0.003, 0.03       # stop distance 0.3% se 3% (fee/stop ratio ko control me rakhne ke liye)


def load(path):
    d = pd.read_csv(path)
    d["time"] = pd.to_datetime(d["time"], unit="ms")
    return d.sort_values("time").drop_duplicates("time").set_index("time")


def prep(d):
    idx = d.index
    dates = idx.normalize().values.astype("datetime64[D]").astype("int64")
    starts = np.r_[0, np.flatnonzero(np.diff(dates)) + 1]
    ends = np.r_[starts[1:], len(d)]
    dclose = d.close.resample("1D").last().dropna()
    e20 = dclose.ewm(span=20, adjust=False).mean()
    tr = np.sign(dclose - e20).shift(1).fillna(0)            # kal tak ka daily trend (aaj ki jaankari nahi)
    trend = {int(k): v for k, v in zip(dclose.index.values.astype("datetime64[D]").astype("int64"), tr.values)}
    return dict(o=d.open.values, h=d.high.values, l=d.low.values, c=d.close.values,
                m=(idx.hour * 60 + idx.minute).values, dow=idx.dayofweek.values, dates=dates,
                starts=starts, ends=ends, trend=trend)


def trades_for(P, cfg, slip):
    o, h, l, c, m = P["o"], P["h"], P["l"], P["c"], P["m"]
    fam = cfg["fam"]
    if fam == "S1":
        r0, r1, e1, x1 = 0, 420, 720, 960
    else:
        r0, r1, e1, x1 = 720, 720 + cfg["L"], 1020, 1200
    need = int(0.8 * (r1 - r0) / 15)
    out = []
    for s, e in zip(P["starts"], P["ends"]):
        if P["dow"][s] >= 5:
            continue
        mm = m[s:e]
        rm = (mm >= r0) & (mm < r1)
        if rm.sum() < need:
            continue
        rh, rl = h[s:e][rm].max(), l[s:e][rm].min()
        width = (rh - rl) / c[s:e][rm][-1] * 100
        if width < cfg["min_range"]:
            continue
        wm = np.flatnonzero((mm >= r1) & (mm < e1))
        if len(wm) == 0:
            continue
        idxs = s + wm
        up, dn = c[idxs] > rh, c[idxs] < rl
        if cfg["trend"]:
            tv = P["trend"].get(int(P["dates"][s]), 0)
            up, dn = up & (tv > 0), dn & (tv < 0)
        hit = np.flatnonzero(up | dn)
        if len(hit) == 0:
            continue
        k = hit[0]
        side = 1 if up[k] else -1
        i0 = idxs[k] + 1
        if i0 >= e or m[i0] >= x1:
            continue
        entry = o[i0] * (1 + slip * side)
        sm = cfg["stop"]
        if sm == "opp":
            stop = rl if side == 1 else rh
        elif sm == "mid":
            stop = (rh + rl) / 2
        else:                                              # fixed-dollar stop
            stop = entry - side * float(sm)
        dist = side * (entry - stop)
        if not dist > 0:
            continue
        if sm in ("opp", "mid") and not (MIN_DIST <= dist / entry <= MAX_DIST):
            continue
        tp = entry + side * cfg["tp_usd"] if cfg.get("tp_usd") else entry + side * cfg["rr"] * dist
        jm = np.flatnonzero(m[i0:e] < x1)
        jend = i0 + jm[-1]
        sl_, sh_ = l[i0:jend + 1], h[i0:jend + 1]
        hs, ht = (sl_ <= stop, sh_ >= tp) if side == 1 else (sh_ >= stop, sl_ <= tp)
        fs = np.argmax(hs) if hs.any() else 10**9
        ft = np.argmax(ht) if ht.any() else 10**9
        if fs <= ft and fs < 10**9:
            px = min(o[i0 + fs], stop) if side == 1 else max(o[i0 + fs], stop)
            if fs == 0:
                px = stop
            why = "SL"
        elif ft < 10**9:
            px, why = tp, "TP"
        else:
            px, why = c[jend], "TIME"
        px *= (1 - slip * side)
        out.append((P["dates"][s], side, entry, px, dist, why))
    return np.array(out, dtype=object) if out else np.zeros((0, 6), dtype=object)


def net_R(tr, fee):
    side, entry, px, dist = (tr[:, i].astype(float) for i in (1, 2, 3, 4))
    return side * (px - entry) / dist - fee * (entry + px) / dist


def net_usd(tr, fee):
    """Net dollar P/L per 1 PAXG per trade (fee + slippage ke baad)."""
    side, entry, px = (tr[:, i].astype(float) for i in (1, 2, 3))
    return side * (px - entry) - fee * (entry + px)


def pf_of(x):
    if len(x) == 0:
        return np.nan
    gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
    return gp / gl if gl > 0 else (np.inf if gp > 0 else np.nan)


def maxdd(R):
    if len(R) == 0:
        return 0.0
    cum = np.cumsum(R)
    return float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min())


def stats(tr, fee, risk):
    R = net_R(tr, fee)
    return dict(n=len(R), win=(R > 0).mean() * 100 if len(R) else np.nan, pf=pf_of(R),
                expR=R.mean() if len(R) else np.nan, ret=R.sum() * risk * 100, dd=maxdd(R) * risk * 100)


def usd_mode(P, d, a, cut):
    px = d.close.iloc[-1]
    print(f"FIXED-DOLLAR MODE | PAXG ~${px:.0f}: $10 = {10 / px * 100:.2f}% , $20 = {20 / px * 100:.2f}% | "
          f"taker round-trip fee ${2 * 0.00075 * px:.1f}, maker ${2 * 0.00025 * px:.1f} (+ slippage ${2 * a.slippage * px:.1f})\n")
    cfgs = []
    for tp, sl, tr in itertools.product((10, 20), (10, 20, "opp"), (0, 1)):
        cfgs.append(dict(fam="S1", L=0, min_range=0.0, stop=sl, tp_usd=tp, trend=tr))
    for L, tp, sl, tr in itertools.product((60, 120), (10, 20), (10, 20, "opp"), (0, 1)):
        cfgs.append(dict(fam="S2", L=L, min_range=0.0, stop=sl, tp_usd=tp, trend=tr))
    rows = []
    for cfg in cfgs:
        tr = trades_for(P, cfg, a.slippage)
        if len(tr) == 0:
            continue
        is_tr = tr[:, 0].astype(int) < cut
        row = dict(fam=cfg["fam"], L=cfg["L"], tp=cfg["tp_usd"], sl=str(cfg["stop"]), trend=cfg["trend"],
                   n=len(tr), n_te=int((~is_tr).sum()))
        for tag, sub in (("tr", tr[is_tr]), ("te", tr[~is_tr])):
            row[f"{tag}_pf_gross"] = pf_of(net_usd(sub, 0.0)) if len(sub) else np.nan
            row[f"{tag}_pf_maker"] = pf_of(net_usd(sub, 0.00025)) if len(sub) else np.nan
            row[f"{tag}_pf_taker"] = pf_of(net_usd(sub, 0.00075)) if len(sub) else np.nan
            row[f"{tag}_win"] = (net_usd(sub, 0.00075) > 0).mean() * 100 if len(sub) else np.nan
            row[f"{tag}_usd_taker"] = net_usd(sub, 0.00075).mean() if len(sub) else np.nan
            row[f"{tag}_usd_maker"] = net_usd(sub, 0.00025).mean() if len(sub) else np.nan
        rows.append(row)
    r = pd.DataFrame(rows)
    r.to_csv(a.out, index=False)
    show = ["fam", "L", "tp", "sl", "trend", "n", "tr_win", "tr_pf_taker", "n_te", "te_win", "te_pf_gross", "te_pf_maker",
            "te_pf_taker", "te_usd_maker", "te_usd_taker"]
    print("===== TRAIN vs TEST (te_usd = TEST me ek trade ka net $ per 1 PAXG, fee+slippage ke baad) =====")
    print(r.sort_values("te_pf_taker", ascending=False)[show].round(2).to_string(index=False))
    print(f"\nTEST me taker fee par PF>1: {(r.te_pf_taker > 1).sum()}/{len(r)} | maker fee par PF>1: {(r.te_pf_maker > 1).sum()}/{len(r)} "
          f"| TRAIN me taker PF>1: {(r.tr_pf_taker > 1).sum()}/{len(r)}")
    print(f"Saare configs CSV me: {a.out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results/data/PAXGUSDT_15m_cache.csv")
    ap.add_argument("--slippage", type=float, default=0.0003)
    ap.add_argument("--risk", type=float, default=0.005)
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--min-train", type=int, default=40)
    ap.add_argument("--out", default="paxg_intraday_all_configs.csv")
    ap.add_argument("--usd", action="store_true", help="fixed $10 / $20 target mode")
    a = ap.parse_args()

    d = load(a.csv)
    P = prep(d)
    days = (d.index[-1] - d.index[0]).days
    dmin, dmax = int(P["dates"][0]), int(P["dates"][-1])
    cut = dmin + int(a.train_frac * (dmax - dmin))
    print(f"PAXG B-PAXG_USDT 15m | {len(d)} candles | {d.index[0].date()} -> {d.index[-1].date()} ({days} din)")
    print(f"TRAIN {d.index[0].date()} -> {pd.Timestamp(cut, unit='D').date()} | TEST baaki | slippage {a.slippage * 100:.2f}% "
          f"| risk {a.risk * 100:g}%/trade | max 1 trade/din, Mon-Fri\n")

    if a.usd:
        return usd_mode(P, d, a, cut)
    cfgs = []
    for mr, st, rr, tr in itertools.product((0.0, 0.7), ("opp", "mid"), (1.0, 1.5, 2.5), (0, 1)):
        cfgs.append(dict(fam="S1", L=0, min_range=mr, stop=st, rr=rr, trend=tr))
    for L, mr, st, rr, tr in itertools.product((60, 90, 120), (0.0, 0.4), ("opp", "mid"), (1.0, 1.5, 2.5), (0, 1)):
        cfgs.append(dict(fam="S2", L=L, min_range=mr, stop=st, rr=rr, trend=tr))

    rows, store = [], {}
    for i, cfg in enumerate(cfgs):
        tr = trades_for(P, cfg, a.slippage)
        store[i] = tr
        if len(tr) == 0:
            continue
        is_tr = tr[:, 0].astype(int) < cut
        row = dict(cfg=i, **cfg)
        for tag, sub in (("tr", tr[is_tr]), ("te", tr[~is_tr])):
            for fn, fee in FEES.items():
                s = stats(sub, fee, a.risk)
                for k, v in s.items():
                    row[f"{tag}_{fn}_{k}"] = v
        row["long_share"] = (tr[:, 1].astype(int) == 1).mean() * 100
        row["avg_dist_pct"] = (tr[:, 4].astype(float) / tr[:, 2].astype(float)).mean() * 100
        rows.append(row)
    r = pd.DataFrame(rows)
    r.to_csv(a.out, index=False)
    r = r[r.tr_taker_n >= a.min_train]

    print(f"===== {len(r)} CONFIGS (S1 London-Asia breakout, S2 NY-open breakout) =====")
    print("Gross edge check (fee 0%, TEST): median PF  S1 {:.2f} | S2 {:.2f}".format(
        r[r.fam == 'S1'].te_gross_pf.median(), r[r.fam == 'S2'].te_gross_pf.median()))
    for fn in ("maker", "taker"):
        print(f"{fn:5s} fee, TEST median PF: S1 {r[r.fam == 'S1'][f'te_{fn}_pf'].median():.2f} | "
              f"S2 {r[r.fam == 'S2'][f'te_{fn}_pf'].median():.2f} | PF>1 configs: "
              f"{(r[f'te_{fn}_pf'] > 1).sum()}/{len(r)}")
    print("\n--- Filter ka asar (TEST taker PF median) ---")
    print(r.groupby(["fam", "trend"]).te_taker_pf.median().round(2).to_string())
    print(r.groupby(["fam", "stop"]).te_taker_pf.median().round(2).to_string())

    top = r.sort_values("tr_taker_pf", ascending=False).head(8)
    show = ["fam", "L", "min_range", "stop", "rr", "trend", "tr_taker_n", "tr_taker_pf", "te_taker_n", "te_taker_win",
            "te_gross_pf", "te_maker_pf", "te_taker_pf", "te_taker_expR", "te_taker_ret", "te_taker_dd"]
    print("\n===== TRAIN (taker fee) ke TOP 8 -- unka TEST (bina dekha data) =====")
    print(top[show].round(2).to_string(index=False))
    good = top[(top.te_taker_pf >= 1.15) & (top.te_taker_n >= 40)]
    print("\n===== VERDICT =====")
    if len(good):
        print(f"{len(good)}/8 top configs TEST me taker fee (0.075%) ke baad bhi chale (PF>=1.15, 40+ trades).")
    else:
        print("Koi top config TEST me taker fee ke baad nahi chala (PF>=1.15, 40+ trades).")
    best_te = r.sort_values("te_taker_pf", ascending=False).head(3)
    print("\n(Sirf jaankari: TEST me sabse achhe 3 -- ye chuna hua hai, isliye bharosa mat karo)")
    print(best_te[show].round(2).to_string(index=False))
    print(f"\nSaare configs CSV me: {a.out}")


if __name__ == "__main__":
    main()
