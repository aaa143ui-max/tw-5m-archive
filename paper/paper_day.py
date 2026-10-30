"""自家打法紙上帳（2026-10-30 建，規則鎖死，不歸零、不改參數）。兩本帳並排，都從 10 萬元起算。

A 帳：BTC 週線開關。上一根收完的週K 收盤 > 最近 20 根週收平均 → 抱 BTC，否則空手。換手扣 0.1%。
B 帳：日線收盤確認。BingX 永續、群友清單（paper/watchlist_bingx.json），日線急速框：
  - 成立那根（碰 5.8）收盤 ≥ 5.8，且基準之後到成立那根全部收紅、前 20 天平均成交額 ≥ 100 萬元 → 當天開盤進場
  - 停損 3.8、不保本；1:20／50／100 出 40／30／30%；日收破 21 日均線全出
  - 每筆賠帳戶 0.5%、同時最多 7 筆、回撤 ≥20% 減半、≤10% 恢復；成本來回 0.3%＋每 8 小時 0.01%
  - 判定用日K：同一天碰停損和目標，先算停損（偏保守）
回測來源：KOL方法驗證/自家打法v0_回測_20261030/_報告_3H與確認抱大.md（1D 3.8 不保本 +0.232R，跟隨機打平，只是線索）。
用法：python paper_day.py [--dry]"""
import json, os, sys, time, zlib, urllib.request, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
BX = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
D = 86_400_000
LV, FR = (20., 50., 100.), (.4, .3, .3)
START = 100_000.0
FEE, F8 = .003, .0001
RISK, CAP = .005, 7
DRY = "--dry" in sys.argv
STATE = os.path.join(HERE, "state.json")


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == tries - 1: raise
            time.sleep(2 + i * 3)


def klines(sym, n=90):
    j = get(f"{BX}?symbol={sym}&interval=1d&limit={n}")
    if j.get("code") not in (0, "0"): raise RuntimeError(f"{sym} {j.get('msg')}")
    return sorted(({"t": int(r["time"]), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]),
                    "v": float(r["volume"])} for r in j["data"]), key=lambda x: x["t"])


def entry58(b): return b["l"] + 5.8 * (b["h"] - b["l"])


def mb_long(bars):
    """同 engine/jisu_core._mb_long"""
    slot = None; stars = []
    for j in range(1, len(bars)):
        bj, bp = bars[j], bars[j - 1]; estd = False
        if slot is not None and bj["h"] >= entry58(bars[slot]):
            stars.append((slot, j)); slot, estd = None, True
        if slot is not None and bj["c"] < bars[slot]["l"]: slot = None
        if not (bj["c"] > bj["o"]) and bj["c"] < min(bp["o"], bp["c"]): slot = None
        if bj["h"] > bp["h"] and bj["c"] > bj["o"] and not estd and bp["h"] > bp["l"]:
            if slot is None or entry58(bp) < entry58(bars[slot]): slot = j - 1
    return stars


def sma21(bars, i):
    return sum(x["c"] for x in bars[i - 20:i + 1]) / 21 if i >= 20 else None


def step_pos(p, bars, today):
    """把部位走過 last 之後、today 之前收完的日K。出場回 True"""
    u = p["e"] - p["s"]
    for i, x in enumerate(bars):
        if x["t"] <= p["last"] or x["t"] >= today: continue
        p["last"] = x["t"]
        if x["l"] <= p["s"]:
            p["px"] = min(p["s"], x["o"]); p["why"] = "停損"; p["end"] = x["t"] + D; return True
        while p["k"] < 3 and x["h"] >= p["e"] + LV[p["k"]] * u:
            p["acc"] += FR[p["k"]] * LV[p["k"]]; p["rem"] -= FR[p["k"]]; p["k"] += 1
        if p["k"] >= 3:
            p["px"] = p["e"]; p["why"] = "1:100 出完"; p["end"] = x["t"] + D; return True
        m = sma21(bars, i)
        if m is not None and x["c"] < m:
            p["px"] = x["c"]; p["why"] = "破 21MA"; p["end"] = x["t"] + D; return True
    return False


def close_R(p):
    u = p["e"] - p["s"]; R = p["acc"] + p["rem"] * (p["px"] - p["e"]) / u
    hrs = (p["end"] - p["t"]) / 3_600_000
    return R - (FEE + F8 * hrs / 8) * p["e"] / u


