# -*- coding: utf-8 -*-
"""全局呼出条：Ctrl+Alt+Space 在任意窗口唤出输入条，回车把话投给当前 DSH 会话。

流程：RegisterHotKey 抢全局热键 -> 弹出无边框置顶窄条 -> 回车 ->
      (可选) 截屏 -> POST {"text": ...} 到 /wechat-bridge/inbox -> 隐藏。

约定：
  * 桥的 mode=followup 会排队，不打断正在跑的那一轮；
  * 截图路径附在文字末尾，模型收到后自己决定要不要 read_image；
  * 命中黑名单（密码/支付/聊天/邮箱/私密）时只发文字不截图。

热键/投递地址/截图开关来自 config.json（wizard.py 生成），环境变量 AI_BUTLER_HOTKEY /
AI_BUTLER_INBOX / AI_BUTLER_SHOT 可临时覆盖。默认 ctrl+alt+space，截图开。

用法：
  python hotkey.py            常驻
  python hotkey.py --no-shot  不截图
  python hotkey.py --selftest 只验证热键能否注册，立刻注销退出
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import window_shot  # noqa: E402  截当前活动窗口 + 黑名单（同目录）
import license  # noqa: E402  离线激活码（同目录，见任务书 9.5）
import abconfig  # noqa: E402  config.json + 环境变量合并（同目录）

# 数据目录：AI_BUTLER_HOME 显式指定 > 默认取 tools/ 的上一级。
# 这样同一份代码在 D:\sleep（开发）和安装目录（分发）下都落在自己的树里，不写死绝对路径。
CFG = abconfig.load()
BASE_DIR = abconfig.BASE_DIR
LOG_DIR = abconfig.LOG_DIR

INBOX_URL = CFG.get("inbox") or "http://127.0.0.1:43120/wechat-bridge/inbox"
SHOT_ENABLED = bool(CFG.get("shot", True))
SHOT_DIR = window_shot.SHOT_DIR
HOTKEY_ID = 0xB001
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
             "shift": MOD_SHIFT, "win": MOD_WIN}
VK_NAMES = {"space": VK_SPACE, "enter": 0x0D, "tab": 0x09, "esc": 0x1B}
for _i in range(1, 13):
    VK_NAMES["f%d" % _i] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_NAMES[_c] = ord(_c.upper())


def parse_hotkey(spec):
    """'ctrl+alt+space' -> (mods, vk)；写错就退回默认 ctrl+alt+space，不静默失效。"""
    mods, vk = 0, 0
    for part in (spec or "").lower().replace(" ", "").split("+"):
        if part in MOD_NAMES:
            mods |= MOD_NAMES[part]
        elif part in VK_NAMES:
            vk = VK_NAMES[part]
    if not mods or not vk:
        return MOD_CONTROL | MOD_ALT, VK_SPACE
    return mods, vk


HOTKEY_SPEC = CFG.get("hotkey") or "ctrl+alt+space"
HOTKEY_MODS, HOTKEY_VK = parse_hotkey(HOTKEY_SPEC)

LOG_PATH = os.path.join(LOG_DIR, "hotkey.log")


def log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


BLOCK_WORDS = [
    "1password", "bitwarden", "keepass", "lastpass", "密码", "password",
    "网银", "银行", "bank", "支付宝", "alipay", "微信支付", "支付",
    "邮箱", "mail", "outlook", "foxmail", "私密", "隐私", "登录", "login",
    "微信", "wechat", "qq",
]

def _enable_dpi():
    """必须在 tk.Tk() 之前调用：否则屏幕尺寸拿到物理值、窗口坐标按逻辑值放，条会错位。"""
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

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or ""


def blocked(title):
    low = (title or "").lower()
    return any(w in low for w in BLOCK_WORDS)


def send(text):
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(INBOX_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300, r.status
    except Exception as e:
        return False, str(e)


def hotkey_thread(q, stop, shot=True):
    if not user32.RegisterHotKey(None, HOTKEY_ID, HOTKEY_MODS | MOD_NOREPEAT, HOTKEY_VK):
        q.put(("error", ctypes.get_last_error() or -1))
        return
    q.put(("ready", 0))
    msg = wintypes.MSG()
    try:
        while not stop.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY:
                    # 在这里抓：呼出条还没显示，前台还是用户真正的窗口。
                    # 截图也在这一刻做——「呼出时截屏」＝他按下热键时看到的那一屏，
                    # 等打完字再截，画面早变了，而且条自己会挡住中间一条。
                    fg = {}
                    try:
                        fg = window_shot.foreground_info()
                        hit, why = window_shot.is_blocked(fg)
                        fg["block"] = why if hit else ""
                        if shot and not hit:
                            fg["shot"] = window_shot.capture(fg)
                    except Exception as e:
                        fg["err"] = repr(e)
                    q.put(("hotkey", fg))
            else:
                time.sleep(0.04)
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)


class Bar:
    """全局呼出条。

    两个在 200% DPI 屏上踩过的坑：
      * 尺寸/字体都要按 SCALE 折算成物理像素（Tk 正数字号是「点」，会再放大一倍）；
      * 无边框置顶窗口拿到热键后必须自己抢前台，否则 Esc/回车根本到不了 entry。
    """

    def __init__(self, root, no_shot):
        import tkinter as tk
        self.tk = tk
        self.root = root
        self.no_shot = no_shot
        self.fg = None          # 按下热键那一刻的前台窗口（截图/黑名单都用它，不能用「现在」的前台）
        self.sending = False
        self.hide_timer = None
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        # 不透明：0.96 时下面的正文会透出来，看着像渲染错乱
        try:
            self.win.attributes("-alpha", 1.0)
        except Exception:
            pass
        self.win.configure(bg="#141018")
        self.W = int(460 * SCALE)
        self.H = int(50 * SCALE)
        self.MARGIN_TOP = int(150 * SCALE)
        frame = tk.Frame(self.win, bg="#141018", highlightthickness=2, highlightbackground="#6b5a7a")
        frame.pack(fill="both", expand=True)
        self.entry = tk.Entry(frame, bg="#141018", fg="#f0e6d2", insertbackground="#f0e6d2",
                              relief="flat", font=("Microsoft YaHei", -int(13 * SCALE)))
        self.entry.pack(fill="x", padx=14, pady=(10, 2))
        self.hint = tk.Label(frame, text="", bg="#141018", fg="#8f7f9f",
                             font=("Microsoft YaHei", -int(9 * SCALE)), anchor="w")
        self.hint.pack(fill="x", padx=14, pady=(0, 6))
        # 窗口级绑定：entry 没拿到键盘焦点时，事件也可能落在顶层窗口上
        for w in (self.win, frame, self.entry):
            w.bind("<Return>", self.on_enter)
            w.bind("<Escape>", lambda e: self.hide())
        self.win.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        for w in (self.win, frame, self.entry):
            w.bind("<Button-1>", lambda e: self.entry.focus_force())
        self.had_focus = False
        self.win.withdraw()

    # ---- 显示 / 隐藏 ----
    def _place(self):
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - self.W) // 2
        y = int(sh * 0.42) - self.H // 2
        self.win.geometry("%dx%d+%d+%d" % (self.W, self.H, x, y))
        return sw, sh, x, y

    def _steal_foreground(self):
        """热键回调里前台是别的程序，Tk 的 focus_force 改不了 Windows 前台锁，必须 Win32 硬抢。"""
        try:
            hwnd = int(self.win.wm_frame(), 16)
        except Exception:
            return False
        u = ctypes.windll.user32
        u.ShowWindow(wintypes.HWND(hwnd), 5)  # SW_SHOW
        fg = u.GetForegroundWindow()
        tid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(fg, ctypes.byref(tid))
        tid_fg = tid.value
        tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = False
        if tid_fg and tid_fg != tid_me:
            attached = bool(u.AttachThreadInput(tid_fg, tid_me, True))
        ok = bool(u.SetForegroundWindow(wintypes.HWND(hwnd)))
        u.SetFocus(wintypes.HWND(hwnd))
        if attached:
            u.AttachThreadInput(tid_fg, tid_me, False)
        return ok

    def show(self, fg=None):
        # 关键：fg 必须是「热键按下那一刻」抓的，因为本窗口一显示就会把前台抢走
        self.fg = fg or window_shot.foreground_info()
        self.sending = False
        self.had_focus = False
        self.win.deiconify()
        sw, sh, x, y = self._place()
        self.win.lift()
        self.entry.delete(0, "end")
        self.entry.config(state="normal")
        self.hint.config(text="回车发送 · Esc 关闭")
        self.entry.focus_force()
        self._steal_foreground()
        log("show geom %dx%d+%d+%d screen %dx%d scale %.2f" % (self.W, self.H, x, y, sw, sh, SCALE))
        if self.hide_timer:
            try:
                self.win.after_cancel(self.hide_timer)
            except Exception:
                pass
        self.hide_timer = self.win.after(25000, self.hide)  # 兜底：25 秒没动自己消失

    def hide(self):
        if self.hide_timer:
            try:
                self.win.after_cancel(self.hide_timer)
            except Exception:
                pass
            self.hide_timer = None
        self.had_focus = False
        self.win.withdraw()

    def _on_focus_in(self, _evt):
        self.had_focus = True

    def _on_focus_out(self, _evt):
        if self.sending:
            return
        self.win.after(150, self._maybe_hide)

    def _maybe_hide(self):
        """只有「曾经拿到过键盘焦点、之后又丢了」才自动关；从没拿到过就留着等用户点。"""
        if self.sending or not self.had_focus:
            return
        try:
            if self.win.focus_get() is None:
                self.hide()
        except Exception:
            pass

    def on_enter(self, _evt):
        if self.sending:
            return
        text = self.entry.get().strip()
        if not text:
            self.hide()
            return
        self.sending = True
        fg = self.fg or {}
        why = fg.get("block") or ""
        shot = fg.get("shot") or ""
        self.hint.config(text="发送中（此窗口不截图）" if why else "发送中…")
        self.win.update_idletasks()

        def work():
            payload = text
            if why:
                # 黑名单命中时连标题都不外发，只报进程名和命中类别
                payload += "\n\n[屏幕] 前台是 %s，命中黑名单(%s)，未截图" % (fg.get("exe") or "?", why)
            elif shot:
                payload += "\n\n[屏幕] 前台是 %s（%s）\n[截图] %s" % (
                    fg.get("exe") or "?", fg.get("title") or "无标题", shot)
            else:
                payload += "\n\n[屏幕] 前台是 %s（%s），截图失败（窗口不可见/太小/无 PIL）" % (
                    fg.get("exe") or "?", fg.get("title") or "无标题")
            log("send shot=%s block=%s title=%s" % (shot or "-", why or "-", (fg.get("title") or "")[:60]))
            ok, info = send(payload)

            def done():
                self.hint.config(text="已送达" if ok else ("发送失败：" + str(info)))
                self.win.after(700, self.hide)

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()


def selftest():
    q = queue.Queue()
    stop = threading.Event()
    t = threading.Thread(target=hotkey_thread, args=(q, stop), daemon=True)
    t.start()
    kind, info = q.get(timeout=5)
    stop.set()
    time.sleep(0.2)
    if kind == "ready":
        log("selftest OK: Ctrl+Alt+Space registered and released")
        return 0
    log("selftest FAIL register err=" + str(info) + " (1409 = already taken)")
    return 2


def main():
    if "--selftest" in sys.argv:
        return selftest()
    # 授权闸：未激活就不常驻（开发机可用 AI_BUTLER_DEV=1 跳过）
    if not license.is_licensed():
        ok, why = license.verify()
        log("license check failed: " + why)
        print("未激活：%s\n机器码 %s，请把机器码发给作者换激活码。"
              % (why, license.group(license.machine_code())))
        return 4
    no_shot = ("--no-shot" in sys.argv) or (not SHOT_ENABLED)
    try:
        window_shot.ensure_rules()
        window_shot.cleanup()
    except Exception as e:
        log("window_shot init fail: %r" % (e,))
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    bar = Bar(root, no_shot)
    q = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=hotkey_thread, args=(q, stop, not no_shot), daemon=True).start()

    def poll():
        try:
            while True:
                kind, info = q.get_nowait()
                if kind == "ready":
                    log("hotkey ready: %s" % HOTKEY_SPEC)
                elif kind == "error":
                    log("hotkey register failed err=" + str(info))
                    root.destroy()
                    return
                elif kind == "hotkey":
                    log("hotkey pressed")
                    bar.show(info)
        except queue.Empty:
            pass
        root.after(60, poll)

    root.after(60, poll)
    try:
        root.mainloop()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
