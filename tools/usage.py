# -*- coding: utf-8 -*-
"""真实 token 账本：直接读 DSH 会话日志里每条 assistant/message 的 usage。

为什么不用代理记账：DSH GUI 的请求走它自己的 DeepSeek 通道，不经过
D:\sleep\llm\proxy（8090），所以 proxy/usage.jsonl 只覆盖零星脚本调用，
「缓存命中率 0%」是覆盖范围问题，不是解析 bug。会话日志才是全量真源：
    {"inputTokens": 2482, "outputTokens": 493,
     "cacheReadTokens": 76800, "cacheWriteTokens": 0, "totalTokens": 79775}
totalTokens = input + cacheRead + cacheWrite + output。

计价（人民币/百万 token，deepseek-flash，2026-10-23 官方价目表核对）：
               命中    未命中   输出
    空闲时段   0.02    1.0      4.0
    高峰时段   0.04    2.0      8.0   （空闲 = 高峰的一半）
时段定义（北京时间）：周一至周五 9:00-12:00、14:00-18:00 为高峰；其余（12:00-14:00、
18:00 到次日 9:00、周末、法定节假日）都是空闲。节假日表不在本脚本里，按高峰算（上限）。
逐条记录按自身时间戳判峰谷，不再用一套统一单价。

CLI:
    py usage.py                 # 最近 1 天，按天汇总
    py usage.py --days 3        # 最近 3 天
    py usage.py --json          # 纯 JSON
    py usage.py --sessions      # 额外列出各会话用量
    py usage.py --path <zstd>   # 只算一个会话文件
退出码：0 = 有数据；3 = 没数据
"""
import io
import json
import os
import re
import sys
import time

SESS_ROOT = r"D:\dsh-home\sessions"
FILE_GLOB = "session*.zstd"
RATES_IDLE = (0.02, 1.0, 4.0)    # 空闲时段：命中 / 未命中 / 输出（元每百万 token）
RATES_PEAK = (0.04, 2.0, 8.0)    # 高峰时段
RATES = RATES_IDLE               # 兼容旧调用


def is_peak(ts):
    """按北京时间判是否高峰时段。ts 为秒级时间戳；周末一律空闲；节假日按高峰（保守）。"""
    t = time.localtime(ts)
    if t.tm_wday >= 5:
        return False
    return (9 <= t.tm_hour < 12) or (14 <= t.tm_hour < 18)
MAX_FILE_BYTES = 300 * 1024 * 1024


def session_files(days=1.0, only=None):
    """返回 [(path, mtime)]，按 mtime 倒序，过滤掉太老的。"""
    if only:
        p = os.path.abspath(only)
        return [(p, os.path.getmtime(p))] if os.path.isfile(p) else []
    cutoff = time.time() - days * 86400.0
    out = []
    for root, _dirs, files in os.walk(SESS_ROOT):
        for name in files:
            if not name.endswith(".zstd"):
                continue
            p = os.path.join(root, name)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m >= cutoff:
                out.append((p, m))
    out.sort(key=lambda x: -x[1])
    return out


def _iter_lines(path):
    """流式解压会话文件，逐行产出（不把整份历史读进内存）。"""
    try:
        import zstandard
    except Exception as e:
        raise SystemExit("缺 zstandard：%s" % e)
    with open(path, "rb") as fh:
        rdr = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
        for line in io.TextIOWrapper(rdr, encoding="utf-8", errors="replace"):
            yield line


def scan(path):
    """返回 [{ts, hit, miss, write, out, total}]，只取带 usage 的 assistant/message。"""
    rows = []
    if os.path.getsize(path) > MAX_FILE_BYTES:
        return rows
    try:
        for line in _iter_lines(path):
            if '"usage"' not in line or "assistant/message" not in line:
                continue
            try:
                j = json.loads(line)
            except ValueError:
                continue
            if j.get("type") != "assistant/message":
                continue
            d = j.get("data") or {}
            u = d.get("usage")
            if not isinstance(u, dict) or not u.get("totalTokens"):
                continue
            rows.append({
                "ts": int(j.get("time") or 0) / 1000.0,
                "hit": int(u.get("cacheReadTokens") or 0),
                "miss": int(u.get("inputTokens") or 0),
                "write": int(u.get("cacheWriteTokens") or 0),
                "out": int(u.get("outputTokens") or 0),
                "total": int(u.get("totalTokens") or 0),
            })
    except Exception:
        return rows
    return rows


def cost(hit, miss, out, peak=False, rates=None):
    r = rates or (RATES_PEAK if peak else RATES_IDLE)
    return (hit * r[0] + miss * r[1] + out * r[2]) / 1000000.0


def _blank():
    return {"n": 0, "hit": 0, "miss": 0, "write": 0, "out": 0, "total": 0, "cost": 0.0}


