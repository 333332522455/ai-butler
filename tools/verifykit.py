#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verifykit：验证脚本的断言器 + 自检（防「验证脚本自己写错，把 FAIL 当 PASS」）。

为什么需要它：2026-10-22 一天里，我写的验证脚本自己错了四次——判 ASCII 前忘剥 BOM、
字面量少了方括号、比对 Pages 忘了归一化 CRLF、子进程输出按 GBK 解码（实际是 UTF-8）。
每次都把 PASS 判成 FAIL，也完全可能反过来把 FAIL 判成 PASS。

用法（在验证脚本里）：
    import sys, os; sys.path.insert(0, os.environ.get("AI_BUTLER_TOOLS", "."))
    from verifykit import Kit
    k = Kit("项目名")
    k.require_selfcheck()          # 自检不过 → 直接退出 3，后面的结论一律不可信
    k.chk("某个断言", cond, extra)
    rc = k.report()                # 打印 PASS/FAIL，返回 0 或 2

命令行：
    python tools/verifykit.py --selfcheck    # 0=自检通过，3=自检失败
    python tools/verifykit.py --audit        # 列出还没接入自检的 _verify_*.py
"""
import io
import os
import re
import sys
import glob

# 控制台默认是 GBK：extra 里只要出现 � 之类的字，print 就会 UnicodeEncodeError，
# 把「断言失败」变成「脚本崩了」，两种都不是想要的结论。导入时就把输出改成 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS = os.path.dirname(os.path.abspath(__file__))
BOM = b"\xef\xbb\xbf"


def strip_bom(b):
    return b[len(BOM):] if b.startswith(BOM) else b


def is_ascii(b):
    """先剥 BOM 再判：带 BOM 的纯 ASCII 文件也算 ASCII（BOM 不是内容）。"""
    return all(c < 128 for c in strip_bom(b))


def norm(b):
    """换行归一化：Git 存 LF、工作区常是 CRLF，不归一化永远 mismatch。"""
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def dec(b, prefer=None):
    """子进程输出解码：编码随 chcp 变（65001=UTF-8、默认=GBK），必须自动判。"""
    order = [prefer] if prefer else []
    order += ["utf-8", "gbk"]
    for enc in order:
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", "replace")


PARAM_RE = re.compile(r"(?m)^\s*param\s*\(")


def has_top_param(text):
    """只认顶格的 param(：脚本里 { param($n) } 这种嵌套不算。"""
    return bool(PARAM_RE.search(text))


class Kit:
    def __init__(self, name="", quiet=False):
        self.name = name
        self.ok = 0
        self.bad = []
        self.quiet = quiet

    def chk(self, label, cond, extra=""):
        if cond:
            self.ok += 1
            if not self.quiet:
                print("  ok   " + label)
        else:
            self.bad.append(label + (("  [" + str(extra) + "]") if extra != "" else ""))
            if not self.quiet:
                print("  FAIL " + label + (("  [" + str(extra) + "]") if extra != "" else ""))
        return bool(cond)

    def report(self):
        total = self.ok + len(self.bad)
        if not self.quiet:
            print("PASS %d / FAIL %d" % (self.ok, len(self.bad)))
            for b in self.bad:
                print("  FAIL: " + b)
        if total == 0:
            if not self.quiet:
                print("!! 一条断言都没跑，本次结果无效")
            return 2
        return 0 if not self.bad else 2

    def require_selfcheck(self):
        errs = selfcheck()
        if errs:
            print("!! verifykit 自检失败，本次验证结果不可信：")
            for e in errs:
                print("   - " + e)
            sys.exit(3)
        if not self.quiet:
            print("  ok   verifykit 自检（%d 项）" % selfcheck_cases())


def selfcheck_cases():
    return 12


def selfcheck():
    """拿真实踩过的坑当回归用例：每个 helper 都必须答对，断言器本身也必须会报错。"""
    errs = []

    def eq(label, got, want):
        if got != want:
            errs.append("%s：期望 %r，实际 %r" % (label, want, got))

    eq("ascii 带 BOM 应算 ASCII", is_ascii(BOM + b"abc"), True)
    eq("含中文应判非 ASCII", is_ascii(b"abc" + "你".encode("utf-8")), False)
    eq("strip_bom", strip_bom(BOM + b"x"), b"x")
    eq("CRLF 归一化后相等", norm(b"a\r\nb") == norm(b"a\nb"), True)
    eq("LF 与 CRLF 不应原样相等", b"a\r\nb" == b"a\nb", False)
    eq("UTF-8 输出解码", dec("这台机".encode("utf-8")), "这台机")
    eq("GBK 输出解码", dec("这台机".encode("gbk")), "这台机")
    eq("顶层 param 命中", has_top_param("param([switch]$x)"), True)
    eq("缩进的顶层 param 命中", has_top_param("  param([switch]$x)"), True)
    eq("嵌套 param 不该命中", has_top_param("$c = { param($n) $n }"), False)
    eq("字面量带方括号才算命中", "[scriptblock]::Create" in "[scriptblock]::Create($t)", True)

    k = Kit("selfcheck", quiet=True)
    k.chk("真条件", True)
    if k.ok != 1 or k.bad:
        errs.append("断言器把真条件判错了")
    k2 = Kit("selfcheck", quiet=True)
    k2.chk("假条件", False)
    if len(k2.bad) != 1 or k2.ok != 0:
        errs.append("断言器把假条件判成了通过（最危险）")
    if k2.report() != 2:
        errs.append("有 FAIL 时 report() 必须返回 2")
    if Kit("selfcheck", quiet=True).report() != 2:
        errs.append("一条断言都没跑时 report() 必须返回 2")
    return errs


def audit(root=None):
    root = root or os.environ.get("AI_BUTLER_LOGS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    rows = []
    for p in sorted(glob.glob(os.path.join(root, "_verify_*.py"))):
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rows.append((os.path.basename(p), "verifykit" in txt and "require_selfcheck" in txt))
    miss = [n for n, ok in rows if not ok]
    print("验证脚本 %d 个，已接入自检 %d 个，未接入 %d 个" % (len(rows), len(rows) - len(miss), len(miss)))
    for n in miss:
        print("   未接入: " + n)
    return 0 if not miss else 1


def main(argv):
    if "--selfcheck" in argv:
        errs = selfcheck()
        if errs:
            print("SELFCHECK FAILED")
            for e in errs:
                print("  - " + e)
            return 3
        print("selfcheck ok (%d cases)" % selfcheck_cases())
        return 0
    if "--audit" in argv:
        return audit()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))