#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dev.py — 本机统一执行器 + 体检器（agent 与人都用这条入口）。

为什么要有它。这台机器上跑命令反复踩三类坑，而 DSH 自带的 shell 工具在放大它们：
  1) 假成功 —— 受限沙箱下 ops_bash 会返回 completed，但其实命令没跑。
  2) 编码地狱 —— 子进程默认 GBK；ps1/py 混用时中文乱码甚至整脚本解析失败。
  3) 输出洪流 —— 一次编译的日志能把上下文撑爆，跑一次吃掉几万 token。

dev.py 的对策：
  * 每条命令都落一份日志，并往 logs/dev/index.jsonl 追加一行（时间/命令/退出码/耗时/日志路径）。
    “到底跑没跑”有独立证据，不依赖启动它的那层 shell 的返回值。
  * 子进程强制 PYTHONUTF8=1 / PYTHONIOENCODING=utf-8，读取时 utf-8 -> gbk 双解码。
  * 默认只回显输出尾部 N 行，完整内容留在日志里，按需 read。
  * 解释器与工具全用绝对路径，不依赖 PATH（node / git / gh 都不在 PATH 上）。

用法：
  dev.cmd run -- <任意命令行>         真跑，落日志，回显尾部
  dev.cmd run --tail 40 -- <命令>     只回显最后 40 行
  dev.cmd run --timeout 900 -- <命令> 自定义超时（秒，默认 300）
  dev.cmd py  <脚本> [参数...]        用本机 python 绝对路径跑
  dev.cmd node <脚本> [参数...]       用本机 node 绝对路径跑
  dev.cmd status                      一屏：端口 / 进程 / 上下文水位 / 最近验证与踩坑
  dev.cmd verify <项目> [--auto|--cmd C] [--review] [--task T]
  dev.cmd log [N]                     最近 N 条执行记录，默认 8
"""
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time

ROOT = r"D:\sleep"
PY = sys.executable
NODE = r"D:\sleep\tools\node22\node.exe"
GIT = r"D:\新建文件夹 (2)\Git\cmd\git.exe"
HANDOFF = os.path.join(ROOT, "tools", "handoff.py")
DEVLOG = os.path.join(ROOT, "logs", "dev")
INDEX = os.path.join(DEVLOG, "index.jsonl")
VERIFY_LOG = os.path.join(ROOT, "logs", "verify.jsonl")
TRAPS = os.path.join(ROOT, "logs", "traps.jsonl")
BRIDGE_CFG = r"D:\sleep\dsh-wechat-bridge\cordis.patch.yml"

# 上下文标尺：会话文件 8.4MB 约等于 100% 窗口（见 notify/watch-notify.py）
CTX_FULL_BYTES = 8.4 * 1024 * 1024
PORTS = [(43120, "DSH"), (43124, "QQ桥"), (8090, "路由代理"), (8080, "本机模型")]
DEFAULT_TAIL = 120
DEFAULT_TIMEOUT = 300


def _decode(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _tail_lines(path, n):
    """从文件尾部按行读，避免把几百 MB 日志整个读进内存。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size == 0:
        return ""
    chunk = min(size, 512 * 1024)
    with open(path, "rb") as f:
        f.seek(size - chunk)
        buf = f.read(chunk)
    text = _decode(buf)
    lines = text.splitlines()
    if chunk < size and lines:
        lines = lines[1:]  # 首行可能被截断，丢掉
    return "\n".join(lines[-n:])


def _slug(cmdline):
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", cmdline.strip())[:32].strip("_")
    return s or "cmd"


