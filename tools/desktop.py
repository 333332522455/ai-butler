import sys, os, time, json, ctypes, subprocess
from ctypes import wintypes
u32 = ctypes.windll.user32
HERE = os.path.dirname(os.path.abspath(__file__))
ALLOW_PATH = os.path.join(HERE, 'desktop_allow.json')
BASE_DIR = os.environ.get('AI_BUTLER_HOME') or os.path.dirname(HERE)
STATE_PATH = os.path.join(BASE_DIR, 'logs', 'agent-state.json')
DWORD = wintypes.DWORD; WORD = wintypes.WORD; LONG = ctypes.c_long; ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', LONG), ('dy', LONG), ('mouseData', DWORD), ('dwFlags', DWORD), ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', WORD), ('wScan', WORD), ('dwFlags', DWORD), ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', DWORD), ('wParamL', WORD), ('wParamH', WORD)]
class UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]
class INPUT(ctypes.Structure):
    _fields_ = [('type', DWORD), ('u', UNION)]

KEYEVENTF_KEYUP = 2; KEYEVENTF_UNICODE = 4
MD = {'left': (2, 4), 'right': (8, 16), 'middle': (32, 64)}
MOUSEEVENTF_WHEEL = 0x0800; MOUSEEVENTF_MOVE = 1; SW_RESTORE = 9
VK = {'enter':13,'tab':9,'esc':27,'escape':27,'space':32,'backspace':8,'delete':46,
      'up':38,'down':40,'left':37,'right':39,'home':36,'end':35,'pageup':33,'pagedown':34,
      'ctrl':17,'alt':18,'shift':16,'win':91}

def dpi_on():
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try: u32.SetProcessDPIAware()
        except Exception: pass

def vrect():
    g = u32.GetSystemMetrics
    return (g(76), g(77), g(78), g(79))

def is_locked():
    try:
        o = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq LogonUI.exe'], capture_output=True, text=True, timeout=8)
        return 'LogonUI.exe' in (o.stdout or '')
    except Exception:
        return False

def send_inputs(items):
    arr = (INPUT * len(items))(*items)
    u32.SendInput(len(items), arr, ctypes.sizeof(INPUT))

def key_event(vk, up=False):
    it = INPUT(); it.type = 1
    it.u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    send_inputs([it])

def type_text(s):
    b = s.encode('utf-16-le')
    vals = [b[i] | (b[i+1] << 8) for i in range(0, len(b), 2)]
    items = []
    for v in vals:
        d = INPUT(); d.type = 1; d.u.ki = KEYBDINPUT(0, v, KEYEVENTF_UNICODE, 0, 0)
        u = INPUT(); u.type = 1; u.u.ki = KEYBDINPUT(0, v, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
        items.append(d); items.append(u)
    send_inputs(items)

def press(combo):
    parts = [p.strip().lower() for p in combo.split('+') if p.strip()]
    if not parts: return
    mods = parts[:-1]; main = parts[-1]
    for m in mods:
        if m in VK: key_event(VK[m])
    if main in VK:
        key_event(VK[main]); time.sleep(0.02); key_event(VK[main], True)
    else:
        for ch in main: type_text(ch)
    for m in reversed(mods):
        if m in VK: key_event(VK[m], True)

def mouse(flag, dx=0, dy=0, data=0):
    u32.mouse_event(flag, dx, dy, data, 0)

def click_at(x, y, button='left', count=1):
    u32.SetCursorPos(int(x), int(y)); time.sleep(0.06)
    d, u = MD.get(button, MD['left'])
    for _ in range(count):
        mouse(d); time.sleep(0.03); mouse(u); time.sleep(0.07)

def drag(x1, y1, x2, y2, ms=400):
    u32.SetCursorPos(int(x1), int(y1)); time.sleep(0.1)
    mouse(2); time.sleep(0.08)
    n = max(int(ms / 16), 8)
    for i in range(1, n + 1):
        u32.SetCursorPos(int(x1 + (x2 - x1) * i / n), int(y1 + (y2 - y1) * i / n))
        time.sleep(ms / 1000.0 / n)
    time.sleep(0.05); mouse(4)

def list_windows():
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        if not u32.IsWindowVisible(hwnd): return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0: return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        r = wintypes.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left < 40 or r.bottom - r.top < 40: return True
        pid = DWORD(); u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((int(hwnd), buf.value, int(pid.value), (r.left, r.top, r.right, r.bottom)))
        return True
    u32.EnumWindows(CB(cb), 0)
    return out

def find_window(pat):
    p = str(pat).lower()
    for hwnd, title, pid, rect in list_windows():
        if p in title.lower() or str(hwnd) == p:
            return hwnd, title, rect
    return None, None, None

def focus_window(pat):
    hwnd, title, rect = find_window(pat)
    if hwnd is None: print('NOT_FOUND', pat); return 2
    u32.ShowWindow(hwnd, SW_RESTORE)
    try: u32.SetForegroundWindow(hwnd)
    except Exception: pass
    time.sleep(0.3)
    print('FOCUSED', hwnd, title); return 0

def load_allow():
    try:
        with open(ALLOW_PATH, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception:
        return []

def save_allow(items):
    with open(ALLOW_PATH, 'w', encoding='utf-8') as f: json.dump(items, f, ensure_ascii=False, indent=2)

def allow_match(title):
    for it in load_allow():
        pat = it.get('pattern') if isinstance(it, dict) else str(it)
        if pat and pat.lower() in str(title).lower(): return True
    return False

def target_of(rest):
    w = opt(rest, '--window')
    if not w: return None
    hwnd, title, rect = find_window(w)
    if hwnd is None:
        print('ABORT target-not-found:', w); return 'ERR'
    allow = load_allow()
    if allow and not allow_match(title):
        print('ABORT not-in-whitelist:', title, '| 允许的:', [a.get('pattern') if isinstance(a, dict) else a for a in allow])
        return 'ERR'
    return (hwnd, title, rect)

def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else default
    return default

def guard(argv, what):
    if is_locked() and '--force' not in argv:
        print('ABORT locked: 桌面在锁屏，注入的', what, '会被系统丢弃。先解锁再试（或加 --force）')
        return 4
    return 0

def set_state(state, text=''):
    """写操控状态，给气泡进程轮询（controlling / idle / waiting）。"""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'state': state, 'text': text, 'since': time.time()}, f, ensure_ascii=False)
    except Exception:
        pass

