# -*- coding: utf-8 -*-
"""toollint —— 工具输出预算审计（只读，提示性）。

扫最近 N 天的会话文件，挑出「结果很大、但调用里没有任何裁剪」的工具调用，
按浪费字符排序。用途：把「工具输出占上下文大头」这件事变成可核对的数据。

用法：
  python tools/toollint.py                    # 最近 1 天，top 8
  python tools/toollint.py --days 3 --top 15
  python tools/toollint.py --session latest    # 只看最新会话
  python tools/toollint.py --json
  python tools/toollint.py --selftest
退出码：0 正常（审计不阻断）；3 自检不过；1 环境/参数问题。
"""
import sys, os, json, re, argparse, collections

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

BIG = 4000            # 结果超过这个字符数就算「大结果」
CHARS_PER_TOK = 3.0   # 粗估：3 字符 ≈ 1 token
TRIM = re.compile(
    r"\btail\b|\bhead\b|sed\s+-n|awk\s+'NR|--max-chars|\.slice\(|Select-Object|"
    r"--top\b|--json\b|Limit-Object|limit\s*=|maxLength|\|\s*head|\|\s*tail", re.I)


def is_trimmed(cmd):
    """调用本身有没有带裁剪动作（tail/head/sed 区间/slice/limit...）。"""
    return bool(TRIM.search(cmd or ""))


def classify(text):
    """按结果形态归类：整份文件带行号 / 整段日志 / 整份 JSON / 巨型 / 其它。"""
    head = text[:4000]
    numbered = len(re.findall(r"(?m)^\s*\d+[|\t:]", head))
    ts = len(re.findall(r"(?m)^\d{4}-\d\d-\d\dT", head))
    if numbered >= 12:
        return "整份文件带行号"
    if ts >= 15:
        return "整段日志"
    if text.lstrip()[:1] in ("{", "["):
        return "整份 JSON/对象"
    if len(text) >= 20000:
        return "巨型输出"
    return "其它大输出"


def collect(path):
    """读一个会话文件，返回 [(chars, tool, cmd, kind)]，只保留大结果。"""
    import usage as U
    out, pend = [], {}
    for line in U._iter_lines(path):
        if '"tool/' not in line:
            continue
        try:
            j = json.loads(line)
        except ValueError:
            continue
        t, d = j.get("type"), (j.get("data") or {})
        if t == "tool/call":
            args = d.get("arguments") or ""
            try:
                a = json.loads(args) if args.strip().startswith("{") else {}
            except ValueError:
                a = {}
            key = a.get("command") or a.get("code") or a.get("file_path") or a.get("pattern") or ""
            pend[d.get("callId")] = (d.get("name") or "?", str(key))
        elif t == "tool/result":
            m = d.get("message") or {}
            txt = "".join((c.get("text") or "") for c in (m.get("content") or []) if isinstance(c, dict))
            n = len(txt)
            name, cmd = pend.pop(m.get("toolCallId"), ("?", ""))
            if n >= BIG:
                out.append((n, name, cmd, classify(txt)))
    return out


def scan(days=1.0, only=None):
    """扫最近的会话，返回 [{sid, total, rows}]。"""
    import usage as U
    files = U.session_files(days, only=only)
    res = []
    for f, _m in files:
        sid = os.path.basename(os.path.dirname(f))[:20]
        try:
            rows = collect(f)
        except Exception as exc:                                    # 坏文件不拖垮整次审计
            rows = [(-1, "ERR", str(exc), "读不了")]
        res.append({"sid": sid, "path": f, "rows": rows})
    return res


def summarize(rows):
    """把行整理成统计：总字符、无裁剪大结果、按形态汇总。只算 >= BIG 的行。"""
    rows = [r for r in rows if r[0] >= BIG]
    tot = sum(r[0] for r in rows)
    waste = [r for r in rows if r[0] > 0 and not is_trimmed(r[2])]
    by_kind = collections.Counter()
    for n, _t, _c, k in waste:
        by_kind[k] += n
    return {
        "big_rows": len(rows),
        "chars": tot,
        "waste_rows": len(waste),
        "waste_chars": sum(r[0] for r in waste),
        "waste_pct": (100.0 * sum(r[0] for r in waste) / tot) if tot else 0.0,
        "by_kind": by_kind,
        "top": sorted(waste, key=lambda r: -r[0]),
    }


