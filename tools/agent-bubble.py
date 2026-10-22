# -*- coding: utf-8 -*-
"""操控气泡：轮询 logs/agent-state.json，在右下角显示「正在操控屏幕 / 需要接管」。

状态文件由 tools/desktop.py 写：{state: controlling|waiting|idle, text, since}
用法：
  python agent-bubble.py            常驻（隐藏主窗，只轮询）
  python agent-bubble.py --selftest 显示 6 秒 controlling 后退出（验证 UI）
"""
import ctypes
import json
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.environ.get("AI_BUTLER_HOME") or os.path.dirname(HERE)
LOG_DIR = os.path.join(BASE_DIR, "logs")
STATE_PATH = os.path.join(LOG_DIR, "agent-state.json")
LOG_PATH = os.path.join(LOG_DIR, "bubble.log")
POLL_MS = 200
STALE_SEC = 300
LINGER_SEC = 2.5  # 动作结束后再多留一会儿，否则单次点击只闪 100ms 用户看不见


def _enable_dpi():
    """必须在建 Tk 之前调用，否则窗口单位(逻辑)与屏幕尺寸(物理)混用，气泡会溢出屏幕右缘。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _dpi_scale():
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


_enable_dpi()
SCALE = _dpi_scale()
W, H = int(300 * SCALE), int(72 * SCALE)
MARGIN_X, MARGIN_Y = int(24 * SCALE), int(56 * SCALE)
# Tk 字号正数单位是「点」，在 200% DPI 下会被放大成 2 倍像素；负数才是像素高。
F_TITLE = -max(18, int(round(17 * SCALE)))
F_BODY = -max(16, int(round(15 * SCALE)))


def log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def read_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("since", 0)) > STALE_SEC:
            return {"state": "idle", "text": ""}
        return d
    except Exception:
        return {"state": "idle", "text": ""}


def write_state(state, text=""):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"state": state, "text": text, "since": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


STYLES = {
    "controlling": ("#f0b030", "正在操控屏幕", "请勿动鼠标键盘"),
    "waiting": ("#e24a4a", "需要你接管", "已暂停，请看一眼再继续"),
}


class Bubble:
    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.94)
        except Exception:
            pass
        self.win.configure(bg="#151019")
        self.frame = tk.Frame(self.win, bg="#151019", highlightthickness=2,
                              highlightbackground="#f0b030")
        self.frame.pack(fill="both", expand=True)
        self.title = tk.Label(self.frame, text="", bg="#151019", fg="#f0b030",
                              font=("Microsoft YaHei", F_TITLE, "bold"), anchor="w")
        self.title.pack(fill="x", padx=12, pady=(8, 0))
        self.body = tk.Label(self.frame, text="", bg="#151019", fg="#d8cfe0",
                             font=("Microsoft YaHei", F_BODY), anchor="w", justify="left")
        self.body.pack(fill="x", padx=12, pady=(2, 8))
        self.win.withdraw()
        self.shown = ""
        self.idle_since = 0.0

    def update(self, st):
        state = st.get("state", "idle")
        if state not in STYLES:
            if self.shown:
                now = time.time()
                if not self.idle_since:
                    self.idle_since = now
                if now - self.idle_since >= LINGER_SEC:
                    self.win.withdraw()
                    self.shown = ""
                    self.idle_since = 0.0
            return
        self.idle_since = 0.0
        color, label, fallback = STYLES[state]
        text = (st.get("text") or "").strip() or fallback
        self.frame.configure(highlightbackground=color)
        self.title.configure(text=label, fg=color)
        self.body.configure(text=text[:70])
        if self.shown != state or not self.win.winfo_ismapped():
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            self.win.geometry("%dx%d+%d+%d" % (W, H, sw - W - MARGIN_X, sh - H - MARGIN_Y))
            self.win.deiconify()
            self.win.lift()
            self.shown = state
            self.win.update_idletasks()
            log("geom %s screen %dx%d scale %.2f" % (self.win.winfo_geometry(), sw, sh, SCALE))


def main():
    root = tk.Tk()
    root.withdraw()
    if "--selftest" in sys.argv:
        b = Bubble(root)
        write_state("controlling", "自检：点击「确定」按钮")
        log("selftest show")
        root.after(POLL_MS, lambda: b.update(read_state()))
        root.after(6000, lambda: (write_state("idle", ""), root.destroy()))
        root.mainloop()
        log("selftest done")
        return 0
    b = Bubble(root)
    log("bubble start")

    def tick():
        try:
            b.update(read_state())
        except Exception as e:
            log("tick error " + str(e)[:120])
        root.after(POLL_MS, tick)

    root.after(POLL_MS, tick)
    try:
        root.mainloop()
    finally:
        log("bubble exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
