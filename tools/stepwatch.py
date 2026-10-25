#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stepwatch.py —— 把「我在第几步」算出来，交给操控气泡显示（任务书 2.4）。

数据全部来自 DSH 会话日志（不用我每步上报，零 token 成本）：
    turn/start 开一轮；step/start 每步 +1；tool/ptc-dispatch-start 记最后调的工具；turn/end 收尾。
写 logs/agent-step.json：
    {"state":"progress"|"idle","n":3,"total":8,"tool":"read","text":"第 3/8 步 · read","at":...,"open":true}
total 的取法：本轮还没结束时用最近 3 轮已知步数的最大值做分母（猜的，宁可标小不标大）；
本轮步数一旦超过它就改用本轮值，保证「第 9/8 步」这种荒谬显示不出现。

CLI:
    py stepwatch.py                # 读一次、写一次，打印状态
    py stepwatch.py --json         # 只打印不写
    py stepwatch.py --loop [--interval 2]   # 常驻
    py stepwatch.py --path <zstd> [--out <json>]
退出码 0 正常 / 3 读不到会话 / 1 用法错。
"""
import json
import os
import re
import sys
import time

if getattr(sys.stdout, "encoding", None) and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("AI_BUTLER_HOME") or os.path.dirname(HERE)
OUT_PATH = os.path.join(BASE, "logs", "agent-step.json")
LOG_PATH = os.path.join(BASE, "logs", "stepwatch.log")
STALE_SEC = 600          # 超过这么久没更新就当 idle（我可能在等你说话）
IDLE_AFTER_END = 3.0     # 一轮跑完后气泡再留 3 秒，让人看清最后一步


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def parse(lines):
    """把日志行变成按 seq 去重、升序的记录列表（尾部倒读会重复读到同一段）。"""
    by_seq = {}
    for ln in lines:
        ln = ln.strip()
        if not ln or '"type"' not in ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        seq = r.get("seq")
        if seq is None:
            continue
        if seq not in by_seq:
            by_seq[seq] = r
    return [by_seq[k] for k in sorted(by_seq)]


def progress(recs):
    """返回 {open, n, total, tool}。纯函数，方便验证。

    step/start 自带 {"turn": 181, "step": 5}，所以步号是绝对序号，
    不依赖 tail 窗口里能不能看到 turn/start（尾部倒读常常只覆盖几 MB）。
    total 用「最近见过的其他轮次的步数」做分母，取不到就退化成 n。
    """
    turn = None
    last_step = None
    last_step_idx = -1
    boundary = None
    tool = None
    maxturn = {}
    for i, r in enumerate(recs):
        t = r.get("type")
        d = r.get("data") or {}
        if t == "turn/start":
            if d.get("turn") is not None:
                turn = d.get("turn")
            boundary = "start"
        elif t == "turn/end":
            boundary = "end"
        elif t == "step/start":
            if d.get("turn") is not None:
                turn = d.get("turn")
            if d.get("step") is not None:
                last_step = int(d.get("step"))
                last_step_idx = i
                maxturn[turn] = max(maxturn.get(turn, 0), last_step)
            boundary = "start"
        elif t in ("tool/ptc-dispatch-start", "tool/call"):
            if d.get("name"):
                tool = str(d.get("name"))
    open_ = last_step is not None and boundary == "start"
    n = last_step if open_ else 0
    others = [v for k, v in maxturn.items() if k != turn and v]
    total = max(others) if others else n
    total = max(total, n)
    return {"open": bool(open_), "n": n, "total": total, "tool": tool}


def text_of(p, label=""):
    """把 progress 渲染成人话。label 是可选的任务名（太长会截）。"""
    n, total = p["n"], p["total"]
    if not p["open"] or n <= 0:
        return ""
    head = ("第 %d/%d 步" % (n, total)) if total > n else ("第 %d 步" % n)
    tool = (p.get("tool") or "").strip()
    body = head + (" · " + tool if tool else "")
    if label:
        body = body + " · " + label[:18]
    return body


def snapshot(path=None, label=""):
    """读会话 → 返回状态 dict。path=None 表示自动定位当前会话。"""
    try:
        sys.path.insert(0, HERE)
        import ctx_watermark as cw
    except Exception as e:
        return {"state": "idle", "reason": "import: %s" % e}
    p = path or cw.current_session_file()
    if not p or not os.path.isfile(p):
        return {"state": "idle", "reason": "no session file"}
    txt = cw._tail_text(p)
    if not txt:
        return {"state": "idle", "reason": "empty tail"}
    pr = progress(parse(txt.splitlines()))
    txt_body = text_of(pr, label)
    out = {"state": "progress" if txt_body else "idle", "n": pr["n"], "total": pr["total"],
           "tool": pr["tool"], "open": pr["open"], "text": txt_body,
           "at": time.time(), "session": os.path.basename(os.path.dirname(p))}
    return out


def write(out, path=None):
    p = path or OUT_PATH
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception as e:
        log("write failed " + str(e)[:120])
        return False


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    path = opt(argv, "--path")
    out_path = opt(argv, "--out")
    label = opt(argv, "--label", "") or ""
    if "--json" in argv:
        print(json.dumps(snapshot(path, label), ensure_ascii=False))
        return 0
    if "--loop" in argv:
        try:
            interval = float(opt(argv, "--interval", "2") or 2)
        except Exception:
            interval = 2.0
        interval = max(1.0, interval)
        log("loop start interval %.1fs" % interval)
        while True:
            try:
                st = snapshot(path, label)
                write(st, out_path)
            except Exception as e:
                log("cycle error " + str(e)[:160])
            time.sleep(interval)
    st = snapshot(path, label)
    ok = write(st, out_path)
    print(json.dumps(st, ensure_ascii=False))
    if st.get("reason") == "no session file":
        return 3
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