def fmt_cmd(cmd, n=110):
    s = re.sub(r"\s+", " ", (cmd or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def report(days, top, only_latest, as_json):
    import usage as U
    only = None
    if only_latest:
        files = U.session_files(max(days, 0.05))
        if not files:
            print("没有找到会话文件")
            return 1
        only = files[0][0]
    data = scan(days, only)
    if not data:
        print("最近 %.1f 天没有会话文件" % days)
        return 0
    total_waste = total_chars = 0
    out = []
    for s in data:
        st = summarize(s["rows"])
        total_waste += st["waste_chars"]
        total_chars += st["chars"]
        out.append({"sid": s["sid"], **{k: v for k, v in st.items() if k not in ("by_kind", "top")},
                    "by_kind": dict(st["by_kind"]), "top": [
                        {"chars": r[0], "tool": r[1], "kind": r[3], "cmd": fmt_cmd(r[2], 200)}
                        for r in st["top"][:top]]})
    if as_json:
        print(json.dumps({"days": days, "sessions": out,
                          "total_waste_chars": total_waste,
                          "total_chars": total_chars}, ensure_ascii=False, indent=1))
        return 0
    print("工具输出审计：最近 %.1f 天，%d 个会话" % (days, len(data)))
    print("总计 %d 字符（≈%dK tok）| 无裁剪大结果 %d 字符 = %.1f%%" % (
        total_chars, total_chars / CHARS_PER_TOK / 1000, total_waste,
        100.0 * total_waste / total_chars if total_chars else 0.0))
    for s in sorted(out, key=lambda x: -x["waste_chars"])[:6]:
        print("\n%s  结果 %d 字符 | 无裁剪大结果 %d 条 / %d 字符 (%.1f%%)" % (
            s["sid"], s["chars"], s["waste_rows"], s["waste_chars"], s["waste_pct"]))
        if s["by_kind"]:
            print("   形态：" + "，".join("%s %d" % (k, v) for k, v in
                                     sorted(s["by_kind"].items(), key=lambda kv: -kv[1])))
        for i, r in enumerate(s["top"], 1):
            print("   %d) %6d 字符  %-11s %s" % (i, r["chars"], r["kind"], r["cmd"]))
    print("\n用法提醒：调用里带 tail/head/sed -n/Select-Object -Last/--json/slice 就算裁剪；"
          "单条 >%d 字符且没裁剪的，下次改写法。" % BIG)
    return 0


def selftest():
    fails = []

    def chk(name, cond, got=None):
        if not cond:
            fails.append("%s -> %s" % (name, got))

    chk("tail 算裁剪", is_trimmed("git log | tail -20"))
    chk("Select-Object 算裁剪", is_trimmed("Get-ChildItem | Select-Object -Last 5"))
    chk("sed -n 算裁剪", is_trimmed("sed -n '1,40p' x.log"))
    chk("slice 算裁剪", is_trimmed("console.log(r.stdout.text.slice(0, 500))"))
    chk("裸命令不算裁剪", not is_trimmed("cat big.log"))
    chk("grep 不带区间不算裁剪", not is_trimmed("grep -h foo big.log"))
    chk("行号 -> 整份文件", classify("\n".join("%d| x" % i for i in range(20))) == "整份文件带行号")
    chk("时间戳 -> 日志", classify("\n".join("2026-10-23T00:00:%02dZ x" % i for i in range(20))) == "整段日志")
    chk("花括号 -> JSON", classify('{"a": 1}') == "整份 JSON/对象")
    chk("普通长文本 -> 其它", classify("x" * 5000) == "其它大输出")
    st = summarize([(9000, "run_code", "cat x", "其它大输出"),
                    (8000, "run_code", "cat y | tail -5", "其它大输出"),
                    (100, "read", "cat z", "其它大输出")])
    chk("统计：小结果不计入", st["big_rows"] == 2, st["big_rows"])
    chk("统计：字符合计", st["chars"] == 17000, st["chars"])
    chk("统计：浪费只算无裁剪大结果", st["waste_chars"] == 9000, st["waste_chars"])
    chk("统计：占比", abs(st["waste_pct"] - 52.9411) < 0.01, st["waste_pct"])
    chk("统计：top 首条是最大浪费", st["top"][0][0] == 9000, st["top"][:1])
    if fails:
        print("SELFTEST FAIL %d" % len(fails))
        for f in fails:
            print("  - " + f)
        return 3
    print("SELFTEST PASS 14/14")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--session", default="", choices=["", "latest"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    try:
        import zstandard  # noqa: F401
    except Exception as exc:
        print("缺 zstandard：%s" % exc)
        return 1
    return report(a.days, a.top, a.session == "latest", a.json)


if __name__ == "__main__":
    sys.exit(main())
