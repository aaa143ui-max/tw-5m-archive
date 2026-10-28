# 台股 5 分K 存檔

每個交易日收盤後由 GitHub Actions 自動抓 Yahoo 5 分K 存下來（Yahoo 只留 60 天，所以要自己存）。

- `data/5m/YYYY-MM-DD.csv.gz`：當天 5 分K（code, time 台北時間, open, high, low, close, volume）
- `data/daily/YYYY-MM-DD.csv`：同一批股票當天日K
- 宇宙：前一交易日成交量 ≥ 1,000 張、收盤 ≥ 10 元的上市櫃股（約 500 多檔）
- 價格是 Yahoo 原始價（未還原除權息）

用途：回測盤中進場的方法（例如馬丁「突破開盤價」），2026-10-28 起累積。