def do(desc, fn, *a, **kw):
    """包一次真实注入动作：动作期间写 controlling，结束（含异常）写 idle。

    开头先停 0.25 秒：气泡进程 200ms 轮询一次，动作本身可能只花 50ms，
    不停一下就会整个动作落在两次轮询之间，用户永远看不到提示。
    """
    set_state('controlling', desc)
    time.sleep(0.25)
    try:
        return fn(*a, **kw)
    finally:
        set_state('idle', '')

def usage():
    print('desktop.py <cmd>');
    print('  观察: observe [--out PATH] | status | size | pos | windows | wake');
    print('  动作: focus PAT | move X Y | click [X Y] [--window PAT] [--button B] [--count N]');
    print('        drag X1 Y1 X2 Y2 [--ms N] | wheel DELTA [X Y]');
    print('        type TEXT [--window PAT] | key COMBO [--window PAT]');
    print('  白名单: allow list | allow add PAT [--note 说明] | allow rm PAT');
    print('  安全: 白名单非空时动作必须带 --window 且命中白名单；锁屏时动作默认拒绝(--force 强来)')

def main(argv):
    dpi_on()
    if not argv or argv[0] in ('-h', '--help', 'help'): usage(); return 0
    cmd = argv[0].lower(); rest = argv[1:]
    if cmd == 'status':
        p = wintypes.POINT(); u32.GetCursorPos(ctypes.byref(p))
        lk = is_locked()
        print('DESKTOP', 'LOCKED' if lk else 'UNLOCKED', 'size', vrect(), 'cursor', (p.x, p.y))
        return 3 if lk else 0
    if cmd == 'size': print('SIZE', vrect()); return 0
    if cmd == 'pos':
        p = wintypes.POINT(); u32.GetCursorPos(ctypes.byref(p)); print('POS', p.x, p.y); return 0
    if cmd == 'windows':
        for hwnd, title, pid, rect in list_windows(): print(hwnd, '|', pid, '|', rect, '|', title)
        return 0
    if cmd == 'focus':
        if not rest: usage(); return 1
        t = target_of(rest)
        if t == 'ERR': return 5
        if t:
            rc = focus_window(t[1]); return rc
        return focus_window(rest[0])
    if cmd == 'allow':
        items = load_allow()
        sub = rest[0] if rest else 'list'
        if sub == 'list':
            if not items: print('WHITELIST empty（不限制窗口）')
            for it in items: print(' -', it.get('pattern') if isinstance(it, dict) else it, '|', it.get('note', '') if isinstance(it, dict) else '')
            return 0
        if sub in ('add', 'rm') and len(rest) >= 2:
            pat = rest[1]
            items = [i for i in items if (i.get('pattern') if isinstance(i, dict) else i) != pat]
            if sub == 'add':
                items.append({'pattern': pat, 'note': opt(rest, '--note', '')})
            save_allow(items); print('ALLOW', sub, pat, '共', len(items), '条'); return 0
        usage(); return 1
    if cmd == 'wake':
        items = []
        for _ in range(3):
            it = INPUT(); it.type = 0; it.u.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MOVE, 0, 0)
            items.append(it)
        send_inputs(items); time.sleep(0.4); print('WAKED'); return 0
    if cmd == 'shot':
        from PIL import ImageGrab
        out = opt(rest, '--out') or os.path.join(HERE, '..', 'logs', 'screen.png')
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        im = ImageGrab.grab(all_screens=True); im.save(out)
        print('SHOT', os.path.abspath(out), im.size, 'virtual', vrect()); return 0
    if cmd == 'observe':
        from PIL import ImageGrab
        p = wintypes.POINT(); u32.GetCursorPos(ctypes.byref(p))
        lk = is_locked()
        print('DESKTOP', 'LOCKED' if lk else 'UNLOCKED', 'size', vrect(), 'cursor', (p.x, p.y))
        print('WHITELIST', [a.get('pattern') if isinstance(a, dict) else a for a in load_allow()] or 'empty')
        print('--- windows (hwnd | pid | rect | title) ---')
        for hwnd, title, pid, rect in list_windows(): print(hwnd, '|', pid, '|', rect, '|', title)
        out = opt(rest, '--out') or os.path.join(HERE, '..', 'logs', 'screen.png')
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        im = ImageGrab.grab(all_screens=True); im.save(out)
        print('SHOT', os.path.abspath(out), im.size)
        return 3 if lk else 0
    if cmd == 'move':
        u32.SetCursorPos(int(rest[0]), int(rest[1])); print('MOVED', rest[0], rest[1]); return 0
    if cmd == 'click':
        t = target_of(rest)
        if t == 'ERR': return 5
        nums = [a for a in rest if a.lstrip('-').isdigit()]
        if t:
            hwnd, title, rect = t
            if load_allow() and not opt(rest, '--window'): return 5
            if len(nums) >= 2: x, y = int(nums[0]), int(nums[1])
            else: x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            if not (rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]):
                print('ABORT coord-outside-window', (x, y), 'not in', rect); return 5
            rc = focus_window(title)
            if rc: return rc
        else:
            if load_allow():
                print('ABORT whitelist-active: 必须带 --window 指明目标窗口'); return 5
            if len(nums) >= 2: x, y = int(nums[0]), int(nums[1])
            else:
                p = wintypes.POINT(); u32.GetCursorPos(ctypes.byref(p)); x, y = p.x, p.y
        rc = guard(rest, 'click')
        if rc: return rc
        do('点击 %d,%d' % (x, y), click_at, x, y, opt(rest, '--button', 'left'), int(opt(rest, '--count', '1')))
        print('CLICK', x, y); return 0
    if cmd == 'drag':
        rc = guard(rest, 'drag')
        if rc: return rc
        nums = [a for a in rest if a.lstrip('-').isdigit()]
        do('拖拽 %s->%s' % (nums[:2], nums[2:4]), drag, int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3]), int(opt(rest, '--ms', '400')))
        print('DRAG', nums[:4]); return 0
    if cmd == 'wheel':
        rc = guard(rest, 'wheel')
        if rc: return rc
        nums = [a for a in rest if a.lstrip('-').isdigit()]
        if len(nums) >= 3: u32.SetCursorPos(int(nums[1]), int(nums[2]))
        do('滚轮', mouse, MOUSEEVENTF_WHEEL, 0, 0, int(nums[0])); print('WHEEL', nums[0]); return 0
    if cmd == 'type' or cmd == 'key':
        t = target_of(rest)
        if t == 'ERR': return 5
        if t:
            rc = focus_window(t[1])
            if rc: return rc
        else:
            print('ABORT need-window: type/key 必须带 --window 指明目标窗口'); return 5
        rc = guard(rest, cmd)
        if rc: return rc
        if cmd == 'type':
            text = opt(rest, '--text') or (rest[0] if rest and not rest[0].startswith('--') else '')
            do('输入 %d 字' % len(text), type_text, text); print('TYPED', len(text)); return 0
        combo = rest[0] if rest else ''
        if not combo: usage(); return 1
        do('按键 ' + combo, press, combo); print('KEY', combo); return 0
    usage(); return 1

if __name__ == '__main__':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
    sys.exit(main(sys.argv[1:]))