def main():
    today = int(time.time() * 1000) // D * D
    day = dt.datetime.fromtimestamp(today / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {
        "start": day, "last_day": None,
        "A": {"cash": START, "coin": 0.0, "peak": START, "mdd": 0.0},
        "B": {"eq": START, "peak": START, "mdd": 0.0, "brake": False, "open": [], "closed": []}}
    if st["last_day"] == day and not DRY:
        print("今天跑過了"); return
    # A 帳
    wk = get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1w&limit=30")
    monday = today - ((today // D + 3) % 7) * D
    wc = [float(r[4]) for r in wk if int(r[0]) < monday]
    want = wc[-1] > sum(wc[-20:]) / 20
    btc = klines("BTC-USDT", 5); px = btc[-1]["o"]; A = st["A"]
    if want and A["coin"] == 0: A["coin"] = A["cash"] * .999 / px; A["cash"] = 0.0
    elif not want and A["coin"] > 0: A["cash"] = A["coin"] * px * .999; A["coin"] = 0.0
    va = A["cash"] + A["coin"] * px; A["peak"] = max(A["peak"], va); A["mdd"] = max(A["mdd"], (A["peak"] - va) / A["peak"])
    # B 帳：先結舊單
    Bk = st["B"]; msgs = []
    for p in list(Bk["open"]):
        bars = klines(p["sym"])
        if step_pos(p, bars, today):
            r = close_R(p); pnl = p["amt"] * r; Bk["eq"] += pnl; Bk["open"].remove(p)
            Bk["closed"].append({"sym": p["sym"], "in": p["day"], "out": day, "R": round(r, 2), "why": p["why"]}); msgs.append(f'{p["sym"]} 出場 {r:+.2f}R（{p["why"]}）')
        time.sleep(.2)
    Bk["peak"] = max(Bk["peak"], Bk["eq"]); dd = (Bk["peak"] - Bk["eq"]) / Bk["peak"]; Bk["mdd"] = max(Bk["mdd"], dd)
    Bk["brake"] = True if dd >= .2 else (False if dd <= .1 else Bk["brake"])
    # 掃新訊號
    bases = json.load(open(os.path.join(HERE, "watchlist_bingx.json"), encoding="utf-8"))["bases"]
    held = {p["sym"] for p in Bk["open"]}; cand = []; ok = 0
    for b0 in bases:
        sym = f"{b0}-USDT"
        try:
            k = klines(sym)
        except Exception:
            continue
        ok += 1; time.sleep(.2)
        cur = k[-1] if k and k[-1]["t"] == today else None
        bars = [x for x in k if x["t"] < today]
        if cur is None or len(bars) < 30 or bars[-1]["t"] != today - D or sym in held: continue
        if sum(x["v"] * x["c"] for x in bars[-20:]) / 20 < 1e6: continue
        for base, est in mb_long(bars):
            if est != len(bars) - 1: continue
            bb = bars[base]; rng = bb["h"] - bb["l"]; e0, s = bb["l"] + 5.8 * rng, bb["l"] + 3.8 * rng
            if bars[est]["c"] < e0 or not all(bars[j]["c"] >= bars[j]["o"] for j in range(base + 1, est + 1)): continue
            e = cur["o"]; pct = (e - s) / e
            if .003 <= pct <= .3: cand.append((zlib.crc32(f"{sym}{today}".encode()), sym, e, s))
    for _, sym, e, s in sorted(cand):
        if len(Bk["open"]) >= CAP: break
        amt = Bk["eq"] * RISK * (.5 if Bk["brake"] else 1)
        Bk["open"].append({"sym": sym, "day": day, "t": today, "last": today - D, "e": e, "s": s, "amt": round(amt, 2), "k": 0, "acc": 0.0, "rem": 1.0})
        msgs.append(f"{sym} 進場 {e}｜停損 {s:.6g}｜賠上限 {amt:,.0f} 元")
    st["last_day"] = day
    n = len(Bk["closed"]); rs = [c["R"] for c in Bk["closed"]]
    md = [f"# 自家打法紙上帳 {day}\n", f"起算 {st['start']}，各 10 萬元。規則鎖死，不歸零。\n",
          "| 帳 | 現值 | 最大回撤 | 狀態 |", "|---|---|---|---|",
          f"| A BTC 週線開關 | {va:,.0f} 元 | {A['mdd']:.1%} | {'抱 BTC' if A['coin'] > 0 else '空手'} |",
          f"| B 日線收盤確認（已實現） | {Bk['eq']:,.0f} 元 | {Bk['mdd']:.1%} | 持倉 {len(Bk['open'])} 筆，已結 {n} 筆，平均 {sum(rs)/n if n else 0:+.2f}R |",
          f"\n掃描 {ok}／{len(bases)} 檔，今日候選 {len(cand)}。"]
    if msgs: md += ["\n## 今天的動作"] + [f"- {m}" for m in msgs]
    if Bk["open"]: md += ["\n## 持倉"] + [f'- {p["sym"]} {p["day"]} 進 {p["e"]}，停損 {p["s"]:.6g}' for p in Bk["open"]]
    txt = "\n".join(md) + "\n"; print(txt)
    if not DRY:
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        open(os.path.join(HERE, "latest.md"), "w", encoding="utf-8").write(txt)


if __name__ == "__main__":
    main()
