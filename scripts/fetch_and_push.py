#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取 BTC / ETH 在「昨日 08:00 → 今日 08:00（北京时间）」窗口内的最高价、最低价，
用 Server酱 推送到微信，并把结果归档成 data/price_YYYYMMDD.json。

只依赖 Python 标准库，GitHub Actions 里无需 pip install。

数据源按顺序尝试（云端 runner 在美国，Gate.io 可能被地域限制）：
  1. Gate.io  现货 USDT 对（与历史归档口径一致）
  2. Coinbase 现货 USD 对
  3. Kraken   现货 USD 对

用法:
    python scripts/fetch_and_push.py                # 取数 + 推送 + 归档
    python scripts/fetch_and_push.py --dry-run      # 只取数并打印，不推送、不归档

环境变量:
    SERVERCHAN_SENDKEY   Server酱 Turbo 的 SendKey（--dry-run 时可省）
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# symbol -> (Gate.io, Coinbase, Kraken)
PAIRS = {
    "BTC": ("BTC_USDT", "BTC-USD", "XBTUSD"),
    "ETH": ("ETH_USDT", "ETH-USD", "ETHUSD"),
}


def http_json(url: str, tries: int = 3) -> object:
    """带重试的 GET，返回解析后的 JSON。"""
    last_err = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (btc-eth-daily-report)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 重试所有网络/解析错误
            last_err = exc
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def fmt_time(ts: int) -> str:
    """UTC 秒级时间戳 -> 北京时间 'MM-DD HH:MM'。"""
    return datetime.fromtimestamp(ts, tz=BJ).strftime("%m-%d %H:%M")


def _bars_from_gateio(symbol: str, start_ts: int, end_ts: int) -> list:
    pair = PAIRS[symbol][0]
    # Gate.io 的 from/to 是闭区间；窗口为 [start, end)，to 传 end-1。
    url = (
        f"https://api.gateio.ws/api/v4/spot/candlesticks"
        f"?currency_pair={pair}&interval=1h&from={start_ts}&to={end_ts - 1}"
    )
    raw = http_json(url)
    return [
        {
            "ts": int(k[0]),
            "open": float(k[2]),
            "high": float(k[3]),
            "low": float(k[4]),
            "close": float(k[5]),
        }
        for k in raw
    ]


def _bars_from_coinbase(symbol: str, start_ts: int, end_ts: int) -> list:
    pair = PAIRS[symbol][1]
    start_iso = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    end_iso = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    url = (
        f"https://api.exchange.coinbase.com/products/{pair}/candles"
        f"?granularity=3600&start={start_iso}&end={end_iso}"
    )
    raw = http_json(url)
    # Coinbase 每行: [时间戳, 最低, 最高, 开, 收, 量]，且按时间倒序
    rows = sorted(raw, key=lambda k: int(k[0]))
    return [
        {
            "ts": int(k[0]),
            "low": float(k[1]),
            "high": float(k[2]),
            "open": float(k[3]),
            "close": float(k[4]),
        }
        for k in rows
    ]


def _bars_from_kraken(symbol: str, start_ts: int, end_ts: int) -> list:
    pair = PAIRS[symbol][2]
    url = (
        f"https://api.kraken.com/0/public/OHLC"
        f"?pair={pair}&interval=60&since={start_ts}"
    )
    raw = http_json(url)
    if raw.get("error"):
        raise RuntimeError(f"Kraken 错误: {raw['error']}")
    result = raw.get("result") or {}
    rows = next((v for k, v in result.items() if k != "last"), [])
    # Kraken 每行: [时间, 开, 高, 低, 收, 均价, 量, 笔数]
    return [
        {
            "ts": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        }
        for k in rows
    ]


PROVIDERS = [
    ("gateio", _bars_from_gateio),
    ("coinbase", _bars_from_coinbase),
    ("kraken", _bars_from_kraken),
]


def fetch_bars(symbol: str, start_ts: int, end_ts: int):
    """
    依次尝试各数据源，返回 (bars, source)。
    bars 已按 [start_ts, end_ts) 过滤，理论上应为 24 根 1h K 线。
    """
    errors = []
    for name, fn in PROVIDERS:
        try:
            bars = fn(symbol, start_ts, end_ts)
        except Exception as exc:  # noqa: BLE001 - 数据源故障时降级
            errors.append(f"{name}: {exc}")
            print(f"  [{symbol}] {name} 失败，尝试下一个: {exc}", file=sys.stderr)
            continue
        bars = [b for b in bars if start_ts <= b["ts"] < end_ts]
        if len(bars) >= 20:  # 容忍偶发缺失，但不能少于 20 根
            return bars, name
        errors.append(f"{name}: 只取到 {len(bars)} 根 K 线")
    raise RuntimeError(f"{symbol} 所有数据源均失败: " + " | ".join(errors))


