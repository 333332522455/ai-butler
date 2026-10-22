# -*- coding: utf-8 -*-
"""离线激活码（任务书 9.5 档 2）：机器码 -> 作者算码 -> 本地 HMAC 校验。

诚实前提：纯客户端校验防不住逆向，密钥必然在用户机器上。它防的是
“顺手把安装目录复制给别人”的人，不防专业破解。

机器码  = SHA256(MachineGuid + C 盘卷序列号) 前 16 位（大写十六进制，只算不传）
激活码  = HMAC_SHA256(密钥, 机器码) 前 16 位（大写，XXXX-XXXX-XXXX-XXXX）
license = %APPDATA%\\ai-butler\\license.json（含机器码、激活码、激活时间）

密钥来源（按顺序）：
  1. 环境变量 AI_BUTLER_LICENSE_KEY
  2. %APPDATA%\\ai-butler\\author.key（作者本机自行保存，绝不进仓库）
  3. <BASE_DIR>\\author.key（开发树里，.gitignore 挡住）

用法：
  python license.py status              看本机是否已激活（人读）
  python license.py code                打印本机机器码（发给作者）
  python license.py check               脚本用：已激活 exit 0，未激活 exit 3
  python license.py make --machine XX   作者用：为某机器码算激活码
  python license.py activate XXXX-...   写入 license.json
  python license.py --unbind            解绑（删 license.json），换机用
  python license.py --key-file P        指定密钥文件（调试用）
"""
import argparse
import ctypes
import hashlib
import hmac
import json
import os
import sys
import winreg
from ctypes import wintypes
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.environ.get("AI_BUTLER_HOME") or os.path.dirname(HERE)
APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ai-butler")
LICENSE_PATH = os.environ.get("AI_BUTLER_LICENSE") or os.path.join(APP_DIR, "license.json")
KEY_CANDIDATES = [os.path.join(APP_DIR, "author.key"), os.path.join(BASE_DIR, "author.key")]

DEV_FLAG = "AI_BUTLER_DEV"


# ---------- 机器码 ----------

def _machine_guid():
    """注册表里的 MachineGuid；读不到返回空串（不抛，后面还有卷序列号兜底）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(val)
    except OSError:
        return ""


def _volume_serial(root="C:\\"):
    """C 盘卷序列号。GetVolumeInformationW 的最后一个出参。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    serial = wintypes.DWORD(0)
    maxlen = wintypes.DWORD(0)
    flags = wintypes.DWORD(0)
    fsname = ctypes.create_unicode_buffer(256)
    ok = k32.GetVolumeInformationW(
        ctypes.c_wchar_p(root), None, 0,
        ctypes.byref(serial), ctypes.byref(maxlen), ctypes.byref(flags),
        fsname, 256)
    if not ok:
        return 0
    return int(serial.value)


def machine_code():
    """机器码：SHA256(MachineGuid + 卷序列号) 前 16 位大写。"""
    raw = "%s|%d" % (_machine_guid(), _volume_serial())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()[:16]


# ---------- 激活码 ----------

def group(code):
    """16 位 -> XXXX-XXXX-XXXX-XXXX。"""
    c = "".join(ch for ch in str(code).upper() if ch.isalnum())
    return "-".join(c[i:i + 4] for i in range(0, len(c), 4))


