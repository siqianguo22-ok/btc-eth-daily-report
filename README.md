# BTC/ETH 每日高低价 → 微信推送（GitHub Actions 云端版）

每天北京时间 **08:20** 自动抓取 BTC、ETH 在过去 24 小时（前一日 08:00 → 当日 08:00）的**最高价**和**最低价**，
通过 **Server酱 Turbo** 推送到微信，并把结果归档到 `data/`。

跑在 GitHub 的云端机器上，**你的电脑关机、休眠都不影响推送**。

---

## 目录结构

```
.
├─ .github/workflows/daily.yml   # 定时 00:20 UTC = 08:20 北京时间
├─ scripts/fetch_and_push.py     # 取数 + 计算 + 推送 + 归档（仅用 Python 标准库）
├─ data/                         # 每日归档，自动提交回仓库
└─ README.md
```

## 一次性配置（约 3 分钟）

### 1. 新建仓库

- 打开 https://github.com/new
- Repository name 填 `btc-eth-daily-report`
- 选 **Public**（完全免费）或 Private（免费额度 2000 分钟/月，本任务每月只用几十秒）
- **不要**勾选 "Add a README file"，直接 Create

### 2. 上传文件

仓库首页点 **Add file → Upload files**，把本文件夹里的内容拖进去（保持目录结构）：

- `.github/workflows/daily.yml`
- `scripts/fetch_and_push.py`
- `data/.gitkeep`
- `README.md`

> 网页上传支持拖整个文件夹（含 `.github` 这种点开头的目录）。

### 3. 添加 Secret（Server酱 SendKey）

- 仓库 → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `SERVERCHAN_SENDKEY`
- Secret: 你的 Server酱 SendKey（`SCT` 开头那串）

### 4. 手动跑一次验证

- 仓库 → **Actions** → 左侧「BTC/ETH 每日高低价」→ 右侧 **Run workflow** → Run
- 约 30 秒，微信收到推送；日志里能看到高低价、K 线根数、实际使用的数据源

## 日常运行

- 每天 **北京时间 08:20** 自动执行（UTC `20 0 * * *`）
- 结果写入 `data/price_YYYYMMDD.json` 并自动提交回仓库
- 失败时推送一条微信，附 Actions 运行链接

## 计算口径（重要）

- 窗口为 **[昨日 08:00, 今日 08:00)**，左闭右开，**不含** 08:00 整那根 K 线
- 正常每次应取到 **24 根** 1 小时 K 线（脚本会在日志里打印根数）
- 高低价取窗口内所有 K 线 high 的最大值 / low 的最小值
- 涨跌幅 = 窗口末根收盘 / 首根开盘 - 1

## 数据源与降级

按顺序尝试，日志会记录实际来源：

| 顺序 | 数据源 | 交易对 | 说明 |
|---|---|---|---|
| 1 | Gate.io | BTC_USDT / ETH_USDT | 与历史归档口径一致 |
| 2 | Coinbase | BTC-USD / ETH-USD | GitHub runner 在美国，Gate 若被地域限制则自动降级 |
| 3 | Kraken | XBTUSD / ETHUSD | 最后兜底 |

不同交易所的 USD/USDT 报价会有千分之几的差异，归档里会记录实际数据源。

## 常见问题

**Q: 会收费吗？**
公开仓库免费；私有仓库走 GitHub Free 每月 2000 分钟免费额度，本任务每次几十秒，实际不花钱。

**Q: 推送时间不准？**
GitHub 定时任务"尽力而为"，高峰期可能延迟几分钟到几十分钟。如需更高准点率可把 cron 提前到 `10 0 * * *`。

**Q: 想改时间/币种？**
改 `.github/workflows/daily.yml` 的 cron（UTC），或脚本里的 `PAIRS`。

## 本地自测

```bash
python scripts/fetch_and_push.py --dry-run
```
