"""Read-only: CoinDCX futures 5m candles (public endpoint, no keys, no orders). Research only / educational."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "smc_v2_backtest"))
import coindcx_fetch as cf

days = int(sys.argv[1]) if len(sys.argv) > 1 else 580
out = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(out, exist_ok=True)
for pair, name in [("B-PAXG_USDT", "PAXGUSDT")]:
    t0 = time.time()
    df = cf.fetch(pair, "5", days)
    print(pair, len(df), "candles", round(time.time() - t0), "s")
    if len(df):
        df.to_csv(os.path.join(out, f"{name}_5m_cache.csv"), index=False)
        print(df.time.iloc[0], df.time.iloc[-1])
