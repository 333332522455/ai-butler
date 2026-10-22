#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2 事件总线 + 规则初筛（纯标准库）。

三类事件：file（监听目录新增/修改）、service（端口掉线 / pythonw 进程数下降）、
schedule（schedule.json 里 active 任务的 scheduledAt 到点）。

硬规矩：绝不写 schedule.json，绝不改其它服务配置，只读外部状态。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))

DEFAULT_BASE = os.environ.get("AI_BUTLER_HOME") or r"D:\ai-butler"
DEFAULT_WATCH_DIRS = [r"D:\dsh-home\attachments", r"D:\sleep\大一信息台", r"D:\sleep\jiuhu"]
DEFAULT_PORTS = [43120, 43124, 8090]
DEFAULT_SCHEDULE_PATH = r"D:\dsh-home\storages\schedule.json"

EXT_WHITELIST = {
    ".md", ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg",
    ".jpeg", ".webp", ".zip", ".py", ".json", ".c", ".h", ".cpp", ".csv",
}
IGNORE_DIRS = {"__pycache__", ".git"}
IGNORE_SUFFIXES = (".tmp", ".part", ".crdownload")
MIN_SIZE = 100 * 1024
MERGE_WINDOW_SECONDS = 300
PORT_FLAP_SECONDS = 30
NIGHT_START = (23, 30)
NIGHT_END = (7, 0)
DEFAULT_INTERVAL = 15


def now_iso(dt):
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def parse_iso(text):
    if not text:
        return None
    s = str(text).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def parse_now(text):
    if not text:
        return None
    return parse_iso(text)


def is_night(dt):
    local = dt.astimezone(TZ)
    hm = (local.hour, local.minute)
    return hm >= NIGHT_START or hm < NIGHT_END


def is_ignored_name(name):
    if name.startswith(".") or name.startswith("~$"):
        return True
    lower = name.lower()
    return lower.endswith(IGNORE_SUFFIXES)


