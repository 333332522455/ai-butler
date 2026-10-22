# -*- coding: utf-8 -*-
"""ai-butler 配置读取（config.json + 环境变量覆盖）。

优先级：环境变量 > <BASE_DIR>/config.json > 内置默认值。
BASE_DIR：AI_BUTLER_HOME > 本文件所在 tools/ 的上一级。
这样同一份代码在开发树（D:\sleep）和安装目录（%LOCALAPPDATA%\ai-butler）都成立。

config.json 由 install.ps1 或 wizard.py 生成，只放「这台机器上怎么跑」，
不放任何个人数据（对话、记忆、课表、密钥都不在这里）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BASE_DIR = os.environ.get("AI_BUTLER_HOME") or os.path.dirname(HERE)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")

DEFAULTS = {
    "name": "ai-butler",
    "version": "1.0",
    "installDir": BASE_DIR,
    "hotkey": "ctrl+alt+space",
    "inbox": "http://127.0.0.1:43120/wechat-bridge/inbox",
    "shot": True,
    "taskName": "ai-butler",
}

_TRUE = ("1", "true", "yes", "on", "y")


def _coerce(key, value):
    if key == "shot":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUE
    return value


def load(path=None):
    """返回合并后的配置 dict；文件坏了就退回默认值，不抛。"""
    cfg = dict(DEFAULTS)
    cfg["installDir"] = BASE_DIR
    p = path or CONFIG_PATH
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in cfg or k in ("python", "pythonw", "installed"):
                    cfg[k] = _coerce(k, v)
    except (OSError, ValueError):
        pass

    # 环境变量覆盖（发给子进程的临时开关用）
    if os.environ.get("AI_BUTLER_HOTKEY"):
        cfg["hotkey"] = os.environ["AI_BUTLER_HOTKEY"]
    if os.environ.get("AI_BUTLER_INBOX"):
        cfg["inbox"] = os.environ["AI_BUTLER_INBOX"]
    if os.environ.get("AI_BUTLER_SHOT") is not None:
        cfg["shot"] = os.environ["AI_BUTLER_SHOT"].strip().lower() in _TRUE
    return cfg


def save(patch, path=None):
    """把 patch 合进现有 config.json（保留 python/pythonw 等安装器写的字段）。"""
    p = path or CONFIG_PATH
    cur = {}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cur = data
    except (OSError, ValueError):
        pass
    cur.update(patch or {})
    cur.setdefault("name", "ai-butler")
    cur.setdefault("version", "1.0")
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    return cur


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(load(), ensure_ascii=False, indent=2))
    print("config: %s" % CONFIG_PATH)