def _note_record(rec):
    try:
        os.makedirs(DEVLOG, exist_ok=True)
        with open(INDEX, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _spawn(cmdline, shell, timeout, cwd, label):
    os.makedirs(DEVLOG, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logpath = os.path.join(DEVLOG, "%s-%s-%d.log" % (stamp, _slug(label), os.getpid()))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("PLATFORMIO_CORE_DIR", r"D:\sleep\pio-core")
    t0 = time.time()
    code = 1
    with open(logpath, "wb") as f:
        try:
            p = subprocess.Popen(cmdline, shell=shell, stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, env=env, cwd=cwd)
        except OSError as exc:
            f.write(("无法启动：%s" % exc).encode("utf-8"))
            p = None
        if p is not None:
            try:
                code = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill(p.pid)
                code = 124
                f.write(("\n[dev] TIMEOUT after %ds\n" % timeout).encode("utf-8"))
    secs = round(time.time() - t0, 1)
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": label, "exit": code,
           "secs": secs, "log": logpath}
    _note_record(rec)
    return code, secs, logpath


def _kill(pid):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _report(code, secs, logpath, tail):
    body = _tail_lines(logpath, tail)
    if body:
        print(body)
    print("[dev] exit=%d  %.1fs  log=%s" % (code, secs, logpath))
    return 0 if code == 0 else (code if 0 < code < 125 else 1)


def cmd_run(argv):
    tail, timeout, cwd = DEFAULT_TAIL, DEFAULT_TIMEOUT, ROOT
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest = argv[i + 1:]
            break
        if a == "--tail" and i + 1 < len(argv):
            tail = int(argv[i + 1]); i += 2; continue
        if a == "--timeout" and i + 1 < len(argv):
            timeout = int(argv[i + 1]); i += 2; continue
        if a == "--cwd" and i + 1 < len(argv):
            cwd = argv[i + 1]; i += 2; continue
        rest.append(a); i += 1
    if not rest:
        print("用法：dev.cmd run [--tail N] [--timeout N] [--cwd D] -- <命令行>")
        return 1
    cmdline = " ".join(rest)
    code, secs, logpath = _spawn(cmdline, True, timeout, cwd, cmdline)
    return _report(code, secs, logpath, tail)


def cmd_interp(exe, argv):
    if not argv:
        print("用法：dev.cmd %s <脚本> [参数...]" % ("py" if exe == PY else "node"))
        return 1
    tail = DEFAULT_TAIL
    if "--tail" in argv:
        k = argv.index("--tail")
        if k + 1 < len(argv):
            tail = int(argv[k + 1])
            argv = argv[:k] + argv[k + 2:]
    code, secs, logpath = _spawn([exe] + argv, False, DEFAULT_TIMEOUT, ROOT, " ".join(argv))
    return _report(code, secs, logpath, tail)


def _port_open(port, timeout=0.6):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _proc_table():
    """toolhelp32 快照，返回 [(exe, pid, ppid)]，不依赖 psutil。"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []
    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snap = k.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    out = []
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE32)]
    k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE32)]
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out.append((e.szExeFile, e.th32ProcessID, e.th32ParentProcessID))
        ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    return out


def _session_glob():
    sid = os.environ.get("DSH_SESSION_ID", "")
    if not sid:
        try:
            with open(BRIDGE_CFG, encoding="utf-8") as f:
                m = re.search(r"sessionId:\s*'?(?:session-)?([0-9a-f]{8})", f.read())
            sid = ("session-" + m.group(1)) if m else ""
        except OSError:
            sid = ""
    if not sid:
        return ""
    return os.path.join(r"D:\dsh-home\sessions", "*", "session-%s*" % sid.strip("session-"),
                        "session.v4.jsonl.zstd")


def _current_session_file():
    pat = _session_glob()
    if not pat:
        return ""
    hits = glob.glob(pat)
    if not hits:
        return ""
    return max(hits, key=lambda p: os.path.getmtime(p))


def _jsonl_tail(path, n):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-n:]


def cmd_status(_argv):
    print("== 端口")
    for port, name in PORTS:
        print("  %-8s %-5d %s" % (name, port, "OK" if _port_open(port) else "DOWN"))
    print("== 进程")
    table = _proc_table()
    pw = [p for p in table if p[0].lower() == "pythonw.exe"]
    dsh = [p for p in table if p[0].lower() == "dsh desktop.exe"]
    print("  pythonw %d 个   DSH Desktop %d 个" % (len(pw), len(dsh)))
    print("== 上下文")
    sf = _current_session_file()
    if sf:
        mb = os.path.getsize(sf) / 1024.0 / 1024.0
        print("  %s" % os.path.basename(os.path.dirname(sf)))
        print("  会话文件 %.2f MB  估算水位 %d%%（标尺 %.1f MB = 100%%）"
              % (mb, min(999, int(mb * 1024 * 1024 / CTX_FULL_BYTES * 100)), CTX_FULL_BYTES / 1048576))
    else:
        print("  找不到当前会话文件（session_glob 为空）")
    print("== 最近验证")
    vs = _jsonl_tail(VERIFY_LOG, 3)
    for r in vs:
        print("  %s  %-4s exit=%s  %s" % (r.get("at", ""), "PASS" if r.get("pass") else "FAIL",
                                          r.get("exit"), (r.get("task") or r.get("project") or "")[:44]))
    if not vs:
        print("  （无记录）")
    print("== 最近踩坑")
    ts = _jsonl_tail(TRAPS, 3)
    for r in ts:
        print("  %s  %s" % (r.get("id") or r.get("at", ""), (r.get("symptom") or r.get("phenomenon") or "")[:60]))
    if not ts:
        print("  （无记录）")
    return 0


def cmd_verify(argv):
    if not argv:
        print("用法：dev.cmd verify <项目> [--auto|--cmd C] [--review] [--task T]")
        return 1
    cmdline = [PY, HANDOFF, "verify"] + argv
    code, secs, logpath = _spawn(cmdline, False, 900, ROOT, "verify " + " ".join(argv))
    return _report(code, secs, logpath, 80)


def cmd_log(argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 8
    rows = _jsonl_tail(INDEX, n)
    for r in rows:
        print("%s  exit=%-4s %5.1fs  %-46s %s" % (r.get("at", ""), r.get("exit"), r.get("secs", 0),
                                                  (r.get("cmd") or "")[:46], os.path.basename(r.get("log", ""))))
    if not rows:
        print("（还没有执行记录）")
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    table = {"run": cmd_run, "status": cmd_status, "verify": cmd_verify, "log": cmd_log}
    if cmd == "py":
        return cmd_interp(PY, rest)
    if cmd == "node":
        return cmd_interp(NODE, rest)
    fn = table.get(cmd)
    if fn is None:
        print("未知子命令：%s\n" % cmd)
        print(__doc__)
        return 1
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