def probe_port(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def count_pythonw():
    """返回 pythonw 进程数；tasklist 不可用时返回 0。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags, timeout=10,
        )
        text = out.stdout.decode("gbk", "replace")
    except Exception:
        return 0
    n = 0
    for line in text.splitlines():
        line = line.strip().strip('"')
        if line.lower().startswith("pythonw.exe"):
            n += 1
    return n


def detect_service(ports):
    """可替换的 service 探测：验证脚本会 monkeypatch 这个函数。"""
    return {
        "ports": {str(p): probe_port(p) for p in ports},
        "pythonw": count_pythonw(),
    }


class EventBus(object):
    def __init__(self, base=None, now=None, dry_run=False, config=None):
        self.base = Path(base or DEFAULT_BASE)
        self.log_dir = self.base / "logs"
        self.events_path = self.log_dir / "events.jsonl"
        self.state_path = self.log_dir / "event-bus-state.json"
        self.dry_run = bool(dry_run)
        self.now = now or datetime.now(TZ)
        self.config = self._load_config(config)
        dirs = self.config.get("watch_dirs")
        self.watch_dirs = [str(p) for p in dirs] if isinstance(dirs, list) else list(DEFAULT_WATCH_DIRS)
        self.ports = list(self.config.get("ports") if self.config.get("ports") is not None else DEFAULT_PORTS)
        self.schedule_path = str(self.config.get("schedule_path") or DEFAULT_SCHEDULE_PATH)
        self.state = self._load_state()
        self.emitted = 0

    # ---------- config / state ----------

    def _load_config(self, config):
        if config is not None:
            return dict(config)
        path = self.base / "config.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print("config 读取失败: %s" % exc, file=sys.stderr)
            return {}
        if not isinstance(data, dict):
            return {}
        if "watch_dirs" not in data:
            for alt in ("watchDirs", "file_dirs", "fileDirs"):
                if alt in data:
                    data["watch_dirs"] = data[alt]
                    break
        return data

    def _empty_state(self):
        return {
            "version": 1,
            "last_scan": None,
            "file_scan": None,
            "ports": {},
            "pythonw_baseline": None,
            "schedule_seen": [],
            "merge_index": {},
            "stats": {"total": 0, "drop_reasons": {}},
        }

    def _load_state(self):
        state = self._empty_state()
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    state.update(data)
            except Exception as exc:
                print("state 读取失败，按空状态继续: %s" % exc, file=sys.stderr)
        stats = state.get("stats")
        if not isinstance(stats, dict):
            state["stats"] = {"total": 0, "drop_reasons": {}}
        state["stats"].setdefault("total", 0)
        state["stats"].setdefault("drop_reasons", {})
        return state

    def save_state(self, end=None):
        if self.dry_run:
            return
        if end is not None:
            self.state["last_scan"] = now_iso(end)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(self.state_path))

    # ---------- emit ----------

    def submit(self, now, kind, target, priority, reason=None):
        ev_id = "ev-" + uuid.uuid4().hex[:8]
        verdict = "drop" if reason else "queue"
        rec = {
            "at": now_iso(now),
            "id": ev_id,
            "kind": kind,
            "target": target,
            "priority": priority,
            "verdict": verdict,
            "reason": reason,
        }
        if verdict == "queue":
            key = kind + "|" + target
            merge_index = self.state.get("merge_index")
            if not isinstance(merge_index, dict):
                merge_index = {}
                self.state["merge_index"] = merge_index
            prev = merge_index.get(key)
            merged = False
            if isinstance(prev, dict):
                prev_at = parse_iso(prev.get("at"))
                if prev_at is not None:
                    delta = (now - prev_at).total_seconds()
                    if -60 <= delta < MERGE_WINDOW_SECONDS:
                        rec["verdict"] = "drop"
                        rec["reason"] = "merge"
                        rec["merged_to"] = prev.get("id")
                        merged = True
            if not merged:
                merge_index[key] = {"id": ev_id, "at": now_iso(now)}
        self._write(rec)
        return rec

    def _write(self, rec):
        if self.dry_run:
            print(json.dumps(rec, ensure_ascii=False))
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        stats = self.state["stats"]
        stats["total"] = int(stats.get("total", 0)) + 1
        if rec["verdict"] == "drop":
            reasons = stats["drop_reasons"]
            reasons[rec["reason"]] = int(reasons.get(rec["reason"], 0)) + 1
        self.emitted += 1

    # ---------- file ----------

    def _iter_files(self, root):
        if not os.path.isdir(root):
            return
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
            for name in filenames:
                if is_ignored_name(name):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                yield os.path.abspath(full), int(st.st_size), float(st.st_mtime)

    def scan_files(self, now):
        snapshot = {}
        for root in self.watch_dirs:
            for full, size, mtime in self._iter_files(root):
                snapshot[full] = {"size": size, "mtime": mtime}
        previous = self.state.get("file_scan")
        if not isinstance(previous, dict):
            self.state["file_scan"] = snapshot
            if not self.dry_run:
                print("file: 首次运行只建基线，共 %d 个文件（不产事件）" % len(snapshot))
            return
        count = 0
        for full in sorted(snapshot):
            cur = snapshot[full]
            old = previous.get(full)
            if old is None:
                changed = True
            else:
                changed = int(old.get("size", -1)) != cur["size"] or abs(float(old.get("mtime", 0.0)) - cur["mtime"]) > 1e-6
            if not changed:
                continue
            reason = self._classify_file(full, cur["size"])
            self.submit(now, "file", full, "P1", reason)
            count += 1
        self.state["file_scan"] = snapshot
        if count:
            print("file: 本轮 %d 个变化" % count)

    @staticmethod
    def _classify_file(full, size):
        ext = os.path.splitext(os.path.basename(full))[1].lower()
        if ext not in EXT_WHITELIST:
            return "ext"
        if size < MIN_SIZE:
            return "small"
        return None

    # ---------- service ----------

    def scan_services(self, now):
        try:
            result = detect_service(list(self.ports))
        except Exception as exc:
            print("service 探测失败: %s" % exc, file=sys.stderr)
            return
        ports_state = self.state.get("ports")
        if not isinstance(ports_state, dict):
            ports_state = {}
            self.state["ports"] = ports_state
        port_up = result.get("ports") or {}
        for port in self.ports:
            key = str(port)
            up = bool(port_up.get(key, True))
            entry = ports_state.get(key)
            if not isinstance(entry, dict):
                entry = {"down_since": None, "reported": False}
            if up:
                if entry.get("down_since") or entry.get("reported"):
                    ports_state[key] = {"down_since": None, "reported": False}
                continue
            down_since = parse_iso(entry.get("down_since"))
            if down_since is None:
                ports_state[key] = {"down_since": now_iso(now), "reported": False}
                continue
            if entry.get("reported"):
                continue
            if (now - down_since).total_seconds() >= PORT_FLAP_SECONDS:
                self.submit(now, "service", "port:%s" % port, "P0", None)
                ports_state[key] = {"down_since": entry.get("down_since"), "reported": True}
            else:
                ports_state[key] = {"down_since": entry.get("down_since"), "reported": False}

        pythonw = result.get("pythonw")
        if isinstance(pythonw, int):
            baseline = self.state.get("pythonw_baseline")
            if not isinstance(baseline, int):
                self.state["pythonw_baseline"] = pythonw
            else:
                if pythonw > baseline:
                    self.state["pythonw_baseline"] = pythonw
                delta = baseline - pythonw
                if delta >= 2:
                    self.submit(now, "service", "pythonw", "P1", None)
                elif delta == 1:
                    self.submit(now, "service", "pythonw", "P1", "noise")

    # ---------- schedule ----------

    def _load_schedule(self):
        path = Path(self.schedule_path)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print("schedule 读取失败: %s" % exc, file=sys.stderr)
            return []
        tables = data.get("tables") if isinstance(data, dict) else None
        tasks = (tables or {}).get("tasks") if isinstance(tables, dict) else None
        items = []
        if isinstance(tasks, dict):
            for key, value in tasks.items():
                if not isinstance(value, dict):
                    continue
                rec = value.get("record") if isinstance(value.get("record"), dict) else value
                sid = rec.get("id") or key
                status = value.get("status") or rec.get("status") or "active"
                sat = rec.get("scheduledAt") or value.get("scheduledAt")
                items.append({"id": str(sid), "status": str(status), "scheduledAt": sat,
                              "title": rec.get("title") or ""})
        elif isinstance(tasks, list):
            for value in tasks:
                if not isinstance(value, dict):
                    continue
                rec = value.get("record") if isinstance(value.get("record"), dict) else value
                items.append({"id": str(rec.get("id") or value.get("id") or ""),
                              "status": str(value.get("status") or rec.get("status") or "active"),
                              "scheduledAt": rec.get("scheduledAt") or value.get("scheduledAt"),
                              "title": rec.get("title") or ""})
        return items

    def scan_schedule(self, now):
        seen = self.state.get("schedule_seen")
        if not isinstance(seen, list):
            seen = []
            self.state["schedule_seen"] = seen
        seen_set = set(str(x) for x in seen)
        last = parse_iso(self.state.get("last_scan"))
        window_start = last if last is not None else now - timedelta(hours=24)
        count = 0
        for item in self._load_schedule():
            if item["status"] != "active":
                continue
            if not item["scheduledAt"] or item["id"] in seen_set:
                continue
            try:
                sat = parse_iso(item["scheduledAt"])
            except Exception:
                continue
            if sat is None:
                continue
            if not (window_start < sat <= now):
                continue
            seen_set.add(item["id"])
            seen.append(item["id"])
            if is_night(now):
                self.submit(now, "schedule", "task:%s" % item["id"], "P2", "night")
            else:
                self.submit(now, "schedule", "task:%s" % item["id"], "P2", None)
            count += 1
        if count:
            print("schedule: 本轮 %d 条到点" % count)

    # ---------- run ----------

    def run_once(self, now=None):
        current = now or self.now
        self.scan_files(current)
        self.scan_services(current)
        self.scan_schedule(current)
        self.save_state(end=current)
        return self.emitted

    def run_loop(self, interval=DEFAULT_INTERVAL):
        print("event-bus 常驻，间隔 %ss，base=%s" % (interval, self.base))
        while True:
            try:
                self.run_once(datetime.now(TZ))
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print("本轮异常: %s" % exc, file=sys.stderr)
            time.sleep(interval)


def print_report(base=None, now=None):
    bus = EventBus(base=base, now=now)
    current = bus.now.astimezone(TZ)
    today = current.date().isoformat()
    total = 0
    by_kind = {}
    queued = 0
    dropped = 0
    reasons = {}
    if bus.events_path.exists():
        with bus.events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                at = parse_iso(rec.get("at"))
                if at is None or at.astimezone(TZ).date().isoformat() != today:
                    continue
                total += 1
                kind = rec.get("kind") or "?"
                by_kind[kind] = by_kind.get(kind, 0) + 1
                if rec.get("verdict") == "queue":
                    queued += 1
                else:
                    dropped += 1
                    reason = rec.get("reason") or "?"
                    reasons[reason] = reasons.get(reason, 0) + 1
    print("today: %s (Asia/Shanghai)" % today)
    print("events total: %d" % total)
    for kind in sorted(by_kind):
        print("  %s: %d" % (kind, by_kind[kind]))
    print("queued: %d" % queued)
    print("dropped: %d" % dropped)
    print("  drop reasons:")
    for reason in sorted(reasons):
        print("    %s: %d" % (reason, reasons[reason]))
    if bus.state.get("stats"):
        stats = bus.state["stats"]
        print("state totals: total=%s drop_reasons=%s" % (
            stats.get("total"), json.dumps(stats.get("drop_reasons") or {}, ensure_ascii=False)))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="M2 事件总线 + 规则初筛")
    parser.add_argument("--once", action="store_true", help="只扫一轮")
    parser.add_argument("--loop", action="store_true", help="常驻循环")
    parser.add_argument("--report", action="store_true", help="打印今天统计")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    parser.add_argument("--now", default=None, help="注入当前时间 ISO")
    parser.add_argument("--base", default=None, help="数据根目录")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="循环间隔秒")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    now = parse_now(args.now)
    if args.report:
        return print_report(base=args.base, now=now)
    if args.loop:
        EventBus(base=args.base, now=now, dry_run=args.dry_run).run_loop(args.interval)
        return 0
    bus = EventBus(base=args.base, now=now, dry_run=args.dry_run)
    bus.run_once(now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
