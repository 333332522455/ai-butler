# -*- coding: utf-8 -*-
"""性能体检：给"我跑得快不快/贵不贵"一个数字，而不是感觉。

数据全来自 DSH 会话日志（每条 assistant/message 带 data.usage，每个 step 有 start/end 时间戳）。
口径：
  模型时延  = assistant/message.time - step/start.time（真正等模型的时间）
  整步时延  = step/end.time - step/start.time（含工具执行）
  轮步数    = 每个 turn 的 step 数
用法：
  python tools/perf.py            最近 24 小时
  python tools/perf.py --days 3 --json
  python tools/perf.py --only 0a69775c
退出码：0 正常；2 有 warning 级指标（模型 p90 > 15s / 每轮步数中位 > 12 / 未命中占比 > 5%）；3 读不到会话。
"""
import glob
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import usage as U  # noqa: E402


def _ts(s):
    """DSH 的 time 是 epoch 毫秒。"""
    try:
        return float(s) / 1000.0
    except Exception:
        return None


def scan_perf(path):
    """返回该会话的时延/步数统计。"""
    model, step_time = [], []
    turns = {}
    slow = []
    cur = {}
    for line in U._iter_lines(path):
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        tm = _ts(o.get("time"))
        d = o.get("data") or {}
        key = (d.get("turn"), d.get("step"))
        if t == "step/start":
            cur[key] = tm
            turns[d.get("turn")] = turns.get(d.get("turn"), 0) + 1
        elif t == "assistant/message":
            s = cur.get(key)
            if s and tm and tm > s:
                model.append(tm - s)
        elif t == "step/end":
            s = cur.get(key)
            if s and tm and tm > s:
                dt = tm - s
                step_time.append(dt)
                slow.append((dt, key))
    slow.sort(reverse=True)
    return {"model": model, "step": step_time, "turns": turns, "slow": slow[:5]}


def _pct(v, q):
    if not v:
        return None
    v = sorted(v)
    i = min(len(v) - 1, max(0, int(len(v) * q)))
    return round(v[i], 2)


def collect(days=1.0, only=None):
    files = U.session_files(days=days, only=only)
    if not files:
        return {"ok": False, "reason": "no-session-files"}
    model, step_time, turns, slow = [], [], {}, []
    usage = U.summarize(files, days=days)
    agg = U._blank()
    for b in usage.get("byDay", {}).values():
        for _k in ("n", "hit", "miss", "write", "out", "total"):
            agg[_k] += b.get(_k, 0)
    usage = {"requests": agg["n"], "cache_hit": agg["hit"], "miss": agg["miss"],
             "out": agg["out"], "cost": U.cost(agg["hit"], agg["miss"], agg["out"])}
    for f, _m in files:
        r = scan_perf(f)
        model += r["model"]
        step_time += r["step"]
        for k, v in r["turns"].items():
            turns[k] = turns.get(k, 0) + v
        slow += [(d, k, os.path.basename(os.path.dirname(f))) for d, k in r["slow"]]
    slow.sort(reverse=True)
    per_turn = [v for v in turns.values() if v]
    hit = usage.get("cache_hit", 0)
    miss = usage.get("miss", 0)
    total_in = hit + miss
    out = {
        "ok": True,
        "window_days": days,
        "sessions": len(files),
        "requests": usage.get("requests", 0),
        "input_tokens": total_in,
        "avg_input": int(total_in / usage["requests"]) if usage.get("requests") else 0,
        "cache_hit_ratio": round(hit / total_in, 4) if total_in else None,
        "cost_cny": round(usage.get("cost", 0.0), 2),
        "model_p50": _pct(model, 0.5),
        "model_p90": _pct(model, 0.9),
        "step_p50": _pct(step_time, 0.5),
        "step_p90": _pct(step_time, 0.9),
        "steps": len(step_time),
        "turns": len(per_turn),
        "steps_per_turn_median": statistics.median(per_turn) if per_turn else None,
        "slowest": [{"sec": round(d, 1), "turn": k[0], "step": k[1]} for d, k, _ in slow[:5]],
    }
    warns = []
    if out["model_p90"] and out["model_p90"] > 15:
        warns.append("模型 p90 %.1fs > 15s" % out["model_p90"])
    if out["steps_per_turn_median"] and out["steps_per_turn_median"] > 12:
        warns.append("每轮步数中位 %s > 12（每次工具调用都是一次完整请求）" % out["steps_per_turn_median"])
    if out["cache_hit_ratio"] is not None and out["cache_hit_ratio"] < 0.95:
        warns.append("缓存命中率 %.1f%% < 95%%（前缀在变，钱会变多）" % (out["cache_hit_ratio"] * 100))
    out["warnings"] = warns
    return out


def print_report(d, as_json=False):
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    if not d.get("ok"):
        print("读不到会话日志：%s" % d.get("reason"))
        return
    print("性能体检（最近 %s 天 / %d 个会话）" % (d["window_days"], d["sessions"]))
    print("  请求 %d 次 · 平均输入 %d tok · 缓存命中 %.1f%% · 估算 %.2f 元"
          % (d["requests"], d["avg_input"], (d["cache_hit_ratio"] or 0) * 100, d["cost_cny"]))
    print("  模型首回复 p50 %ss / p90 %ss" % (d["model_p50"], d["model_p90"]))
    print("  整步（含工具）p50 %ss / p90 %ss" % (d["step_p50"], d["step_p90"]))
    print("  %d 步 / %d 轮 · 每轮步数中位 %s" % (d["steps"], d["turns"], d["steps_per_turn_median"]))
    if d["slowest"]:
        print("  最慢几步：" + "、".join("%.1fs(turn%s/step%s)" % (s["sec"], s["turn"], s["step"]) for s in d["slowest"]))
    for w in d.get("warnings", []):
        print("  !! " + w)


def main(argv):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    days = 1.0
    as_json = False
    only = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--days":
            i += 1
            days = float(argv[i])
        elif a == "--json":
            as_json = True
        elif a == "--only":
            i += 1
            only = argv[i]
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        i += 1
    d = collect(days=days, only=only)
    print_report(d, as_json=as_json)
    if not d.get("ok"):
        return 3
    return 2 if d.get("warnings") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