def norm(code):
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def expected_code(machine, key):
    digest = hmac.new(key.encode("utf-8"), machine.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest.upper()[:16]


def load_key(key_file=None):
    """密钥：环境变量 > 显式文件 > APPDATA > 开发树。找不到返回 None。"""
    env = os.environ.get("AI_BUTLER_LICENSE_KEY")
    if env and env.strip():
        return env.strip()
    cands = [key_file] if key_file else []
    cands += KEY_CANDIDATES
    for p in cands:
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    return v
            except OSError:
                continue
    return None


# ---------- license.json ----------

def read_license(path=None):
    path = path or LICENSE_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_license(machine, code, note="", path=None):
    path = path or LICENSE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "machine": machine,
        "code": group(code),
        "activatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": note,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def verify(machine=None, key=None, path=None):
    """返回 (ok, reason)。reason 直接可给人看。"""
    machine = machine or machine_code()
    path = path or LICENSE_PATH
    data = read_license(path)
    if not data:
        return False, "未激活（没有 %s）" % path
    if norm(data.get("machine", "")) != norm(machine):
        return False, "激活码不属于这台机器（换机了？先 --unbind 再重新激活）"
    k = key or load_key()
    if not k:
        return False, "找不到密钥，无法校验（作者本机才需要密钥；用户只需激活码）"
    want = expected_code(machine, k)
    if not hmac.compare_digest(norm(data.get("code", "")), want):
        return False, "激活码校验失败（文件被改过，或密钥不匹配）"
    return True, "已激活（%s 激活于 %s）" % (group(machine), data.get("activatedAt", "?"))


def is_licensed():
    """给别的工具调用：已激活或开发模式都为真。"""
    if os.environ.get(DEV_FLAG) == "1":
        return True
    ok, _ = verify()
    return ok


def unbind(path=None):
    path = path or LICENSE_PATH
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ---------- CLI ----------

def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--key-file", default=None, help="指定密钥文件")
    common.add_argument("--license", default=None, help="指定 license.json 路径（测试用）")

    ap = argparse.ArgumentParser(description="ai-butler 离线激活码", parents=[common])
    ap.add_argument("--unbind", action="store_true", help="删掉 license.json（换机用）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="人读状态", parents=[common])
    sub.add_parser("code", help="打印机器码", parents=[common])
    sub.add_parser("check", help="已激活 exit 0 / 未激活 exit 3", parents=[common])

    p_make = sub.add_parser("make", help="作者用：算激活码", parents=[common])
    p_make.add_argument("--machine", default=None, help="目标机器码，默认本机")

    p_act = sub.add_parser("activate", help="写入激活码", parents=[common])
    p_act.add_argument("code", help="XXXX-XXXX-XXXX-XXXX")
    p_act.add_argument("--note", default="", help="备注")

    args = ap.parse_args(argv)
    path = args.license or LICENSE_PATH

    if args.unbind:
        print("已解绑" if unbind(path) else "本来就没有 %s" % path)
        return 0

    cmd = args.cmd or "status"

    if cmd == "code":
        print(machine_code())
        return 0

    if cmd == "status":
        ok, why = verify(path=path, key=load_key(args.key_file))
        print("机器码: %s" % group(machine_code()))
        print(("已激活: " if ok else "未激活: ") + why)
        print("license: %s" % path)
        return 0

    if cmd == "check":
        ok, why = verify(path=path, key=load_key(args.key_file))
        print(("OK " if ok else "NO ") + why)
        return 0 if ok else 3

    if cmd == "make":
        k = load_key(args.key_file)
        if not k:
            print("没有密钥：设置 AI_BUTLER_LICENSE_KEY，或把密钥写到 %s" % KEY_CANDIDATES[0],
                  file=sys.stderr)
            return 4
        m = norm(args.machine or machine_code())
        if len(m) != 16:
            print("机器码应为 16 位十六进制，收到 %r" % args.machine, file=sys.stderr)
            return 2
        print(group(expected_code(m, k)))
        return 0

    if cmd == "activate":
        m = machine_code()
        given = norm(args.code)
        if len(given) != 16:
            print("激活码应为 16 位（XXXX-XXXX-XXXX-XXXX），收到 %r" % args.code, file=sys.stderr)
            return 2
        k = load_key(args.key_file)
        if not k:
            print("本机没有密钥，无法验证激活码是否正确；仍按原样写入。" % (), file=sys.stderr)
        elif not hmac.compare_digest(given, expected_code(m, k)):
            print("激活码与这台机器不符，未写入。", file=sys.stderr)
            return 2
        data = write_license(m, given, note=args.note, path=path)
        print("已激活 %s -> %s" % (group(m), data["code"]))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