def _add(b, r):
    for k in ("hit", "miss", "write", "out", "total"):
        b[k] += r[k]
    b["cost"] += cost(r["hit"], r["miss"], r["out"], peak=is_peak(r["ts"]))
    b["n"] += 1


def summarize(files, days=1.0):
    cutoff = time.time() - days * 86400.0
    by_day, by_sess = {}, {}
    for path, _m in files:
        rows = scan(path)
        if not rows:
            continue
        sid = sid_of(path)
        for r in rows:
            if r["ts"] < cutoff:
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
            _add(by_day.setdefault(day, _blank()), r)
            _add(by_sess.setdefault(sid, _blank()), r)
    data = {
        "days": days,
        "rates": {"idle": {"hit": RATES_IDLE[0], "miss": RATES_IDLE[1], "out": RATES_IDLE[2]},
                  "peak": {"hit": RATES_PEAK[0], "miss": RATES_PEAK[1], "out": RATES_PEAK[2]}},
        "byDay": {},
        "bySession": {},
    }
    for day, b in by_day.items():
        b = dict(b)
        b["hitRate"] = round(b["hit"] / float(b["hit"] + b["miss"]), 4) if (b["hit"] + b["miss"]) else None
        b["costCny"] = round(b["cost"], 2)
        data["byDay"][day] = b
    for sid, b in by_sess.items():
        b = dict(b)
        b["hitRate"] = round(b["hit"] / float(b["hit"] + b["miss"]), 4) if (b["hit"] + b["miss"]) else None
        b["costCny"] = round(b["cost"], 2)
        data["bySession"][sid] = b
    return data


def sid_of(path):
    d = os.path.basename(os.path.dirname(path)) or path
    m = re.search(r"(session-[0-9a-fA-F-]{8,})", d)
    return m.group(1) if m else d


def print_report(data, show_sessions=False):
    days = data["days"]
    day_keys = sorted(data["byDay"])
    if not day_keys:
        print("最近 %g 天没有会话用量记录。" % days)
        return 3
    tot = _blank()
    print("真实 token 账本（最近 %g 天，来源：DSH 会话日志的 usage）" % days)
    print("")
    print("%-12s %6s %14s %14s %12s %8s %10s" % ("日期", "请求", "缓存命中tok", "未命中tok", "输出tok", "命中率", "估算¥"))
    for day in day_keys:
        b = data["byDay"][day]
        for k in tot:
            tot[k] += b[k]
        hr = "%.1f%%" % (b["hitRate"] * 100) if b["hitRate"] is not None else "-"
        print("%-12s %6d %14d %14d %12d %8s %10.2f" % (
            day, b["n"], b["hit"], b["miss"], b["out"], hr, b["costCny"]))
    allhit = tot["hit"] + tot["miss"]
    print("-" * 80)
    print("%-12s %6d %14d %14d %12d %8s %10.2f" % (
        "合计", tot["n"], tot["hit"], tot["miss"], tot["out"],
        ("%.1f%%" % (tot["hit"] / float(allhit) * 100)) if allhit else "-",
        tot["cost"]))
    if tot["n"]:
        print("")
        print("平均每次请求 输入 %.0f tok（命中 %.0f + 未命中 %.0f）/ 输出 %.0f tok" % (
            tot["total"] / float(tot["n"]), tot["hit"] / float(tot["n"]),
            tot["miss"] / float(tot["n"]), tot["out"] / float(tot["n"])))
    if show_sessions:
        sess = sorted(data["bySession"].items(), key=lambda kv: -kv[1]["total"])
        print("")
        print("按会话（前 10）：")
        print("%-40s %6s %14s %12s %8s %8s" % ("会话", "请求", "总tok", "输出tok", "命中率", "估算¥"))
        for sid, b in sess[:10]:
            hr = "%.1f%%" % (b["hitRate"] * 100) if b["hitRate"] is not None else "-"
            print("%-40s %6d %14d %12d %8s %8.2f" % (
                sid[:40], b["n"], b["total"], b["out"], hr, b["costCny"]))
    print("")
    print("注：只统计带 usage 的模型回复；DSH 原生压缩/本机模型请求不计。")
    print("代理 usage.jsonl 只覆盖手工走 8090 的调用（GUI 不经过它），别拿它算总量。")
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    days = 1.0
    if "--days" in argv:
        i = argv.index("--days")
        if i + 1 < len(argv):
            days = float(argv[i + 1])
    path = None
    if "--path" in argv:
        i = argv.index("--path")
        if i + 1 < len(argv):
            path = argv[i + 1]
    files = session_files(days, path)
    if not files:
        print("最近 %g 天没有会话文件。" % days)
        return 3
    data = summarize(files, days)
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data["byDay"] else 3
    return print_report(data, show_sessions=("--sessions" in argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
