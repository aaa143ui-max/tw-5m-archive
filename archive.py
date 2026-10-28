"""台股 5 分K 每日存檔（2026-10-28 RAY：「每天存5分K」）。
為什麼：Yahoo 5 分K 只保留最近 60 天，馬丁「突破開盤價」這類盤中進場要長資料才能回測。
做法：
  1. tickers.txt＝上市櫃全部代號（.TW／.TWO）。先抓最近 5 天日K，挑「前一交易日成交量 ≥ 1,000 張、收盤 ≥ 10 元」的
     （比 RAY 的 3,000 張／20 元寬，之後回測可以再收緊）。
  2. 抓這些股票的 5 分K（存檔少於 5 天時抓 60 天補齊，平常抓 7 天），按日期存 data/5m/YYYY-MM-DD.csv.gz；
     已存在的日期不覆蓋（今天的檔要等收盤後才寫，避免存到盤中半套）。
  3. 同時存當天日K（官方收盤／昨收要用）data/daily/YYYY-MM-DD.csv。
健康檢查：最新存檔日期離今天（台北）超過 6 天 → 失敗（GitHub 寄信）。Yahoo 保留 60 天，漏幾天下次會自動補回。"""
import datetime as dt, os, sys, time
import pandas as pd, yfinance as yf

TPE = dt.timezone(dt.timedelta(hours=8))
now = dt.datetime.now(TPE)
today = now.date()
D5, DD = "data/5m", "data/daily"
os.makedirs(D5, exist_ok=True); os.makedirs(DD, exist_ok=True)
have = sorted(f[:10] for f in os.listdir(D5) if f.endswith(".csv.gz"))
period = os.environ.get("PERIOD") or ("60d" if len(have) < 5 else "7d")   # PERIOD=60d 可手動補漏

tick = [t.strip() for t in open("tickers.txt") if t.strip()]


def dl(ts, **kw):
    for i in range(3):
        try:
            return yf.download(ts, progress=False, group_by="ticker", auto_adjust=False, threads=True, **kw)
        except Exception as e:
            print("重試", i, e, flush=True); time.sleep(10)
    return pd.DataFrame()


# 1) 日K → 挑宇宙，順便存日K
day_rows, pick = [], set()
for i in range(0, len(tick), 200):
    ch = tick[i:i + 200]
    y = dl(ch, period="1mo" if period == "7d" else "3mo", interval="1d")
    for t in ch:
        try:
            b = y[t].dropna(subset=["Open", "Close"])
        except Exception:
            continue
        if b.empty:
            continue
        done = b[b.index.date < today] if now.hour < 14 else b   # 14:00 前今天的日K還沒收完
        if len(done) and done.Volume.iloc[-1] >= 1_000_000 and done.Close.iloc[-1] >= 10:
            pick.add(t)
        for ts, r in b.iterrows():
            day_rows.append((ts.date().isoformat(), t, round(r.Open, 2), round(r.High, 2), round(r.Low, 2), round(r.Close, 2), int(r.Volume) if r.Volume == r.Volume else 0))
print("宇宙", len(pick), "／", len(tick), flush=True)
if len(pick) < 100:
    sys.exit(f"宇宙只有 {len(pick)} 檔，Yahoo 可能擋了")

DY = pd.DataFrame(day_rows, columns=["date", "code", "open", "high", "low", "close", "volume"])

# 2) 5 分K
rows, got = [], set()


def grab(ch):
    x = dl(ch, period=period, interval="5m")
    for t in ch:
        try:
            a = x[t].dropna(subset=["Open", "Close"])
        except Exception:
            continue
        if a.empty:
            continue
        a = a.tz_convert("Asia/Taipei") if a.index.tz is not None else a.tz_localize("UTC").tz_convert("Asia/Taipei")
        got.add(t)
        for ts, r in a.iterrows():
            rows.append((ts.date().isoformat(), t, ts.strftime("%H:%M"), round(r.Open, 2), round(r.High, 2), round(r.Low, 2), round(r.Close, 2), int(r.Volume) if r.Volume == r.Volume else 0))


pk = sorted(pick)
for i in range(0, len(pk), 50):
    grab(pk[i:i + 50]); print(i, len(rows), flush=True)
miss = [t for t in pk if t not in got]   # 整批被 Yahoo 限流擋掉的，慢慢一檔一檔重抓
for t in miss:
    time.sleep(2); grab([t])
print("重抓", len(miss), "→ 仍缺", len([t for t in pk if t not in got]), flush=True)
M = pd.DataFrame(rows, columns=["date", "code", "time", "open", "high", "low", "close", "volume"])

new = []
for d, g in M.groupby("date"):
    if d == today.isoformat() and now.hour < 14:
        continue   # 盤中半套不存
    f, fd = f"{D5}/{d}.csv.gz", f"{DD}/{d}.csv"
    g = g.drop(columns="date")
    dd = DY[(DY.date == d) & DY.code.isin(g.code.unique())].drop(columns="date").drop_duplicates("code")
    if os.path.exists(f):   # 已存的日期只補「之前漏掉的股票」，不改舊資料
        old = pd.read_csv(f)
        g = g[~g.code.isin(old.code.unique())]
        if g.empty:
            continue
        g = pd.concat([old, g])
        if os.path.exists(fd):
            od = pd.read_csv(fd); dd = pd.concat([od, dd[~dd.code.isin(od.code)]])
    g.sort_values(["code", "time"]).to_csv(f, index=False, compression="gzip")
    dd.to_csv(fd, index=False)
    new.append(f"{d}（{g.code.nunique()} 檔）")
print("新存／補：", "、".join(new) or "無", flush=True)

have = sorted(f[:10] for f in os.listdir(D5) if f.endswith(".csv.gz"))
if not have:
    sys.exit("一天都沒存到")
gap = (today - dt.date.fromisoformat(have[-1])).days
print("存檔", len(have), "天，最新", have[-1], flush=True)
if gap > 6:
    sys.exit(f"最新存檔 {have[-1]} 已經 {gap} 天沒更新")
