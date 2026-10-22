# -*- coding: utf-8 -*-
"""ai-butler 首次运行向导：只生成 config.json，不打包任何个人数据。

    python tools/wizard.py                # 交互（默认值都在方括号里，直接回车即可）
    python tools/wizard.py --yes          # 全默认，非交互（install.ps1 用）
    python tools/wizard.py --show         # 只看当前生效配置
    python tools/wizard.py --hotkey alt+f9 --inbox http://127.0.0.1:43120/wechat-bridge/inbox --shot off

退出码：0 配置已写好 / 2 输入非法或用户取消
"""
import argparse
import ctypes
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import abconfig  # noqa: E402
import hotkey as hk  # noqa: E402  复用 parse_hotkey，保证向导与运行期解析一致

try:
    import license as lic
except Exception:  # 授权模块缺失不该挡住向导
    lic = None

HOTKEY_RE = re.compile(r"^[a-z0-9+]+$")
MOD_NOREPEAT = 0x4000
MOD_ORDER = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}


def _enable_dpi():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def norm_hotkey(spec):
    """规范化成 ctrl+alt+space 这种顺序；解析不了抛 ValueError。"""
    s = (spec or "").strip().lower().replace(" ", "").replace("control", "ctrl")
    parts = [p for p in s.split("+") if p]
    mods = sorted([p for p in parts if p in MOD_ORDER], key=lambda p: MOD_ORDER[p])
    others = [p for p in parts if p not in MOD_ORDER]
    if len(others) != 1:
        raise ValueError("需要恰好一个主键，例如 ctrl+alt+space")
    out = "+".join(mods + others)
    if not HOTKEY_RE.match(out):
        raise ValueError("热键里只能有 a-z 0-9 +")
    hk.parse_hotkey(out)  # 让运行期的解析器再确认一次
    return out


def probe_hotkey(spec):
    """真注册一次看能不能占上。返回 (ok, reason)。"""
    try:
        mods, vk = hk.parse_hotkey(spec)
    except Exception as e:
        return False, "解析不了：%s" % e
    # use_last_error=True 才能可靠取到 1409（已被占用）；windll 版本拿到的恒为 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = ctypes.c_bool
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UnregisterHotKey.restype = ctypes.c_bool
    hid = 0xB1F1
    ctypes.set_last_error(0)
    if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
        user32.UnregisterHotKey(None, hid)
        return True, "可用"
    err = ctypes.get_last_error()
    if err == 1409:
        return False, "已被其他程序占用"
    return False, "注册失败（错误码 %s），可能被系统保留" % err


def ask(prompt, default, noninteractive, validate=None):
    if noninteractive:
        return default
    while True:
        try:
            raw = input("%s [%s]: " % (prompt, default)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(2)
        val = raw or default
        if validate is None:
            return val
        ok, msg = validate(val)
        if ok:
            return val
        print("  输入不合法：%s" % msg)


def _hotkey_ok(v):
    try:
        spec = norm_hotkey(v)
    except ValueError as e:
        return False, str(e)
    free, msg = probe_hotkey(spec)
    if free:
        return True, ""
    if msg == "已被其他程序占用":
        return True, ""  # 允许坚持，运行期会报错写日志
    return False, msg


def _url_ok(v):
    if not re.match(r"^https?://[^\s/]+", v or ""):
        return False, "要写成 http://主机:端口/路径"
    return True, ""


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="ai-butler 首次运行向导（只写 config.json）")
    ap.add_argument("--yes", action="store_true", help="非交互，全部用默认值")
    ap.add_argument("--show", action="store_true", help="只打印当前生效配置")
    ap.add_argument("--hotkey")
    ap.add_argument("--inbox")
    ap.add_argument("--shot", choices=["on", "off"])
    ap.add_argument("--no-license", action="store_true", help="跳过授权这一步")
    args = ap.parse_args(argv)

    _enable_dpi()
    cfg = abconfig.load()

    if args.show:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        print("config: %s" % abconfig.CONFIG_PATH)
        if lic:
            ok, why = lic.verify()
            print("授权: %s（%s）" % ("已激活" if ok else "未激活", why))
        return 0

    noninteractive = args.yes
    print("ai-butler 首次运行向导")
    print("  配置写到 : %s" % abconfig.CONFIG_PATH)
    print("  程序目录 : %s" % abconfig.BASE_DIR)
    print("  只写这台机器怎么跑（热键/端口/截图开关），不放任何个人数据。")
    print("")

    if args.hotkey:
        try:
            hotspec = norm_hotkey(args.hotkey)
        except ValueError as e:
            print("  [X] 热键不合法：%s" % e)
            return 2
    else:
        hotspec = ask("呼出热键", cfg.get("hotkey") or "ctrl+alt+space", noninteractive, validate=_hotkey_ok)
        hotspec = norm_hotkey(hotspec)
    free, hk_msg = probe_hotkey(hotspec)
    print("  热键 %s：%s" % (hotspec, hk_msg))
    if not free and args.hotkey:
        print("  [X] 这个热键不可用，换一个")
        return 2

    inbox = args.inbox or ask("消息投递地址", cfg.get("inbox"), noninteractive, validate=_url_ok)
    if not _url_ok(inbox)[0]:
        print("  [X] 投递地址不合法：%s" % inbox)
        return 2

    if args.shot:
        shot = args.shot == "on"
    elif noninteractive:
        shot = bool(cfg.get("shot", True))
    else:
        cur = "on" if cfg.get("shot", True) else "off"
        shot = ask("呼出时截当前窗口（on/off，黑名单窗口不截）", cur,
                   False, validate=lambda v: (v in ("on", "off"), "只能填 on 或 off")) == "on"

    written = abconfig.save({
        "hotkey": hotspec,
        "inbox": inbox,
        "shot": shot,
        "installDir": abconfig.BASE_DIR,
        "wizardAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    print("")
    print("已写入 %s" % abconfig.CONFIG_PATH)
    print("  热键 : %s" % written.get("hotkey"))
    print("  投递 : %s" % written.get("inbox"))
    print("  截图 : %s" % ("开" if written.get("shot") else "关"))

    if lic and not args.no_license:
        ok, why = lic.verify()
        if ok:
            print("  授权 : 已激活")
        else:
            print("  授权 : 未激活（%s）" % why)
            print("         机器码 %s —— 发给作者换激活码，然后跑：" % lic.machine_code())
            print("         python tools\\license.py activate XXXX-XXXX-XXXX-XXXX")
            if not noninteractive:
                try:
                    code = input("  现在有激活码吗？（直接回车跳过）: ").strip()
                except (EOFError, KeyboardInterrupt):
                    code = ""
                if code:
                    rc = lic.activate(code)
                    print("  %s" % ("激活成功" if rc == 0 else "激活失败（码不对或不是这台机器）"))
    print("")
    print("下一步：结束 hotkey.py 进程，再跑 launchers\\start-hotkey-hidden.vbs 让新配置生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
