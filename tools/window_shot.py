# -*- coding: utf-8 -*-
"""窗口截图 + 黑名单判定（全局呼出条专用）

只截「当前活动窗口」，不截整屏。命中黑名单（密码/支付/聊天/邮箱/私密）时不截图，
只返回原因，让调用方把文字发出去。

命令行：
  python tools/window_shot.py --check   只打印当前前台窗口信息与判定（不截图）
  python tools/window_shot.py --once    截一张，打印路径
  python tools/window_shot.py --clean   清理超过保留期的旧截图
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
# 数据目录：AI_BUTLER_HOME 显式指定 > tools/ 的上一级（开发树与安装目录通用）
BASE_DIR = os.environ.get("AI_BUTLER_HOME") or os.path.dirname(HERE)
SHOT_DIR = os.path.join(BASE_DIR, "logs", "shots")
RULE_PATH = os.path.join(HERE, "shot-blocklist.json")   # 规则跟代码走，同目录
KEEP_DAYS = 3
MIN_SIDE = 80  # 太小的窗口不值得截（工具提示之类）

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 默认黑名单：命中任意词就不截图。分类只为可读，匹配时不区分。
DEFAULT_RULES = {
    "_说明": "title 里出现这些词（不区分大小写）或进程名命中 exe 列表，就只发文字不截图。改完不用重启热键进程，下一次呼出生效。",
    "凭据": ["1password", "bitwarden", "keepass", "lastpass", "password", "passwd",
             "密码", "凭据", "credential", "密钥", "token"],
    "支付": ["支付宝", "alipay", "微信支付", "wechat pay", "网银", "银行", "bank",
             "转账", "付款", "支付", "checkout", "信用卡"],
    "聊天": ["微信", "wechat", "weixin", "qq", "tim", "telegram", "whatsapp", "discord"],
    "邮箱": ["outlook", "foxmail", "thunderbird", "邮箱", "mail.google", "gmail", "mail"],
    "私密": ["私密", "隐私", "无痕", "incognito", "private browsing"],
    "登录": ["登录", "登陆", "login", "sign in", "signin", "验证码", "扫码登录"],
    "exe": ["1password.exe", "keepass.exe", "bitwarden.exe", "wechat.exe", "weixin.exe",
            "qq.exe", "tim.exe", "outlook.exe", "foxmail.exe", "discord.exe", "telegram.exe"],
}


def _enable_dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _exe_name(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def foreground_info():
    """返回前台窗口的 hwnd / 标题 / pid / 进程名 / 屏幕矩形。"""
    hwnd = user32.GetForegroundWindow()
    title = ""
    pid = 0
    rc = wintypes.RECT()
    if hwnd:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value or ""
        d = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(d))
        pid = d.value
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return {
        "hwnd": int(hwnd or 0),
        "title": title,
        "pid": int(pid),
        "exe": _exe_name(pid) if pid else "",
        "rect": [rc.left, rc.top, rc.right, rc.bottom],
        "visible": bool(user32.IsWindowVisible(hwnd)) if hwnd else False,
        "minimized": bool(user32.IsIconic(hwnd)) if hwnd else False,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def load_rules():
    if os.path.exists(RULE_PATH):
        try:
            with open(RULE_PATH, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_RULES


def ensure_rules():
    """第一次跑就把默认黑名单落盘成 json，方便用户自己改。"""
    if not os.path.exists(RULE_PATH):
        try:
            os.makedirs(os.path.dirname(RULE_PATH), exist_ok=True)
            with open(RULE_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_RULES, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def is_blocked(info, rules=None):
    """(是否命中, 原因)。原因会写进日志，方便用户问「为什么不截图」。"""
    rules = rules if rules is not None else load_rules()
    title = (info.get("title") or "").lower()
    exe = (info.get("exe") or "").lower()
    exes = [x.lower() for x in rules.get("exe", [])]
    if exe and exe in exes:
        return True, "进程黑名单:" + exe
    for cat, words in rules.items():
        if cat.startswith("_") or cat == "exe":
            continue
        for w in words:
            if w and w.lower() in title:
                return True, cat + ":" + w
    return False, ""


def capture(info=None):
    """截当前活动窗口，返回 png 路径；跳过的原因用 blocked_reason() 另取。"""
    info = info or foreground_info()
    if not info.get("hwnd") or info.get("minimized") or not info.get("visible"):
        return ""
    left, top, right, bottom = info["rect"]
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        return ""
    try:
        from PIL import ImageGrab
    except Exception:
        return ""
    os.makedirs(SHOT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(SHOT_DIR, stamp + ".png")
    i = 1
    while os.path.exists(path):
        path = os.path.join(SHOT_DIR, stamp + "-%d.png" % i)
        i += 1
    try:
        img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        img.save(path)
        return path
    except Exception:
        return ""


def cleanup(days=KEEP_DAYS):
    """删掉超过保留期的截图，返回删除个数。"""
    if not os.path.isdir(SHOT_DIR):
        return 0
    cut = time.time() - days * 86400
    n = 0
    for name in os.listdir(SHOT_DIR):
        p = os.path.join(SHOT_DIR, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cut:
                os.remove(p)
                n += 1
        except Exception:
            pass
    return n


def describe(info, rules=None):
    """给日志/回执用的一行摘要。"""
    blocked, why = is_blocked(info, rules)
    return "%s | %s | %s | %s" % (
        info.get("exe") or "?", info.get("title") or "(无标题)",
        "BLOCK " + why if blocked else "OK", info.get("rect"))


def main(argv):
    _enable_dpi()
    ensure_rules()
    if "--clean" in argv:
        print("cleaned", cleanup())
        return 0
    info = foreground_info()
    print(describe(info))
    if "--check" in argv:
        return 0
    if "--once" in argv:
        blocked, why = is_blocked(info)
        if blocked:
            print("跳过截图：命中黑名单 " + why)
            return 3
        p = capture(info)
        print("shot:", p or "(失败：窗口不可见/太小/无 PIL)")
        return 0 if p else 4
    print("用法: --check | --once | --clean")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
