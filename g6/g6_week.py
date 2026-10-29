"""G6 強弱排名多空：每週紙上跟單＋實測掛單簿滑價（2026-10-29 建，參數照回測鎖死，不改）。

規則（同 xsmomr_bt.py 的 G6，BingX 永續）：
  - 每週一 00:00 UTC 後跑。BTC 濾網：幣安現貨 BTC 最近一根已收完週K 收盤 < 最近 20 根已收完週K 收盤平均 → 這週空手。
  - 幣池：BingX USDT 永續、上市滿 30 天、30 天平均日成交額 ≥ 500 萬元、t-1／t-8／t-15／t-29 都有收盤；排除穩定幣與股票／商品類。
  - 排名：28 天報酬（週日收盤 ÷ 28 天前收盤 − 1）。n = max(3, 幣池檔數 × 10% 取整)。
  - 多最強 n 檔合計 0.5 倍、空最弱 n 檔合計 0.5 倍；用過去 30 天日報酬算組合年化波動，總曝險 × min(1, 40% ÷ 波動)。
紙上帳：起始 3,100 元（≈10 萬台幣），每週按上面權重換倉。
  成交價＝掛單簿實際吃單均價（只交易「權重變動」那部分），手續費 taker 0.05%／邊，資金費率照 BingX 實際紀錄。
另外記錄每檔在 300／1,000／5,000 元下單的單邊滑價（相對中間價），回答「每邊滑價 > 1.1% 就打平」那個問題。
用法：python g6_week.py        （正式週跑，會更新 state.json）
      python g6_week.py --dry  （測試，不動帳本）
"""
import json, math, os, sys, time, urllib.request, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
BX = "https://open-api.bingx.com/openApi/swap"
D = 86_400_000
LB, VOL_N, VOL_TGT, QV_MIN = 28, 30, 0.40, 5e6
FEE = 0.0005
START_EQ = 3100.0
SIZES = (300, 1000, 5000)
STABLE = {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "USDE", "EUR", "DAI", "PYUSD", "RLUSD", "USD1", "XUSD", "U", "USDS", "USTC_X",
          "PAXG", "XAUT", "XAU", "XAG", "BTCDOM", "DEFI", "FOOTBALL", "BLUEBIRD", "ALL"}
# 幣安標成「非幣」的標的（股票、ETF、商品…），2026-10 版
NONCOIN = set("""AAOI AAPL ABBV ACN ADBE AGPU AKAM ALAB ALL AMAT AMC AMD AMZN ANET ANTHROPIC APLD APP ARM ASML ASTS AVGO AXTI BABA BAC BBX BE
BITO BMNR BNC BOT BRKB BSP BTCDOM BWET BX BYD BZ CAT CBRS CEG CIEN CL COHR COIN COPPER COST CRCL CRDO CRM CRML CRWD CRWV CSCO CSOPSAMSUNG2L
CSOPSKHYNIX2L CVNA CXMT CYPH DDOG DEFI DELL DIS DJT DKNG DRAM EBAY EIDO EQT EWJ EWT EWY EWZ FLEX FLNC FWDI GDX GEV GIGADEV GLW GME GOOGL GPRO
GS GTLB HANMI HD HIMS HK0625 HK0700 HK0992 HK1810 HOOD HPE HUT HYUNDAI IBKR IBM INTC INTW IONQ IREN IWM JPM KLAC KO KODEX200 KORU KSTR KUAISHOU
LGELECTRONICS LITE LLY LRCX LYTE MARA MCD MDB MDT MEITUAN META MINIMAX MOONSHOT MP MRK MRNA MRVL MSFT MSTR MSTU MU MUU MVLL NATGAS NAVER NBIS
NET NFLX NKE NOK NOW NVDA NVDL NVO OKLO ONDS OPENAI ORCL OURA PANW PATH PAYP PDD PENG PLTR POPMART PREDICT PYPL QCOM QNTX QQQ RAM RDDT RDW
RIVN RKLB RUM SAMSUNG SAMSUNGEM SECZ SHAZ SHOP SKDD SKHY SKHYNIX SKUU SMCI SMH SNDK SNOW SNPS SNXX SOFI SONY SOXL SOXS SPCX SPY SQQQ STM STRC
STXX TBT TEAM TEM TENCENT TER TMF TQQQ TSLA TSLL TSM TTWO TWST TXN TZA UBER UNH UNITREE URNM USAR USDBRL USDEX UVXY V VKTX VRT VST WDC WEN
WMT XAG XAU XBI XLE XOM XPD XPT ZCSH ZHIPU ZHONGJI ZM ZS GOLD SILVER OIL WTI BRENT""".split())


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "g6-paper/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(2 + i * 3)