def analyze(symbol: str, start_ts: int, end_ts: int):
    bars, source = fetch_bars(symbol, start_ts, end_ts)
    hi = max(bars, key=lambda b: b["high"])
    lo = min(bars, key=lambda b: b["low"])
    first_open = bars[0]["open"]
    last_close = bars[-1]["close"]
    return {
        "high": hi["high"],
        "high_time": fmt_time(hi["ts"]),
        "low": lo["low"],
        "low_time": fmt_time(lo["ts"]),
        "first_open": first_open,
        "last_close": last_close,
        "change_pct": round((last_close / first_open - 1) * 100, 2),
        "bars": len(bars),
        "source": source,
    }


def build_markdown(win_text: str, data: dict) -> str:
    lines = ["### BTC/ETH 高低价", f"窗口：{win_text}（北京时间）", ""]
    for symbol, d in data.items():
        lines += [
            f"**{symbol}/USDT**",
            f"- 最高 **${d['high']:,.1f}**（{d['high_time']}）",
            f"- 最低 **${d['low']:,.1f}**（{d['low_time']}）",
            f"- 开→收：{d['first_open']:,.1f} → {d['last_close']:,.1f}"
            f"（**{d['change_pct']:+.2f}%**）",
            "",
        ]
    sources = sorted({d["source"] for d in data.values()})
    lines.append(f"数据源：{' / '.join(sources)} 现货 1h K线")
    return "\n".join(lines)


def push_wechat(sendkey: str, title: str, markdown: str) -> dict:
    """Server酱 Turbo 推送。返回响应 JSON（code=0 且 data.errno=0 表示成功）。"""
    query = urllib.parse.urlencode({"title": title, "desp": markdown})
    url = f"https://sctapi.ftqq.com/{sendkey}.send?{query}"
    res = http_json(url)
    if not isinstance(res, dict):
        raise RuntimeError(f"Server酱 返回异常: {res!r}")
    code = res.get("code")
    errno = (res.get("data") or {}).get("errno")
    if code != 0 or errno not in (0, None):
        raise RuntimeError(f"Server酱 推送失败: {res}")
    return res


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

    win_text = (
        f"{start.astimezone(BJ).strftime('%Y-%m-%d %H:%M')} → "
        f"{end.astimezone(BJ).strftime('%Y-%m-%d %H:%M')}"
    )
    day_tag = end.astimezone(BJ).strftime("%Y-%m-%d")

    print(f"窗口: {win_text}")
    data = {}
    for symbol in PAIRS:
        data[symbol] = analyze(symbol, start_ts, end_ts)
        d = data[symbol]
        print(
            f"{symbol}: 高 {d['high']:,.1f} @ {d['high_time']} | "
            f"低 {d['low']:,.1f} @ {d['low_time']} | "
            f"{d['first_open']:,.1f} → {d['last_close']:,.1f} "
            f"({d['change_pct']:+.2f}%) | {d['bars']}根 | {d['source']}"
        )

    markdown = build_markdown(win_text, data)
    if dry_run:
        print("\n--- dry-run，不推送 ---")
        print(markdown)
    else:
        sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
        if not sendkey:
            print("ERROR: 未设置 SERVERCHAN_SENDKEY 环境变量", file=sys.stderr)
            return 1
        title = f"BTC/ETH 高低价 {day_tag[5:]}"
        res = push_wechat(sendkey, title, markdown)
        print(f"推送成功: pushid={(res.get('data') or {}).get('pushid')}")

    if dry_run:
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"price_{end.astimezone(BJ).strftime('%Y%m%d')}.json"
    payload = {
        "window": {
            "start": start.astimezone(BJ).isoformat(),
            "end": end.astimezone(BJ).isoformat(),
            "note": "北京时间，24 小时窗口 [start, end)；数据源优先级 gateio > coinbase > kraken",
        },
        "data": data,
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已归档: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