def bx(path, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    j = get(f"{BX}{path}?{qs}")
    if j.get("code") not in (0, "0"): raise RuntimeError(f"{path} {q} → {j.get('code')} {j.get('msg')}")
    return j["data"]


def btc_below(monday_ms):
    try:
        rows = get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1w&limit=30")
        closes = [float(r[4]) for r in rows if int(r[0]) < monday_ms]
        src = "幣安現貨"
    except Exception:
        rows = bx("/v3/quote/klines", symbol="BTC-USDT", interval="1w", limit=30)
        closes = [float(r["close"]) for r in sorted(rows, key=lambda r: r["time"]) if int(r["time"]) < monday_ms]
        src = "BingX（幣安連不上的備援）"
    last, ma = closes[-1], sum(closes[-20:]) / 20
    return last < ma, last, ma, src


def universe(today_ms):
    cs = bx("/v2/quote/contracts")
    syms = []
    for c in cs:
        s = c["symbol"]
        # NC 開頭＝BingX 的股票／商品／外匯合約（NCSK…、NCCO…），回測幣池沒有
        if not s.endswith("-USDT") or s.startswith("NC") or int(c.get("status", 1)) != 1: continue
        base = s[:-5]
        if base in STABLE or base in NONCOIN: continue
        syms.append(s)
    out = {}
    for s in syms:
        try:
            k = bx("/v3/quote/klines", symbol=s, interval="1d", limit=45)
        except Exception:
            continue
        k = sorted((r for r in k if int(r["time"]) < today_ms), key=lambda r: r["time"])   # 只要收完的日K
        if len(k) < LB + 2: continue
        t = [int(r["time"]) for r in k]
        if t[-1] != today_ms - D: continue                                           # 昨天要有K
        C = {ti: float(r["close"]) for ti, r in zip(t, k)}
        need = [today_ms - D * j for j in (1, 8, 15, 29)]
        if any(x not in C for x in need): continue
        if t[0] > today_ms - 30 * D: continue                                       # 上市滿 30 天
        last30 = [r for r in k if int(r["time"]) >= today_ms - 30 * D]
        qv30 = sum(float(r["volume"]) * float(r["close"]) for r in last30) / 30
        if qv30 < QV_MIN: continue
        dr = []
        for j in range(VOL_N, 0, -1):
            a, b = C.get(today_ms - D * (j + 1)), C.get(today_ms - D * j)
            dr.append(b / a - 1 if a and b else 0.0)
        out[s] = dict(mom=C[today_ms - D] / C[today_ms - D * 29] - 1, qv30=qv30, dr=dr, close=C[today_ms - D])
        time.sleep(0.05)
    return out


def depth(sym):
    d = bx("/v2/quote/depth", symbol=sym, limit=100)
    bids = [(float(p), float(q)) for p, q in d["bids"]]
    asks = [(float(p), float(q)) for p, q in d["asks"]]
    bids.sort(key=lambda x: -x[0]); asks.sort(key=lambda x: x[0])
    return bids, asks


def fill(levels, notional):
    """吃單均價；掛單簿 100 檔不夠吃 → 回傳 None。"""
    left, cost, qty = notional, 0.0, 0.0
    for p, q in levels:
        v = p * q
        take = min(v, left)
        cost += take; qty += take / p; left -= take
        if left <= 1e-9: return cost / qty
    return None


def slip_row(sym, sizes):
    bids, asks = depth(sym)
    mid = (bids[0][0] + asks[0][0]) / 2
    r = dict(mid=mid, spread=(asks[0][0] - bids[0][0]) / mid)
    for n in sizes:
        b, s = fill(asks, n), fill(bids, n)
        r[f"buy{n}"] = None if b is None else b / mid - 1
        r[f"sell{n}"] = None if s is None else 1 - s / mid
    return r


def funding(sym, t0, t1):
    try:
        rows = bx("/v2/quote/fundingRate", symbol=sym, startTime=t0, endTime=t1, limit=100)
        return sum(float(r["fundingRate"]) for r in rows if t0 < int(r["fundingTime"]) <= t1)
    except Exception:
        return 0.0003 * 21                                                           # 抓不到：假設每 8 小時 0.01%


def pct(x, d=2):
    return "—" if x is None else f"{x*100:+.{d}f}%"


def main():
    dry = "--dry" in sys.argv
    now = int(time.time() * 1000)
    today = now // D * D
    tw = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    st_path = os.path.join(HERE, "state.json")
    st = json.load(open(st_path, encoding="utf-8")) if os.path.exists(st_path) else dict(equity=START_EQ, pos={}, t=None, weeks=[])

    below, bc, bma, bsrc = btc_below(today - ((today // D + 3) % 7) * D)
    U = universe(today)
    syms = sorted(U)
    N = len(syms)
    n = max(3, int(N * 0.1))
    order = sorted(syms, key=lambda s: -U[s]["mom"])
    top, bot = order[:n], order[::-1][:n]
    w = {}
    if not below:
        for s in top: w[s] = 0.5 / n
        for s in bot: w[s] = w.get(s, 0) - 0.5 / n
        port = [sum(w[s] * U[s]["dr"][i] for s in w) for i in range(VOL_N)]
        m = sum(port) / VOL_N
        v = math.sqrt(sum((x - m) ** 2 for x in port) / (VOL_N - 1)) * math.sqrt(365)
        sc = min(1.0, VOL_TGT / v) if v > 0 else 1.0
        w = {s: x * sc for s, x in w.items()}
    else:
        v, sc = float("nan"), 0.0

    # ---- 結算上週（用現在的掛單簿出場價＋這週實際資金費率）----
    old = st["pos"]; eq0 = st["equity"]; settle = None
    need = sorted(set(old) | set(w))
    book = {}
    for s in need:
        try: book[s] = depth(s)
        except Exception: book[s] = None
        time.sleep(0.05)
    if old and st["t"]:
        pnl = 0.0; fund_total = 0.0; rows = []
        for s, p in old.items():
            b = book.get(s)
            if not b: rows.append((s, p["w"], None)); continue
            mid = (b[0][0][0] + b[1][0][0]) / 2
            r = mid / p["px"] - 1
            f = funding(s, st["t"], now); fund_total += -p["w"] * f * eq0
            pnl += p["w"] * r * eq0 - p["w"] * f * eq0
            rows.append((s, p["w"], r))
        settle = dict(pnl=pnl, fund=fund_total, rows=rows)
    eq_mid = eq0 + (settle["pnl"] if settle else 0.0)

    # ---- 換倉：只交易權重變動那部分，用掛單簿實際吃單價算滑價 ----
    trade_cost = 0.0; trades = []; missing = []
    cur = {s: (old[s]["w"] if s in old else 0.0) for s in need}
    for s in need:
        dw = w.get(s, 0.0) - cur.get(s, 0.0)
        if abs(dw) < 1e-9: continue
        notion = abs(dw) * eq_mid
        b = book.get(s)
        if not b: missing.append(s); continue
        bids, asks = b
        mid = (bids[0][0] + asks[0][0]) / 2
        px = fill(asks if dw > 0 else bids, notion)
        if px is None: missing.append(s); px = (asks if dw > 0 else bids)[-1][0]
        sl = (px / mid - 1) if dw > 0 else (1 - px / mid)
        trade_cost += notion * (sl + FEE)
        trades.append(dict(s=s, side="買" if dw > 0 else "賣", notion=notion, slip=sl))
    eq1 = eq_mid - trade_cost
    newpos = {}
    for s, x in w.items():
        b = book.get(s)
        if b: newpos[s] = dict(w=x, px=(b[0][0][0] + b[1][0][0]) / 2)

    # ---- 固定金額的滑價量測（這週要做的每一檔）----
    meas = {}
    for s in w:
        try: meas[s] = slip_row(s, SIZES)
        except Exception: meas[s] = None
        time.sleep(0.05)

    def avg(key, side):
        xs = [meas[s][key] for s in meas if meas[s] and meas[s][key] is not None and (w[s] > 0) == (side == "L")]
        return sum(xs) / len(xs) if xs else None

    # ---- 報告 ----
    L = [f"# G6 紙上跟單＋滑價實測　{tw(now)}（台北）", ""]
    L.append(f"- BTC 濾網：上週收 {bc:,.0f}、20 週均線 {bma:,.0f}（{bsrc}）→ **{'在均線下，這週空手' if below else '在均線上，照常持倉'}**")
    L.append(f"- 幣池 {N} 檔、每邊 {n} 檔；組合波動 {('—' if below else f'{v*100:.0f}%')}，曝險倍數 {sc:.2f}")
    if settle:
        L.append(f"- 上週結算：損益 {settle['pnl']:+.1f} 元（含資金費率 {settle['fund']:+.1f} 元）")
    L.append(f"- 換倉成本（實際吃單滑價＋手續費 0.05%／邊）：{trade_cost:.2f} 元；帳本 {eq0:,.1f} → **{eq1:,.1f} 元**（起始 {START_EQ:,.0f}）")
    if missing: L.append(f"- ⚠️ 掛單簿抓不到或吃不完：{', '.join(missing)}")
    L += ["", "## 這週每邊滑價（相對中間價，平均）", "", "| 下單金額 | 多單買進 | 空單賣出 | 空單回補（買） |", "|---|---|---|---|"]
    for z in SIZES:
        L.append(f"| {z:,} 元 | {pct(avg(f'buy{z}', 'L'), 3)} | {pct(avg(f'sell{z}', 'S'), 3)} | {pct(avg(f'buy{z}', 'S'), 3)} |")
    L.append("")
    L.append("回測的生死線：空單每邊滑價約 1.1～1.3% 就打平。")
    L += ["", "## 持倉與單檔滑價（1,000 元）", "", "| 幣 | 方向 | 權重 | 28 天漲跌 | 價差 | 買 1,000 | 賣 1,000 |", "|---|---|---|---|---|---|---|"]
    for s in sorted(w, key=lambda s: -w[s]):
        m_ = meas.get(s) or {}
        L.append(f"| {s} | {'多' if w[s] > 0 else '空'} | {w[s]*100:+.1f}% | {pct(U[s]['mom'], 1)} | {pct(m_.get('spread'), 3)} | {pct(m_.get('buy1000'), 3)} | {pct(m_.get('sell1000'), 3)} |")
    rep = "\n".join(L) + "\n"
    print(rep)
    if dry:
        open(os.path.join(HERE, "dry_run.md"), "w", encoding="utf-8").write(rep)
        return
    wk = dt.datetime.utcfromtimestamp(today / 1000).strftime("%Y-%m-%d")
    os.makedirs(os.path.join(HERE, "weeks"), exist_ok=True)
    open(os.path.join(HERE, "weeks", f"{wk}.md"), "w", encoding="utf-8").write(rep)
    open(os.path.join(HERE, "latest.md"), "w", encoding="utf-8").write(rep)
    st["weeks"].append(dict(week=wk, eq0=eq0, eq1=eq1, settle=(settle["pnl"] if settle else None), cost=trade_cost, below=below, N=N, n=n, scale=sc,
                            slip={f"{side}{z}": avg(f"{k}{z}", side2) for z in SIZES for side, k, side2 in (("Lbuy", "buy", "L"), ("Ssell", "sell", "S"), ("Sbuy", "buy", "S"))}))
    st.update(equity=eq1, pos=newpos, t=now)
    json.dump(st, open(st_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
