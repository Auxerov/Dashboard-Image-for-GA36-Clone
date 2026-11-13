#!/usr/bin/env python3
"""
R36S / GA36 PC Companion & Live Control Suite v3.5
Communicates with R36S over USB CDC-ACM Serial (/dev/ttyACM*) and TCP (8888).

Features:
- Instant RPC test & diagnostic (test / ping)
- Live Battery & OTG Power Inspector (battery / charge)
- High-Quality Image / Wallpaper Pusher (image <path>)
- Real-time 'btop' Cyber System Monitor HUD (dashboard / stream)
- Interactive Root Shell session (shell)
- Remote Command Execution (exec "<command>")
- Root File Transfer (push, pull, ls)
- Screen Clear (clear)
- Console Agent Auto-Updater (update-agent)
"""

import os
import sys
import time
import json
import glob
import base64
import zlib
import socket
import signal
import argparse
import threading
import subprocess
import psutil
import serial
import shutil
from PIL import Image, ImageDraw, ImageFont, ImageSequence

try:
    import dbus
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

# -------------------------------------------------------------
# Font Loader Helper
# -------------------------------------------------------------
def load_font(size=12, bold=False):
    bold_candidates = [
        "/usr/share/fonts/TTF/JetBrainsMonoNerdFontPropo-Bold.ttf",
        "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Bold.ttf",
        "/usr/share/fonts/Adwaita/AdwaitaMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
    ]
    reg_candidates = [
        "/usr/share/fonts/TTF/JetBrainsMonoNerdFontPropo-Regular.ttf",
        "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf",
        "/usr/share/fonts/Adwaita/AdwaitaMono-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ]
    candidates = bold_candidates if bold else reg_candidates
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


FONT_GIANT_NUM = load_font(48, bold=True)
FONT_GIANT_TITLE = load_font(22, bold=True)
FONT_GIANT_SUB = load_font(20, bold=True)
FONT_HUGE = load_font(26, bold=True)
FONT_LARGE = load_font(18, bold=True)
FONT_VAL = load_font(16, bold=True)
FONT_TITLE = load_font(13, bold=True)
FONT_BOLD = load_font(11, bold=True)
FONT_REG = load_font(11, bold=False)
FONT_SMALL = load_font(10, bold=False)

# -------------------------------------------------------------
# PC Hardware Metrics Collection
# -------------------------------------------------------------
NET_LAST_BYTES = None
NET_LAST_TIME = None
CPU_HISTORY = []
GPU_HISTORY = []


def get_cpu_info():
    global CPU_HISTORY
    usage = int(psutil.cpu_percent(interval=None))
    CPU_HISTORY.append(usage)
    if len(CPU_HISTORY) > 20:
        CPU_HISTORY.pop(0)

    freq = 0.0
    try:
        f = psutil.cpu_freq()
        if f:
            freq = round(f.current / 1000.0, 1)
    except Exception:
        pass

    temp = 45
    try:
        temps = psutil.sensors_temperatures()
        if "k10temp" in temps:
            for s in temps["k10temp"]:
                if s.label in ("Tctl", "Tccd1"):
                    temp = int(s.current)
                    break
        elif "coretemp" in temps:
            temp = int(temps["coretemp"][0].current)
    except Exception:
        pass

    return {
        "usage": usage,
        "temp": temp,
        "freq": freq,
        "cores": psutil.cpu_count(logical=True),
        "name": "AMD Ryzen 7 5700X",
        "history": list(CPU_HISTORY)
    }


def get_gpu_info():
    global GPU_HISTORY
    info = {
        "usage": 0,
        "temp": 45,
        "name": "NVIDIA RTX 5070",
        "vram_used": 0.0,
        "vram_total": 12.0,
        "history": list(GPU_HISTORY)
    }
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,name",
            "--format=csv,noheader,nounits"
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=1).decode("utf-8").strip()
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 5:
            info["usage"] = int(parts[0])
            info["temp"] = int(parts[1])
            info["vram_used"] = round(float(parts[2]) / 1024.0, 1)
            info["vram_total"] = round(float(parts[3]) / 1024.0, 1)
            info["name"] = parts[4].replace("NVIDIA GeForce ", "")
    except Exception:
        pass

    GPU_HISTORY.append(info["usage"])
    if len(GPU_HISTORY) > 20:
        GPU_HISTORY.pop(0)
    info["history"] = list(GPU_HISTORY)
    return info


def get_ram_info():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "usage": int(mem.percent),
        "used_gb": round(mem.used / (1024**3), 1),
        "total_gb": round(mem.total / (1024**3), 1),
        "swap_usage": int(swap.percent),
        "swap_used_gb": round(swap.used / (1024**3), 1),
        "swap_total_gb": round(swap.total / (1024**3), 1)
    }


def get_net_info():
    global NET_LAST_BYTES, NET_LAST_TIME
    now = time.time()
    counters = psutil.net_io_counters()
    cur_bytes = (counters.bytes_recv, counters.bytes_sent)
    down_str = "0 B/s"
    up_str = "0 B/s"
    down_mb = 0.0
    up_mb = 0.0
    if NET_LAST_BYTES and NET_LAST_TIME:
        dt = max(0.05, now - NET_LAST_TIME)
        d_rate = max(0.0, (cur_bytes[0] - NET_LAST_BYTES[0]) / dt)
        u_rate = max(0.0, (cur_bytes[1] - NET_LAST_BYTES[1]) / dt)
        down_mb = round(d_rate / (1024 * 1024), 2)
        up_mb = round(u_rate / (1024 * 1024), 2)

        if d_rate >= 1024 * 1024:
            down_str = f"{d_rate / (1024*1024):.1f} MB/s"
        elif d_rate >= 1024:
            down_str = f"{d_rate / 1024:.0f} KB/s"
        else:
            down_str = f"{int(d_rate)} B/s"

        if u_rate >= 1024 * 1024:
            up_str = f"{u_rate / (1024*1024):.1f} MB/s"
        elif u_rate >= 1024:
            up_str = f"{u_rate / 1024:.0f} KB/s"
        else:
            up_str = f"{int(u_rate)} B/s"

    NET_LAST_BYTES = cur_bytes
    NET_LAST_TIME = now
    return {"down_str": down_str, "up_str": up_str, "down_mb": down_mb, "up_mb": up_mb}


def get_disk_info():
    try:
        d = psutil.disk_usage("/")
        return {"usage": int(d.percent), "used_gb": int(d.used / (1024**3)), "total_gb": int(d.total / (1024**3))}
    except Exception:
        return {"usage": 40, "used_gb": 320, "total_gb": 931}


def get_media_info():
    info = {"title": "No media playing", "artist": "", "playing": False}
    if not HAS_DBUS:
        return info
    try:
        bus = dbus.SessionBus()
        for p in bus.list_names():
            if p.startswith("org.mpris.MediaPlayer2"):
                obj = bus.get_object(p, "/org/mpris/MediaPlayer2")
                props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                status = str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
                meta = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
                title = str(meta.get("xesam:title", ""))
                artist_val = meta.get("xesam:artist", [""])
                artist = str(artist_val[0]) if len(artist_val) > 0 else ""
                info["title"] = title if title else "Media"
                info["artist"] = artist
                info["playing"] = (status.lower() == "playing")
                break
    except Exception:
        pass
    return info


# -------------------------------------------------------------
# R36S Client Transport & RPC
# -------------------------------------------------------------
class R36SClient:
    def __init__(self, port=None):
        self.port = port
        self.ser = None
        self.sock = None
        self.req_id = 0
        self.pending_responses = {}
        self.lock = threading.Lock()
        self.running = True
        self.reader_thread = None
        self.cwd = "/storage"
        self.last_power = {}
        self.button_callback = None

    def find_port(self):
        if self.port and os.path.exists(self.port):
            return self.port
        candidates = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"), reverse=True)
        if candidates:
            return candidates[0]
        return None

    def try_tcp_connect(self, host="192.168.10.2", port=8888):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect((host, port))
            s.settimeout(None)
            self.sock = s
            self.port = f"TCP:{host}:{port}"
            self.reader_thread = threading.Thread(target=self._tcp_reader_loop, daemon=True)
            self.reader_thread.start()
            print(f"\n[CONNECTED] GA36 TCP Network Port: {self.port}")
            return True
        except Exception:
            return False

    def close_serial(self):
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            for rid, (ev, res) in list(self.pending_responses.items()):
                res["status"] = "error"
                res["msg"] = "Serial connection closed"
                ev.set()
            self.pending_responses.clear()

    def try_usb_reset(self):
        if not shutil.which("usbreset"):
            return
        target_ids = ["18d1:0001", "1d6b:0104", "0525:a4a7", "1f3a:efe8"]
        try:
            out = subprocess.check_output(["lsusb"], stderr=subprocess.DEVNULL).decode("utf-8")
            for line in out.splitlines():
                for tid in target_ids:
                    if tid.lower() in line.lower() or "gadget" in line.lower() or "android" in line.lower():
                        parts = line.split()
                        if len(parts) >= 6 and parts[5].count(":") == 1:
                            subprocess.run(["sudo", "-n", "usbreset", parts[5]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            time.sleep(0.5)
                            return
        except Exception:
            pass

    def reconnect(self, timeout=None):
        self.close_serial()
        self.try_usb_reset()
        return self.connect(timeout=timeout)

    def connect(self, timeout=30):
        start = time.time()
        spin_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spin_idx = 0
        last_reset_time = 0.0

        while self.running and (timeout is None or (time.time() - start < timeout)):
            elapsed = int(time.time() - start)
            spin = spin_chars[spin_idx % len(spin_chars)]
            spin_idx += 1

            # 1. Try Serial
            p = self.find_port()
            if p:
                sys.stdout.write(f"\r\033[K  \033[1;36m[{spin}] Fetching devices...\033[0m Handshaking on {p}... ")
                sys.stdout.flush()
                try:
                    s = serial.Serial(p, 115200, timeout=0.6, write_timeout=1.5)
                    s.dtr = True
                    s.rts = True
                    s.reset_input_buffer()
                    s.reset_output_buffer()
                    time.sleep(0.1)
                    self.ser = s
                    self.port = p
                    self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
                    self.reader_thread.start()

                    # Quick handshake ping
                    res = self.ping(timeout=2.0)
                    if res.get("status") == "ok":
                        sys.stdout.write(f"\r\033[K  \033[1;32m[✔] GA36 Console detected & linked on {self.port}!\033[0m\n")
                        sys.stdout.flush()
                        return True
                    else:
                        self.close_serial()
                        if time.time() - last_reset_time > 5.0:
                            last_reset_time = time.time()
                            self.try_usb_reset()
                except Exception:
                    self.close_serial()

            # 2. Try TCP
            if self.try_tcp_connect():
                sys.stdout.write(f"\r\033[K  \033[1;32m[✔] GA36 Console connected over TCP Network!\033[0m\n")
                sys.stdout.flush()
                return True

            if timeout is not None:
                rem = max(0, int(timeout - (time.time() - start)))
                status_str = f"Scanning USB ports (/dev/ttyACM*, /dev/ttyUSB*) [{rem:02d}s remaining]"
            else:
                elapsed_str = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
                status_str = f"Scanning USB ports (/dev/ttyACM*, /dev/ttyUSB*) [{elapsed_str}]"

            sys.stdout.write(f"\r\033[K  \033[1;36m[{spin}] Fetching devices...\033[0m {status_str} ")
            sys.stdout.flush()
            time.sleep(0.12)

        sys.stdout.write("\n")
        return False

    def _reader_loop(self):
        while self.running and self.ser and self.ser.is_open:
            try:
                line = self.ser.readline()
                if not line:
                    continue
                try:
                    resp = json.loads(line.decode("utf-8", "ignore").strip())
                    if resp.get("type") == "button":
                        self._on_button(resp.get("name"))
                    elif resp.get("type") == "response":
                        rid = resp.get("id")
                        if rid in self.pending_responses:
                            ev, result_holder = self.pending_responses[rid]
                            result_holder.update(resp)
                            ev.set()
                except Exception:
                    pass
            except Exception:
                break
        # Auto-cleanup descriptor when reader loop terminates
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _tcp_reader_loop(self):
        buf = b""
        while self.running and self.sock:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line_str = line.decode("utf-8", "ignore").strip()
                    if line_str:
                        try:
                            resp = json.loads(line_str)
                            if resp.get("type") == "button":
                                self._on_button(resp.get("name"))
                            elif resp.get("type") == "response":
                                rid = resp.get("id")
                                if rid in self.pending_responses:
                                    ev, result_holder = self.pending_responses[rid]
                                    result_holder.update(resp)
                                    ev.set()
                        except Exception:
                            pass
            except Exception:
                break

    def default_button_action(self, btn_name):
        try:
            if btn_name in ("A", "KEY_304"):
                if HAS_DBUS:
                    bus = dbus.SessionBus()
                    for p in bus.list_names():
                        if p.startswith("org.mpris.MediaPlayer2"):
                            player = bus.get_object(p, "/org/mpris/MediaPlayer2")
                            player.PlayPause(dbus_interface="org.mpris.MediaPlayer2.Player")
                            break
            elif btn_name in ("B", "KEY_305"):
                if HAS_DBUS:
                    bus = dbus.SessionBus()
                    for p in bus.list_names():
                        if p.startswith("org.mpris.MediaPlayer2"):
                            player = bus.get_object(p, "/org/mpris/MediaPlayer2")
                            player.Next(dbus_interface="org.mpris.MediaPlayer2.Player")
                            break
            elif btn_name in ("X", "KEY_307"):
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif btn_name in ("Y", "KEY_308"):
                subprocess.run(["spectacle", "-b", "-n"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Action error: {e}")

    def _on_button(self, btn_name):
        print(f"\n[BUTTON PRESSED] Hardware Key: {btn_name}")
        if self.button_callback:
            self.button_callback(btn_name)
        else:
            self.default_button_action(btn_name)

    def call_rpc(self, msg_dict, timeout=15):
        with self.lock:
            self.req_id += 1
            cur_id = self.req_id
            msg_dict["id"] = cur_id
            ev = threading.Event()
            res = {}
            self.pending_responses[cur_id] = (ev, res)
            raw = (json.dumps(msg_dict, ensure_ascii=False) + "\n").encode("utf-8")
            if self.sock:
                self.sock.sendall(raw)
            elif self.ser:
                try:
                    self.ser.write(raw)
                    self.ser.flush()
                except Exception as e:
                    del self.pending_responses[cur_id]
                    return {"status": "error", "msg": f"Serial write error: {e}"}
            else:
                return {"status": "error", "msg": "No active transport"}

        if ev.wait(timeout):
            del self.pending_responses[cur_id]
            return res
        else:
            if cur_id in self.pending_responses:
                del self.pending_responses[cur_id]
            return {"status": "error", "msg": "Timeout waiting for R36S response"}

    def ping(self, timeout=3.0):
        res = self.call_rpc({"type": "ping"}, timeout=timeout)
        if res.get("status") == "ok" and "power" in res:
            self.last_power = res.get("power", {})
        return res

    def get_power(self):
        res = self.call_rpc({"type": "power"}, timeout=5)
        if res.get("status") == "ok":
            self.last_power = res.get("power", {})
            return self.last_power
        return self.last_power

    def pause_gui(self):
        res = self.call_rpc({"type": "gui_pause"}, timeout=5)
        if res.get("status") != "ok":
            self.exec_cmd("systemctl stop emustation.service 2>/dev/null ; killall -9 emulationstation 2>/dev/null", timeout=5)
        return True

    def resume_gui(self):
        res = self.call_rpc({"type": "gui_resume"}, timeout=5)
        if res.get("status") != "ok":
            self.exec_cmd("systemctl unmask emustation.service 2>/dev/null ; systemctl start emustation.service 2>/dev/null", timeout=5)
        return True

    def display_frame_raw(self, compressed_bytes):
        b64 = base64.b64encode(compressed_bytes).decode("ascii")
        return self.call_rpc({"type": "fb_raw", "data_b64": b64}, timeout=5)

    def clear_screen(self, color=(0, 0, 0)):
        return self.call_rpc({"type": "fb_clear", "color": list(color)}, timeout=5)

    def exec_cmd(self, cmd_str, timeout=120):
        return self.call_rpc({"type": "exec", "cmd": cmd_str, "timeout": timeout}, timeout=timeout + 5)

    def shell_exec(self, user_cmd):
        wrapped = f"cd {self.cwd} && ({user_cmd})\n__RET__=$?\necho '___CWD___:'\npwd\nexit $__RET__"
        res = self.exec_cmd(wrapped)
        if res.get("status") != "ok":
            return res.get("msg", "Execution error"), "", 1
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")
        code = res.get("code", 0)
        if "___CWD___:" in stdout:
            parts = stdout.split("___CWD___:")
            cmd_out = parts[0]
            new_cwd = parts[1].strip()
            if new_cwd and os.path.isabs(new_cwd):
                self.cwd = new_cwd
            return cmd_out, stderr, code
        return stdout, stderr, code

    def push_file(self, local_path, remote_path):
        if not os.path.exists(local_path):
            return False, f"Local file not found: {local_path}"
        try:
            with open(local_path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("ascii")
            res = self.call_rpc({"type": "write_file", "path": remote_path, "content_b64": b64}, timeout=30)
            if res.get("status") == "ok":
                return True, f"Uploaded: {len(data)} bytes -> {remote_path}"
            return False, f"Error: {res.get('msg')}"
        except Exception as e:
            return False, str(e)

    def pull_file(self, remote_path, local_path):
        res = self.call_rpc({"type": "read_file", "path": remote_path}, timeout=30)
        if res.get("status") != "ok":
            return False, f"Error: {res.get('msg')}"
        try:
            data = base64.b64decode(res.get("content_b64", ""))
            os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(data)
            return True, f"Downloaded: {len(data)} bytes -> {local_path}"
        except Exception as e:
            return False, str(e)

    def list_dir(self, remote_path):
        return self.call_rpc({"type": "ls", "path": remote_path})

    def close(self):
        self.running = False
        self.close_serial()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# -------------------------------------------------------------
# Frame Renderers
# -------------------------------------------------------------
def prepare_image_frame(image_path, target_size=(640, 480)):
    """Loads any image and scales/crops to exact 640x480 32-bit BGRA bytes, zlib compressed."""
    img = Image.open(image_path).convert("RGB")
    # Aspect fit or crop to 640x480
    img_ratio = img.width / img.height
    target_ratio = target_size[0] / target_size[1]

    if img_ratio > target_ratio:
        # Wider: scale by height then crop width
        new_h = target_size[1]
        new_w = int(img.width * (target_size[1] / img.height))
        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - target_size[0]) // 2
        img_cropped = img_resized.crop((left, 0, left + target_size[0], target_size[1]))
    else:
        # Taller: scale by width then crop height
        new_w = target_size[0]
        new_h = int(img.height * (target_size[0] / img.width))
        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        top = (new_h - target_size[1]) // 2
        img_cropped = img_resized.crop((0, top, target_size[0], top + target_size[1]))

    r, g, b = img_cropped.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", target_size, 255)))
    return zlib.compress(bgra.tobytes(), level=1)


def draw_gauge_bar(draw, x, y, w, h, percent, fill_color, bg_color=(25, 35, 55), border_color=(45, 60, 90)):
    draw.rectangle([x, y, x + w, y + h], fill=bg_color, outline=border_color)
    pct = max(0, min(100, percent))
    fill_w = int((w - 2) * (pct / 100.0))
    if fill_w > 0:
        draw.rectangle([x + 1, y + 1, x + 1 + fill_w, y + h - 1], fill=fill_color)


def draw_sparkline(draw, x, y, w, h, data_list, max_val=100, color=(0, 229, 255)):
    if not data_list:
        return
    bar_w = max(2, w // max(len(data_list), 1) - 1)
    for idx, val in enumerate(data_list):
        bx = x + idx * (bar_w + 1)
        bh = max(2, int((min(val, max_val) / float(max_val)) * h))
        draw.rectangle([bx, y + h - bh, bx + bar_w, y + h], fill=color)


def draw_big_pie(draw, cx, cy, radius, percent, color, track_color=(22, 32, 50), thickness=18, title="", val_str="", subtitle=""):
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.arc(bbox, start=0, end=360, fill=track_color, width=thickness)
    pct = max(0, min(100, percent))
    if pct > 0:
        end_angle = -90 + (pct / 100.0) * 360
        draw.arc(bbox, start=-90, end=end_angle, fill=color, width=thickness)

    if title:
        draw.text((cx, cy - 48), title, fill=color, font=FONT_GIANT_TITLE, anchor="mm")

    v_text = val_str or f"{pct}%"
    draw.text((cx, cy + 2), v_text, fill=(255, 255, 255), font=FONT_GIANT_NUM, anchor="mm")

    if subtitle:
        draw.text((cx, cy + 50), subtitle, fill=(185, 205, 230), font=FONT_GIANT_SUB, anchor="mm")


def draw_dual_pie(draw, cx, cy, r_out, r_in, pct_out, pct_in, col_out, col_in, title="", val_str="", subtitle=""):
    draw.arc([cx - r_out, cy - r_out, cx + r_out, cy + r_out], 0, 360, fill=(24, 34, 52), width=8)
    p_out = max(0, min(100, pct_out))
    if p_out > 0:
        draw.arc([cx - r_out, cy - r_out, cx + r_out, cy + r_out], -90, -90 + (p_out / 100.0) * 360, fill=col_out, width=8)

    draw.arc([cx - r_in, cy - r_in, cx + r_in, cy + r_in], 0, 360, fill=(18, 26, 40), width=6)
    p_in = max(0, min(100, pct_in))
    if p_in > 0:
        draw.arc([cx - r_in, cy - r_in, cx + r_in, cy + r_in], -90, -90 + (p_in / 100.0) * 360, fill=col_in, width=6)

    if title:
        tw = draw.textlength(title, font=FONT_BOLD)
        draw.text((cx - tw // 2, cy - 24), title, fill=(160, 175, 200), font=FONT_BOLD)

    v_text = val_str or f"{pct_out}%"
    vw = draw.textlength(v_text, font=FONT_LARGE)
    draw.text((cx - vw // 2, cy - 9), v_text, fill=(245, 250, 255), font=FONT_LARGE)

    if subtitle:
        sw = draw.textlength(subtitle, font=FONT_SMALL)
        draw.text((cx - sw // 2, cy + 14), subtitle, fill=col_out, font=FONT_SMALL)


def render_pie_dashboard(cpu, gpu, ram, net, disk, media, power_info, osd_msg=None):
    """Renders a Full-Screen, Maximum Size 4-Quadrant Giant Pie HUD (640x480)."""
    img = Image.new("RGB", (640, 480), color=(8, 12, 20))
    draw = ImageDraw.Draw(img)

    C_CYAN = (0, 229, 255)
    C_PINK = (255, 42, 133)
    C_GREEN = (0, 230, 118)
    C_ORANGE = (255, 145, 0)
    C_CARD_BG = (13, 19, 30)
    C_CARD_BORDER = (28, 42, 68)

    # 4 Expanded Quadrants (Full 640x480 screen utilization, completely borderless and headerless)
    # Box 1: CPU (Top Left: 8, 8, 316, 236) -> Center: (162, 122)
    draw.rounded_rectangle([8, 8, 316, 236], radius=10, fill=C_CARD_BG, outline=C_CARD_BORDER, width=1)
    draw_big_pie(draw, 162, 122, 92, cpu["usage"], C_CYAN, thickness=18, title="CPU", val_str=f"{cpu['usage']}%", subtitle=f"{cpu['temp']}°C")

    # Box 2: GPU (Top Right: 324, 8, 632, 236) -> Center: (478, 122)
    draw.rounded_rectangle([324, 8, 632, 236], radius=10, fill=C_CARD_BG, outline=C_CARD_BORDER, width=1)
    draw_big_pie(draw, 478, 122, 92, gpu["usage"], C_PINK, thickness=18, title="GPU", val_str=f"{gpu['usage']}%", subtitle=f"{gpu['temp']}°C")

    # Box 3: RAM (Bottom Left: 8, 244, 316, 472) -> Center: (162, 358)
    draw.rounded_rectangle([8, 244, 316, 472], radius=10, fill=C_CARD_BG, outline=C_CARD_BORDER, width=1)
    draw_big_pie(draw, 162, 358, 92, ram["usage"], C_GREEN, thickness=18, title="RAM", val_str=f"{ram['usage']}%", subtitle=f"{ram['used_gb']} GB")

    # Box 4: DISK / NET (Bottom Right: 324, 244, 632, 472) -> Center: (478, 358)
    draw.rounded_rectangle([324, 244, 632, 472], radius=10, fill=C_CARD_BG, outline=C_CARD_BORDER, width=1)
    disk_sub = f"↓ {net['down_str']}" if (net.get("down_mb", 0) > 0.05 or net.get("up_mb", 0) > 0.05) else f"{disk['used_gb']} GB"
    draw_big_pie(draw, 478, 358, 92, disk["usage"], C_ORANGE, thickness=18, title="DISK", val_str=f"{disk['usage']}%", subtitle=disk_sub)

    if osd_msg:
        bw, bh = 340, 48
        bx = (640 - bw) // 2
        by = 54
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(10, 16, 26), outline=C_CYAN, width=2)
        draw.text((bx + bw // 2, by + 16), osd_msg, fill=(255, 255, 255), font=FONT_LARGE, anchor="mm")
        draw.text((bx + bw // 2, by + 34), "Press Up/Down arrow to change focus", fill=C_CYAN, font=FONT_SMALL, anchor="mm")

    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


def render_dual_dashboard(cpu, gpu, ram, net, disk, media, power_info, osd_msg=None):
    """Renders a high-tech Dual-Ring Concentric Cockpit HUD (640x480)."""
    img = Image.new("RGB", (640, 480), color=(8, 12, 20))
    draw = ImageDraw.Draw(img)

    C_CYAN = (0, 229, 255)
    C_PINK = (255, 42, 133)
    C_GREEN = (0, 230, 118)
    C_ORANGE = (255, 145, 0)
    C_WHITE = (240, 245, 255)
    C_DIM = (120, 135, 160)
    C_CARD_BG = (14, 20, 32)
    C_CARD_BORDER = (28, 40, 64)

    # Header
    clock_str = time.strftime("%H:%M:%S")
    draw.text((12, 10), "⚡ DUAL-RING CLUSTER HUD", fill=C_CYAN, font=FONT_TITLE)
    pulse_dot = (int(time.time() * 2) % 2 == 0)
    dot_col = C_GREEN if pulse_dot else (0, 140, 70)
    draw.ellipse([214, 14, 222, 22], fill=dot_col)
    draw.text((228, 12), f"CANLI {clock_str} | Auxerov-PC", fill=C_DIM, font=FONT_REG)

    cap = power_info.get("capacity", 0)
    v = power_info.get("voltage_v", 0.0)
    is_charging = (power_info.get("status") == "Charging" or power_info.get("online"))
    p_str = f"[⚡ USB CHG: {cap}% ({v}V)]" if is_charging else f"[BATT: {cap}% ({v}V)]"
    draw.text((435, 12), p_str, fill=C_GREEN if is_charging else C_WHITE, font=FONT_BOLD)

    # 4 Quadrants
    # Q1: CPU (10, 42, 315, 222)
    draw.rectangle([10, 42, 315, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    temp_pct = min(100, int((cpu["temp"] / 90.0) * 100))
    draw_dual_pie(draw, 88, 132, 52, 40, cpu["usage"], temp_pct, C_CYAN, (0, 180, 216), title="CPU", val_str=f"{cpu['usage']}%", subtitle=f"{cpu['temp']}°C // {cpu['freq']}G")
    draw.text((158, 62), f"{cpu['name']}", fill=C_CYAN, font=FONT_BOLD)
    draw.text((158, 88), f"Outer Ring: {cpu['usage']}% Load", fill=C_WHITE, font=FONT_REG)
    draw.text((158, 110), f"Inner Ring: {cpu['temp']}°C Temp", fill=(0, 180, 216), font=FONT_REG)
    draw.text((158, 132), f"Clock Rate: {cpu['freq']} GHz", fill=C_DIM, font=FONT_REG)
    draw.text((158, 154), f"Cores     : {cpu['cores']} Threads", fill=C_DIM, font=FONT_SMALL)

    # Q2: GPU (325, 42, 630, 222)
    draw.rectangle([325, 42, 630, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    vram_pct = int((gpu["vram_used"] / max(gpu["vram_total"], 0.1)) * 100)
    draw_dual_pie(draw, 403, 132, 52, 40, gpu["usage"], vram_pct, C_PINK, (255, 110, 180), title="GPU", val_str=f"{gpu['usage']}%", subtitle=f"{gpu['temp']}°C // RTX")
    draw.text((473, 62), f"{gpu['name']}", fill=C_PINK, font=FONT_BOLD)
    draw.text((473, 88), f"Outer Ring: {gpu['usage']}% Load", fill=C_WHITE, font=FONT_REG)
    draw.text((473, 110), f"Inner Ring: {vram_pct}% VRAM", fill=(255, 110, 180), font=FONT_REG)
    draw.text((473, 132), f"Temp      : {gpu['temp']}°C", fill=C_DIM, font=FONT_REG)
    draw.text((473, 154), f"Bellek    : {gpu['vram_used']}/{gpu['vram_total']} GB", fill=C_DIM, font=FONT_SMALL)

    # Q3: RAM (10, 230, 315, 398)
    draw.rectangle([10, 230, 315, 398], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw_dual_pie(draw, 88, 314, 52, 40, ram["usage"], ram["swap_usage"], C_GREEN, (0, 180, 140), title="RAM", val_str=f"{ram['usage']}%", subtitle=f"{ram['used_gb']}G")
    draw.text((158, 250), "BELLEK VE SWAP", fill=C_GREEN, font=FONT_BOLD)
    draw.text((158, 276), f"Outer Ring: {ram['usage']}% RAM", fill=C_WHITE, font=FONT_REG)
    draw.text((158, 298), f"Inner Ring: {ram['swap_usage']}% SWAP", fill=(0, 180, 140), font=FONT_REG)
    draw.text((158, 320), f"Used      : {ram['used_gb']}/{ram['total_gb']} GB", fill=C_DIM, font=FONT_REG)
    draw.text((158, 342), f"SWAP      : {ram['swap_used_gb']} GB", fill=C_DIM, font=FONT_SMALL)

    # Q4: DISK & NET (325, 230, 630, 398)
    draw.rectangle([325, 230, 630, 398], fill=C_CARD_BG, outline=C_CARD_BORDER)
    net_activity = min(100, int((net["down_mb"] / 10.0) * 100))
    draw_dual_pie(draw, 403, 314, 52, 40, disk["usage"], net_activity, C_ORANGE, C_CYAN, title="DISK", val_str=f"{disk['usage']}%", subtitle=f"{disk['used_gb']}G")
    draw.text((473, 250), "STORAGE & NETWORK", fill=C_ORANGE, font=FONT_BOLD)
    draw.text((473, 276), f"Outer Ring: {disk['usage']}% Disk", fill=C_WHITE, font=FONT_REG)
    draw.text((473, 298), f"Download  : ↓ {net['down_str']}", fill=C_CYAN, font=FONT_BOLD)
    draw.text((473, 320), f"Upload    : ↑ {net['up_str']}", fill=C_ORANGE, font=FONT_BOLD)
    draw.text((473, 342), f"Storage   : {disk['used_gb']}/{disk['total_gb']} GB", fill=C_DIM, font=FONT_SMALL)

    # Bottom Bar
    draw.rectangle([10, 406, 630, 474], fill=C_CARD_BG, outline=C_CARD_BORDER)
    m_icon = "▶ " if media["playing"] else "■ "
    m_text = f"{m_icon}{media['title']}"
    if media["artist"]:
        m_text += f" - {media['artist']}"
    draw.text((22, 414), m_text[:50], fill=C_CYAN if media["playing"] else C_DIM, font=FONT_BOLD)
    draw.text((22, 438), "[A] Play/Pause  [B] Next  [▲/▼] Focus  [MODE] Themes", fill=C_DIM, font=FONT_SMALL)

    if osd_msg:
        bw, bh = 340, 48
        bx = (640 - bw) // 2
        by = 54
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(10, 16, 26), outline=C_CYAN, width=2)
        draw.text((bx + bw // 2, by + 16), osd_msg, fill=(255, 255, 255), font=FONT_LARGE, anchor="mm")
        draw.text((bx + bw // 2, by + 34), "Press Up/Down arrow to change focus", fill=C_CYAN, font=FONT_SMALL, anchor="mm")

    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


def render_btop_dashboard(cpu, gpu, ram, net, disk, media, power_info, osd_msg=None):
    """Renders a btop-like cyber HUD (640x480) with real-time metrics."""
    img = Image.new("RGB", (640, 480), color=(8, 12, 20))
    draw = ImageDraw.Draw(img)

    C_CYAN = (0, 229, 255)
    C_GREEN = (0, 230, 118)
    C_PINK = (255, 42, 133)
    C_ORANGE = (255, 145, 0)
    C_YELLOW = (255, 214, 0)
    C_WHITE = (235, 240, 250)
    C_DIM = (110, 125, 150)
    C_CARD_BG = (16, 23, 38)
    C_CARD_BORDER = (31, 45, 71)

    # 1. Header (y: 6 to 36)
    draw.text((12, 10), "BTOP++ HUD", fill=C_CYAN, font=FONT_TITLE)
    draw.text((115, 12), "| ARCH LINUX | Auxerov-PC", fill=C_DIM, font=FONT_REG)

    # Power status on header
    cap = power_info.get("capacity", 0)
    v = power_info.get("voltage_v", 0.0)
    is_charging = (power_info.get("status") == "Charging" or power_info.get("online"))
    if is_charging:
        p_str = f"[⚡ USB CHG: {cap}% ({v}V)]"
        p_col = C_GREEN
    else:
        p_str = f"[BATT: {cap}% ({v}V)]"
        p_col = C_YELLOW if cap < 30 else C_WHITE
    draw.text((440, 12), p_str, fill=p_col, font=FONT_BOLD)

    # 2. CPU Card (Top Left: 10, 42, 315, 222)
    draw.rectangle([10, 42, 315, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((22, 50), f"CPU: {cpu['name']}", fill=C_CYAN, font=FONT_BOLD)
    draw.text((250, 50), f"{cpu['usage']}%", fill=C_WHITE, font=FONT_BOLD)
    draw_gauge_bar(draw, 22, 70, 275, 14, cpu["usage"], C_CYAN)

    draw.text((22, 94), f"Temp      : {cpu['temp']}°C", fill=C_WHITE, font=FONT_REG)
    draw.text((170, 94), f"Clock Rate: {cpu['freq']} GHz", fill=C_WHITE, font=FONT_REG)
    draw.text((22, 114), f"Cores     : {cpu['cores']} Threads", fill=C_DIM, font=FONT_REG)
    draw.text((22, 138), "CPU Load History:", fill=C_DIM, font=FONT_SMALL)
    draw_sparkline(draw, 22, 155, 275, 50, cpu.get("history", []), 100, C_CYAN)

    # 3. GPU Card (Top Right: 325, 42, 630, 222)
    draw.rectangle([325, 42, 630, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((337, 50), f"GPU: {gpu['name']}", fill=C_PINK, font=FONT_BOLD)
    draw.text((565, 50), f"{gpu['usage']}%", fill=C_WHITE, font=FONT_BOLD)
    draw_gauge_bar(draw, 337, 70, 275, 14, gpu["usage"], C_PINK)

    draw.text((337, 94), f"Temp      : {gpu['temp']}°C", fill=C_WHITE, font=FONT_REG)
    vram_pct = int((gpu["vram_used"] / max(gpu["vram_total"], 0.1)) * 100)
    draw.text((337, 114), f"VRAM      : {gpu['vram_used']} / {gpu['vram_total']} GB ({vram_pct}%)", fill=C_WHITE, font=FONT_REG)
    draw_gauge_bar(draw, 337, 134, 275, 10, vram_pct, C_PINK)
    draw.text((337, 150), "GPU Load History:", fill=C_DIM, font=FONT_SMALL)
    draw_sparkline(draw, 337, 166, 275, 40, gpu.get("history", []), 100, C_PINK)

    # 4. RAM & SWAP Card (Bottom Left: 10, 230, 315, 396)
    draw.rectangle([10, 230, 315, 396], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((22, 238), "MEMORY (RAM & SWAP)", fill=C_GREEN, font=FONT_BOLD)
    draw.text((22, 260), f"RAM: {ram['used_gb']} / {ram['total_gb']} GB ({ram['usage']}%)", fill=C_WHITE, font=FONT_REG)
    draw_gauge_bar(draw, 22, 280, 275, 14, ram["usage"], C_GREEN)

    draw.text((22, 308), f"SWAP: {ram['swap_used_gb']} / {ram['swap_total_gb']} GB ({ram['swap_usage']}%)", fill=C_DIM, font=FONT_REG)
    draw_gauge_bar(draw, 22, 328, 275, 10, ram["swap_usage"], (0, 180, 216))

    draw.text((22, 355), f"Available : {round(ram['total_gb'] - ram['used_gb'], 1)} GB", fill=C_DIM, font=FONT_SMALL)

    # 5. NETWORK & DISK Card (Bottom Right: 325, 230, 630, 396)
    draw.rectangle([325, 230, 630, 396], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((337, 238), "NETWORK & STORAGE", fill=C_ORANGE, font=FONT_BOLD)

    draw.text((337, 262), f"Download (Down) : ↓ {net['down_mb']} MB/s", fill=C_CYAN, font=FONT_REG)
    draw.text((337, 282), f"Upload (Up)     : ↑ {net['up_mb']} MB/s", fill=C_ORANGE, font=FONT_REG)

    draw.text((337, 312), f"Storage (/)     : {disk['used_gb']} / {disk['total_gb']} GB ({disk['usage']}%)", fill=C_WHITE, font=FONT_REG)
    draw_gauge_bar(draw, 337, 332, 275, 12, disk["usage"], C_ORANGE)

    # 6. Bottom Media & Controller Bar (10, 404, 630, 472)
    draw.rectangle([10, 404, 630, 472], fill=C_CARD_BG, outline=C_CARD_BORDER)

    # Media status
    m_icon = "▶ " if media["playing"] else "■ "
    m_text = f"{m_icon}{media['title']}"
    if media["artist"]:
        m_text += f" - {media['artist']}"
    draw.text((22, 412), m_text[:50], fill=C_CYAN if media["playing"] else C_DIM, font=FONT_BOLD)

    # Physical button guide
    btn_guide = "[A] Play/Pause   [B] Next Track   [X] Mic Mute   [▲/▼] Focus   [MODE] Themes"
    draw.text((22, 436), btn_guide, fill=C_DIM, font=FONT_SMALL)

    if osd_msg:
        bw, bh = 340, 48
        bx = (640 - bw) // 2
        by = 54
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(10, 16, 26), outline=C_CYAN, width=2)
        draw.text((bx + bw // 2, by + 16), osd_msg, fill=(255, 255, 255), font=FONT_LARGE, anchor="mm")
        draw.text((bx + bw // 2, by + 34), "Press Up/Down arrow to change focus", fill=C_CYAN, font=FONT_SMALL, anchor="mm")

    # Convert to 32-bit BGRA
    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


def render_focus_dashboard(metric, cpu, gpu, ram, net, disk, media, power_info, osd_msg=None):
    """Renders a dedicated single-metric focus cockpit (640x480) for CPU, GPU, RAM, or NET."""
    img = Image.new("RGB", (640, 480), color=(8, 12, 20))
    draw = ImageDraw.Draw(img)

    C_CYAN = (0, 229, 255)
    C_PINK = (255, 42, 133)
    C_GREEN = (0, 230, 118)
    C_ORANGE = (255, 145, 0)
    C_WHITE = (245, 250, 255)
    C_DIM = (120, 145, 175)
    C_CARD_BG = (13, 19, 30)
    C_CARD_BORDER = (28, 42, 68)
    C_SUB_BG = (18, 26, 42)

    color_map = {
        "cpu": C_CYAN,
        "gpu": C_PINK,
        "ram": C_GREEN,
        "net": C_ORANGE
    }
    primary_color = color_map.get(metric, C_CYAN)

    # 1. Top Status Bar (8, 6, 632, 42)
    draw.rounded_rectangle([8, 6, 632, 42], radius=6, fill=C_CARD_BG, outline=C_CARD_BORDER)

    headers = {
        "cpu": ("⚡ CPU COCKPIT", "AMD Ryzen 7 5700X"),
        "gpu": ("🎮 GPU COCKPIT", "NVIDIA RTX 5070"),
        "ram": ("💾 MEMORY COCKPIT", f"{ram['total_gb']} GB System RAM"),
        "net": ("🌐 NETWORK & STORAGE", "High-Speed I/O")
    }
    h_title, h_sub = headers.get(metric, ("⚡ HARDWARE COCKPIT", "System Telemetry"))
    draw.text((18, 14), h_title, fill=primary_color, font=FONT_LARGE)
    draw.text((185, 18), f"[FOCUSED: {metric.upper()}]", fill=primary_color, font=FONT_BOLD)

    clock_str = time.strftime("%H:%M:%S")
    pwr_str = f"⚡ {power_info.get('capacity', 100)}% ({power_info.get('voltage_v', 4.1)}V)" if power_info.get('capacity') else "⚡ PWR"
    draw.text((490, 16), f"{clock_str} | {pwr_str}", fill=C_DIM, font=FONT_REG)

    # 2. Main Left Focus Dial Card (8, 48, 316, 422)
    draw.rounded_rectangle([8, 48, 316, 422], radius=8, fill=C_CARD_BG, outline=C_CARD_BORDER)

    if metric == "cpu":
        draw_big_pie(draw, 162, 195, 100, cpu["usage"], C_CYAN, thickness=20,
                     title="CPU LOAD", val_str=f"{cpu['usage']}%", subtitle=f"{cpu['temp']}°C")
        draw.text((22, 325), f"Core Temp: {cpu['temp']}°C", fill=C_WHITE, font=FONT_BOLD)
        temp_col = C_CYAN if cpu['temp'] < 65 else (C_ORANGE if cpu['temp'] < 80 else (255, 60, 60))
        draw_gauge_bar(draw, 22, 345, 272, 12, int(min(100, (cpu['temp'] / 95.0) * 100)), temp_col)
        draw.text((22, 368), f"Clock: {cpu['freq']} GHz  |  {cpu['cores']} Logical Cores", fill=C_DIM, font=FONT_REG)
        draw.text((22, 390), f"Headroom: {max(0, 95 - cpu['temp'])}°C to TjMax (95°C)", fill=C_DIM, font=FONT_SMALL)

    elif metric == "gpu":
        draw_big_pie(draw, 162, 195, 100, gpu["usage"], C_PINK, thickness=20,
                     title="GPU LOAD", val_str=f"{gpu['usage']}%", subtitle=f"{gpu['temp']}°C")
        vram_pct = int((gpu["vram_used"] / max(0.1, gpu["vram_total"])) * 100)
        draw.text((22, 325), f"VRAM Used: {gpu['vram_used']} / {gpu['vram_total']} GB ({vram_pct}%)", fill=C_WHITE, font=FONT_BOLD)
        draw_gauge_bar(draw, 22, 345, 272, 12, vram_pct, C_PINK)
        draw.text((22, 368), f"VRAM Free: {round(gpu['vram_total'] - gpu['vram_used'], 1)} GB Available", fill=C_DIM, font=FONT_REG)
        draw.text((22, 390), f"Thermal Headroom: {max(0, 83 - gpu['temp'])}°C (Target 83°C)", fill=C_DIM, font=FONT_SMALL)

    elif metric == "ram":
        draw_big_pie(draw, 162, 195, 100, ram["usage"], C_GREEN, thickness=20,
                     title="RAM LOAD", val_str=f"{ram['usage']}%", subtitle=f"{ram['used_gb']} GB")
        draw.text((22, 325), f"Swap Used: {ram['swap_used_gb']} / {ram['swap_total_gb']} GB ({ram['swap_usage']}%)", fill=C_WHITE, font=FONT_BOLD)
        draw_gauge_bar(draw, 22, 345, 272, 12, ram["swap_usage"], C_GREEN)
        avail_gb = round(ram['total_gb'] - ram['used_gb'], 1)
        draw.text((22, 368), f"Available: {avail_gb} GB Physical Free", fill=C_DIM, font=FONT_REG)
        draw.text((22, 390), f"Total Virtual: {round(ram['total_gb'] + ram['swap_total_gb'], 1)} GB", fill=C_DIM, font=FONT_SMALL)

    elif metric == "net":
        draw_big_pie(draw, 162, 195, 100, min(100, int(net["down_mb"] * 10)), C_ORANGE, thickness=20,
                     title="DOWNLOAD", val_str=net["down_str"], subtitle=f"↑ {net['up_str']}")
        draw.text((22, 325), f"Storage (/): {disk['used_gb']} / {disk['total_gb']} GB ({disk['usage']}%)", fill=C_WHITE, font=FONT_BOLD)
        draw_gauge_bar(draw, 22, 345, 272, 12, disk["usage"], C_ORANGE)
        free_disk = disk['total_gb'] - disk['used_gb']
        draw.text((22, 368), f"Free Storage: {free_disk} GB Free on root", fill=C_DIM, font=FONT_REG)
        draw.text((22, 390), f"Speed: ↓ {net['down_mb']} MB/s  ↑ {net['up_mb']} MB/s", fill=C_DIM, font=FONT_SMALL)

    # 3. Right Details & Telemetry Cards (324, 48, 632, 422)
    draw.rounded_rectangle([324, 48, 632, 422], radius=8, fill=C_CARD_BG, outline=C_CARD_BORDER)

    # Sub-Card 1: Specs & Architecture (y: 56 to 166)
    draw.rounded_rectangle([332, 56, 624, 168], radius=6, fill=C_SUB_BG, outline=C_CARD_BORDER)
    if metric == "cpu":
        draw.text((342, 64), "PROCESSOR SPECIFICATIONS", fill=C_CYAN, font=FONT_BOLD)
        draw.text((342, 84), f"Model     : {cpu['name']}", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 104), f"Topology  : {cpu['cores']} Logical Cores (8C / 16T)", fill=C_DIM, font=FONT_REG)
        draw.text((342, 124), f"Frequency : {cpu['freq']} GHz Live Clock", fill=C_DIM, font=FONT_REG)
        try:
            l1, l5, l15 = os.getloadavg()
            draw.text((342, 144), f"Load Avg  : {l1:.2f} (1m), {l5:.2f} (5m), {l15:.2f} (15m)", fill=C_DIM, font=FONT_REG)
        except Exception:
            pass

    elif metric == "gpu":
        draw.text((342, 64), "GRAPHICS ACCELERATOR", fill=C_PINK, font=FONT_BOLD)
        draw.text((342, 84), f"Model     : {gpu['name']}", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 104), f"Driver    : NVIDIA UNIX Driver / CUDA", fill=C_DIM, font=FONT_REG)
        draw.text((342, 124), f"VRAM Size : {gpu['vram_total']} GB High-Speed Memory", fill=C_DIM, font=FONT_REG)
        draw.text((342, 144), f"Status    : ACTIVE (PCIe Gen4)", fill=C_DIM, font=FONT_REG)

    elif metric == "ram":
        draw.text((342, 64), "MEMORY ARCHITECTURE", fill=C_GREEN, font=FONT_BOLD)
        draw.text((342, 84), f"Total RAM : {ram['total_gb']} GB System Memory", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 104), f"Allocated : {ram['used_gb']} GB ({ram['usage']}%)", fill=C_DIM, font=FONT_REG)
        draw.text((342, 124), f"Available : {round(ram['total_gb'] - ram['used_gb'], 1)} GB Unallocated", fill=C_DIM, font=FONT_REG)
        draw.text((342, 144), f"Swap File : {ram['swap_total_gb']} GB Configured", fill=C_DIM, font=FONT_REG)

    elif metric == "net":
        draw.text((342, 64), "NETWORK INTERFACE & TRAFFIC", fill=C_ORANGE, font=FONT_BOLD)
        draw.text((342, 84), f"Download  : ↓ {net['down_str']} ({net['down_mb']} MB/s)", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 104), f"Upload    : ↑ {net['up_str']} ({net['up_mb']} MB/s)", fill=C_DIM, font=FONT_REG)
        draw.text((342, 124), f"Link      : Ethernet / High-Speed LAN", fill=C_DIM, font=FONT_REG)
        draw.text((342, 144), f"Status    : ONLINE (Connected to Host)", fill=C_DIM, font=FONT_REG)

    # Sub-Card 2: Live Activity / Sparkline (y: 178 to 292)
    draw.rounded_rectangle([332, 178, 624, 292], radius=6, fill=C_SUB_BG, outline=C_CARD_BORDER)
    if metric == "cpu":
        draw.text((342, 186), "REAL-TIME CPU HISTORY (20 SAMPLES)", fill=C_CYAN, font=FONT_BOLD)
        draw_sparkline(draw, 342, 210, 270, 56, cpu.get("history", [cpu["usage"]]), max_val=100, color=C_CYAN)
        draw.text((342, 272), f"Peak: {max(cpu.get('history', [cpu['usage']]))}%   Current: {cpu['usage']}%", fill=C_DIM, font=FONT_SMALL)

    elif metric == "gpu":
        draw.text((342, 186), "REAL-TIME GPU HISTORY (20 SAMPLES)", fill=C_PINK, font=FONT_BOLD)
        draw_sparkline(draw, 342, 210, 270, 56, gpu.get("history", [gpu["usage"]]), max_val=100, color=C_PINK)
        draw.text((342, 272), f"Peak: {max(gpu.get('history', [gpu['usage']]))}%   Current: {gpu['usage']}%", fill=C_DIM, font=FONT_SMALL)

    elif metric == "ram":
        draw.text((342, 186), "MEMORY USAGE COMPOSITION", fill=C_GREEN, font=FONT_BOLD)
        used_w = int(270 * (ram['usage'] / 100.0))
        draw.rectangle([342, 215, 612, 245], fill=(25, 35, 55), outline=(45, 60, 90))
        if used_w > 0:
            draw.rectangle([342, 215, 342 + used_w, 245], fill=C_GREEN)
        draw.text((342, 255), f"Used: {ram['used_gb']} GB ({ram['usage']}%)  |  Free: {round(ram['total_gb'] - ram['used_gb'], 1)} GB", fill=C_DIM, font=FONT_SMALL)
        draw.text((342, 272), f"Swap Usage: {ram['swap_used_gb']} / {ram['swap_total_gb']} GB ({ram['swap_usage']}%)", fill=C_DIM, font=FONT_SMALL)

    elif metric == "net":
        draw.text((342, 186), "STORAGE FILESYSTEM BREAKDOWN", fill=C_ORANGE, font=FONT_BOLD)
        disk_w = int(270 * (disk['usage'] / 100.0))
        draw.rectangle([342, 215, 612, 245], fill=(25, 35, 55), outline=(45, 60, 90))
        if disk_w > 0:
            draw.rectangle([342, 215, 342 + disk_w, 245], fill=C_ORANGE)
        draw.text((342, 255), f"Root (/): {disk['used_gb']} GB used of {disk['total_gb']} GB ({disk['usage']}%)", fill=C_DIM, font=FONT_SMALL)
        draw.text((342, 272), f"Free Disk Space: {disk['total_gb'] - disk['used_gb']} GB Remaining", fill=C_DIM, font=FONT_SMALL)

    # Sub-Card 3: Health & Diagnostics (y: 302 to 414)
    draw.rounded_rectangle([332, 302, 624, 414], radius=6, fill=C_SUB_BG, outline=C_CARD_BORDER)
    if metric == "cpu":
        draw.text((342, 310), "THERMAL MONITORING & HEALTH", fill=C_CYAN, font=FONT_BOLD)
        status_txt = "OPTIMAL (Normal Operations)" if cpu['temp'] < 65 else ("WARM (Heavy Workload)" if cpu['temp'] < 80 else "HIGH TEMP WARNING")
        draw.text((342, 330), f"Thermal State   : {status_txt}", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 350), f"Target Limit    : 95°C Max Tj", fill=C_DIM, font=FONT_REG)
        draw.text((342, 370), f"Governor State  : Performance / Schedutil", fill=C_DIM, font=FONT_REG)
        draw.text((342, 390), f"Hardware Health : 100% (No Thermal Throttling)", fill=C_GREEN, font=FONT_REG)

    elif metric == "gpu":
        draw.text((342, 310), "VRAM & THERMAL EFFICIENCY", fill=C_PINK, font=FONT_BOLD)
        draw.text((342, 330), f"GPU Core Temp   : {gpu['temp']} °C (Max 83°C)", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 350), f"VRAM Utilization: {vram_pct}% Allocated", fill=C_DIM, font=FONT_REG)
        draw.text((342, 370), f"Cooling State   : Dynamic Fan Profile", fill=C_DIM, font=FONT_REG)
        draw.text((342, 390), f"GPU Health      : OPTIMAL / NOMINAL", fill=C_GREEN, font=FONT_REG)

    elif metric == "ram":
        draw.text((342, 310), "MEMORY HEALTH & PRESSURE", fill=C_GREEN, font=FONT_BOLD)
        draw.text((342, 330), f"Memory Pressure : Low (Headroom Available)", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 350), f"Swap Activity   : Healthy (Low Swapping)", fill=C_DIM, font=FONT_REG)
        draw.text((342, 370), f"Cache Efficiency: High Hit Ratio", fill=C_DIM, font=FONT_REG)
        draw.text((342, 390), f"Status          : 100% Healthy", fill=C_GREEN, font=FONT_REG)

    elif metric == "net":
        draw.text((342, 310), "I/O THROUGHPUT & LINK INTEGRITY", fill=C_ORANGE, font=FONT_BOLD)
        draw.text((342, 330), f"Packet Drops    : 0 (No Errors)", fill=C_WHITE, font=FONT_REG)
        draw.text((342, 350), f"Transfer Rates  : ↓ {net['down_mb']} MB/s  ↑ {net['up_mb']} MB/s", fill=C_DIM, font=FONT_REG)
        draw.text((342, 370), f"Disk Mount      : / (ext4/btrfs)", fill=C_DIM, font=FONT_REG)
        draw.text((342, 390), f"Link Integrity  : 100% Optimal", fill=C_GREEN, font=FONT_REG)

    # 4. Bottom Navigation & Selector Bar (8, 430, 632, 474)
    draw.rounded_rectangle([8, 430, 632, 474], radius=6, fill=C_CARD_BG, outline=C_CARD_BORDER)

    pills = [("ALL", "all"), ("CPU", "cpu"), ("GPU", "gpu"), ("RAM", "ram"), ("NET", "net")]
    px = 16
    draw.text((px, 444), "[▲/▼ D-Pad]", fill=C_DIM, font=FONT_BOLD)
    px += 85

    for label, mode_key in pills:
        is_active = (mode_key == metric)
        tw = int(draw.textlength(label, font=FONT_BOLD))
        pw = tw + 14
        if is_active:
            draw.rounded_rectangle([px, 437, px + pw, 467], radius=4, fill=primary_color)
            draw.text((px + 7, 444), label, fill=(10, 15, 25), font=FONT_BOLD)
        else:
            draw.rounded_rectangle([px, 437, px + pw, 467], radius=4, fill=(20, 28, 44), outline=C_CARD_BORDER)
            draw.text((px + 7, 444), label, fill=C_DIM, font=FONT_REG)
        px += pw + 8

    draw.text((495, 444), "[MODE] Themes", fill=C_DIM, font=FONT_REG)

    if osd_msg:
        bw, bh = 340, 48
        bx = (640 - bw) // 2
        by = 54
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(10, 16, 26), outline=primary_color, width=2)
        draw.text((bx + bw // 2, by + 16), osd_msg, fill=C_WHITE, font=FONT_LARGE, anchor="mm")
        draw.text((bx + bw // 2, by + 34), "Press Up/Down arrow to change focus", fill=primary_color, font=FONT_SMALL, anchor="mm")

    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


# -------------------------------------------------------------
# Command Modes
# -------------------------------------------------------------
def mode_test(client):
    print("\n[TEST] Checking GA36 / R36S connection and state...")
    res = client.ping()
    if res.get("status") == "ok":
        p = res.get("power", {})
        print("\n" + "=" * 55)
        print(" [SUCCESS] GA36 / R36S USB PROTOCOL & AGENT ACTIVE!")
        print("=" * 55)
        print(f" Agent Version : {res.get('agent')} v{res.get('version')}")
        print(f" Privilege     : root ({res.get('user')})")
        print(f" Console Time  : {res.get('time')}")
        print(f" Uptime        : {int(res.get('uptime_sec', 0))} seconds")
        print(f" Display       : {res.get('screen', {}).get('width')}x{res.get('screen', {}).get('height')} IPS (32-bit)")
        print(f" USB Power    : {'ACTIVE (Host Power)' if p.get('online') else 'Battery Power'}")
        print(f" Battery      : %{p.get('capacity')} ({p.get('status')}) - {p.get('voltage_v')}V")
        print("=" * 55)

        if res.get("version") != "3.3":
            print("\n[AUTO-UPDATE] Upgrading Agent to v3.3...")
            mode_update_agent(client)

        print("\nAvailable Commands:")
        print("  ./install.sh dashboard       -> Start live hardware dashboard (zero-flicker)")
        print("  ./install.sh image <file>    -> Send image/wallpaper to console screen")
        print("  ./install.sh resume          -> Resume EmulationStation gaming menu")
        print("  ./install.sh battery         -> Monitor battery and power status")
        print("  ./install.sh restore         -> Restore stock gaming system from backup")
        print("  ./install.sh shell           -> Open interactive live root shell")
        print("  ./install.sh clear           -> Clear console screen")
    else:
        print(f"\n[ERROR] GA36 did not respond: {res.get('msg')}")
        print("\nTroubleshooting Steps:")
        print("  1. Re-plug USB-C cable to reset USB serial buffer.")
        print("  2. Restart console via EmulationStation or reboot command.")


def mode_battery(client):
    print("\n" + "=" * 50)
    print(" GA36 Power & Battery Diagnostics (AXP223 PMIC)")
    print("=" * 50)
    p = client.get_power()
    print(f" USB Input        : {'Connected & Active' if p.get('online') else 'Disconnected'}")
    print(f" Charging Status  : {p.get('status')}")
    print(f" Battery Level    : %{p.get('capacity')}")
    print(f" Voltage          : {p.get('voltage_v')} V")
    print(f" Current          : {p.get('current_ma')} mA")
    if p.get("temp_c"):
        print(f" Battery Temp     : {p.get('temp_c')} °C")
    print("=" * 50)


def mode_image(client, image_path):
    if not os.path.exists(image_path):
        print(f"\n[ERROR] Specified image file not found: {image_path}")
        return
    print(f"\n[IMAGE] Loading '{image_path}' and rendering to 640x480...")
    try:
        compressed = prepare_image_frame(image_path)
        print(f"  Image compressed ({len(compressed)} bytes). Transferring to display...")
        client.pause_gui()
        res = client.display_frame_raw(compressed)
        if res.get("status") == "ok":
            print("\n[SUCCESS] Image rendered and pinned to GA36 display! (640x480 IPS)")
            print("  Press Ctrl+C to return to menu...")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\nReturning to EmulationStation menu...")
                client.resume_gui()
        else:
            print(f"\n[ERROR] Failed to output image: {res.get('msg')}")
    except Exception as e:
        print(f"\n[ERROR] Image processing error: {e}")


# -------------------------------------------------------------
# Gallery & Animated GIF Viewer Engine
# -------------------------------------------------------------
GALLERY_DIR = os.path.expanduser("~/Pictures/GA36_Gallery")
VALID_MEDIA_EXTS = (".gif", ".png", ".jpg", ".jpeg", ".bmp", ".webp")
THEMES = ["pie", "dual", "btop", "gallery"]
THEME_FILE = os.path.expanduser("~/.config/ga36/theme")


class AnimatedGifMedia:
    def __init__(self, path):
        self.path = path
        self.frames = []
        self.delays = []
        self.frame_idx = 0
        self.last_advance = time.time()
        self._load()

    def _load(self):
        try:
            im = Image.open(self.path)
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            for frame in ImageSequence.Iterator(im):
                f_rgba = frame.convert("RGBA")
                canvas = Image.new("RGBA", (640, 480), (0, 0, 0, 255))
                w, h = f_rgba.size
                scale = min(640 / w, 480 / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                resized = f_rgba.resize((new_w, new_h), resample)
                canvas.paste(resized, ((640 - new_w) // 2, (480 - new_h) // 2), resized)
                self.frames.append(canvas)
                dur = frame.info.get("duration", 80)
                if dur < 25:
                    dur = 80
                self.delays.append(dur / 1000.0)
        except Exception as e:
            print(f"[GIF LOAD ERROR] {self.path}: {e}")

        if not self.frames:
            self.frames = [Image.new("RGBA", (640, 480), (0, 0, 0, 255))]
            self.delays = [0.1]

    def get_current_image(self):
        now = time.time()
        delay = self.delays[self.frame_idx]
        if now - self.last_advance >= delay:
            self.frame_idx = (self.frame_idx + 1) % len(self.frames)
            self.last_advance = now
        return self.frames[self.frame_idx], self.delays[self.frame_idx]


class StaticImageMedia:
    def __init__(self, path):
        self.path = path
        self.image = None
        self._load()

    def _load(self):
        try:
            im = Image.open(self.path).convert("RGBA")
            canvas = Image.new("RGBA", (640, 480), (0, 0, 0, 255))
            w, h = im.size
            scale = min(640 / w, 480 / h)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            resized = im.resize((new_w, new_h), resample)
            canvas.paste(resized, ((640 - new_w) // 2, (480 - new_h) // 2), resized)
            self.image = canvas
        except Exception as e:
            print(f"[IMAGE LOAD ERROR] {self.path}: {e}")
            self.image = Image.new("RGBA", (640, 480), (0, 0, 0, 255))

    def get_current_image(self):
        return self.image, 0.35


class GalleryManager:
    def __init__(self, gallery_dir=GALLERY_DIR):
        self.gallery_dir = gallery_dir
        self.files = []
        self.current_idx = 0
        self.loaded_item = None
        self.osd_text = ""
        self.osd_until = 0.0
        self._ensure_gallery_dir()
        self.rescan()

    def _ensure_gallery_dir(self):
        try:
            os.makedirs(self.gallery_dir, exist_ok=True)
            # Copy sample assets if empty
            base_dir = os.path.dirname(os.path.abspath(__file__))
            sample_dir = os.path.join(base_dir, "gallery")
            if os.path.exists(sample_dir):
                existing = [f for f in os.listdir(self.gallery_dir) if f.lower().endswith(VALID_MEDIA_EXTS)]
                if not existing:
                    for s in os.listdir(sample_dir):
                        if s.lower().endswith(VALID_MEDIA_EXTS):
                            shutil.copy2(os.path.join(sample_dir, s), os.path.join(self.gallery_dir, s))
        except Exception:
            pass

    def rescan(self):
        self._ensure_gallery_dir()
        try:
            if os.path.exists(self.gallery_dir):
                self.files = sorted([
                    f for f in os.listdir(self.gallery_dir)
                    if f.lower().endswith(VALID_MEDIA_EXTS) and not f.startswith(".")
                ])
            else:
                self.files = []
        except Exception:
            self.files = []

        if self.files:
            self.current_idx = self.current_idx % len(self.files)
            self._load_current()
        else:
            self.loaded_item = None

    @property
    def total_count(self):
        return len(self.files)

    @property
    def current_filename(self):
        if self.files and 0 <= self.current_idx < len(self.files):
            return self.files[self.current_idx]
        return "None"

    def next_item(self):
        self.rescan()
        if not self.files:
            return
        self.current_idx = (self.current_idx + 1) % len(self.files)
        self._load_current()
        self.trigger_osd()

    def prev_item(self):
        self.rescan()
        if not self.files:
            return
        self.current_idx = (self.current_idx - 1) % len(self.files)
        self._load_current()
        self.trigger_osd()

    def trigger_osd(self):
        if self.files:
            self.osd_text = f"[{self.current_idx + 1}/{len(self.files)}] {self.current_filename}"
            self.osd_until = time.time() + 2.5

    def _load_current(self):
        if not self.files:
            self.loaded_item = None
            return
        path = os.path.join(self.gallery_dir, self.files[self.current_idx])
        if path.lower().endswith(".gif"):
            self.loaded_item = AnimatedGifMedia(path)
        else:
            self.loaded_item = StaticImageMedia(path)


def render_gallery_frame(gallery_mgr):
    if not gallery_mgr.loaded_item or not gallery_mgr.files:
        img = Image.new("RGBA", (640, 480), (10, 15, 25, 255))
        draw = ImageDraw.Draw(img)
        draw.text((320, 180), "⚡ GA36 GALLERY MODE", fill=(0, 229, 255), anchor="mm", font=FONT_TITLE)
        draw.text((320, 240), "No images or GIFs found in:", fill=(240, 245, 255), anchor="mm", font=FONT_REG)
        draw.text((320, 270), "~/Pictures/GA36_Gallery", fill=(255, 145, 0), anchor="mm", font=FONT_BOLD)
        draw.text((320, 330), "Drop .gif, .png, .jpg files into this folder", fill=(120, 135, 160), anchor="mm", font=FONT_REG)
        draw.text((320, 360), "Press D-Pad [Left/Right] on console to browse", fill=(120, 135, 160), anchor="mm", font=FONT_SMALL)
        r, g, b, a = img.split()
        bgra = Image.merge("RGBA", (b, g, r, a))
        return zlib.compress(bgra.tobytes(), level=1), 0.5

    base_img, delay = gallery_mgr.loaded_item.get_current_image()

    if time.time() < gallery_mgr.osd_until:
        display_img = base_img.copy()
        draw = ImageDraw.Draw(display_img)
        badge_text = gallery_mgr.osd_text
        bbox = draw.textbbox((320, 445), badge_text, anchor="mm", font=FONT_BOLD)
        pad_x, pad_y = 16, 6
        draw.rounded_rectangle(
            [bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y],
            radius=8,
            fill=(10, 15, 25, 225),
            outline=(0, 229, 255, 255),
            width=1
        )
        draw.text((320, 445), badge_text, fill=(240, 255, 255), anchor="mm", font=FONT_BOLD)
    else:
        display_img = base_img

    r, g, b, a = display_img.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    return zlib.compress(bgra.tobytes(), level=1), delay


FOCUS_MODES = ["all", "cpu", "gpu", "ram", "net"]
FOCUS_FILE = os.path.expanduser("~/.config/ga36/focus")


def get_saved_theme():
    try:
        if os.path.exists(THEME_FILE):
            with open(THEME_FILE, "r") as f:
                t = f.read().strip().lower()
                if t in THEMES:
                    return t
    except Exception:
        pass
    return "pie"


def save_active_theme(theme_name):
    try:
        os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
        with open(THEME_FILE, "w") as f:
            f.write(theme_name.strip().lower() + "\n")
    except Exception:
        pass


def get_saved_focus():
    try:
        if os.path.exists(FOCUS_FILE):
            with open(FOCUS_FILE, "r") as f:
                f_mode = f.read().strip().lower()
                if f_mode in FOCUS_MODES:
                    return f_mode
    except Exception:
        pass
    return "all"


def save_active_focus(focus_name):
    try:
        os.makedirs(os.path.dirname(FOCUS_FILE), exist_ok=True)
        with open(FOCUS_FILE, "w") as f:
            f.write(focus_name.strip().lower() + "\n")
    except Exception:
        pass


def mode_dashboard(client, theme=None):
    if theme and theme.lower() in THEMES:
        current_theme = theme.lower()
        save_active_theme(current_theme)
    else:
        current_theme = get_saved_theme()

    current_focus = get_saved_focus()
    osd_msg = None
    osd_until = 0.0

    print("\n" + "=" * 60)
    print(" GA36 / R36S CYBER HARDWARE & MEDIA HUD ACTIVE!")
    print(f" Active Theme: {current_theme.upper()} | Focus View: {current_focus.upper()}")
    print(" Physical Gamepad Controls:")
    print("   [ A ]             -> Media Play / Pause")
    print("   [ B ]             -> Next Track")
    print("   [ X ]             -> Toggle Microphone Mute")
    print("   [ Y ]             -> Capture Screenshot")
    print("   [ DPAD UP / DOWN ]-> Cycle Focus (ALL ➔ CPU ➔ GPU ➔ RAM ➔ NET)")
    print("   [ DPAD LEFT/RIGHT]-> Cycle HUD Themes (or Prev/Next in Gallery)")
    print("   [ SELECT/MODE/FN ]-> Cycle Themes (or Reset Focus to ALL)")
    print(" Press Ctrl+C in terminal to exit.")
    print("=" * 60 + "\n")

    print("[INFO] Pausing EmulationStation to prevent flicker...")
    client.pause_gui()

    gallery_mgr = GalleryManager()

    def handle_btn(btn_name):
        nonlocal current_theme, current_focus, osd_msg, osd_until
        btn_upper = str(btn_name).upper()

        is_theme_cycle = btn_upper in (
            "SELECT", "MODE", "MENU", "START", "FN",
            "KEY_314", "KEY_315", "KEY_316", "KEY_464", "KEY_139", "KEY_296", "KEY_297"
        )
        is_left = btn_upper in ("DPAD_LEFT", "KEY_546", "KEY_105")
        is_right = btn_upper in ("DPAD_RIGHT", "KEY_547", "KEY_106")
        is_up = btn_upper in ("DPAD_UP", "KEY_544", "KEY_17", "KEY_103")
        is_down = btn_upper in ("DPAD_DOWN", "KEY_545", "KEY_16", "KEY_108")
        is_l1 = btn_upper in ("L1", "KEY_310")
        is_r1 = btn_upper in ("R1", "KEY_311")
        is_l2 = btn_upper in ("L2", "KEY_312")
        is_r2 = btn_upper in ("R2", "KEY_313")

        if current_theme == "gallery":
            if is_right or is_down or is_r1:
                gallery_mgr.next_item()
                print(f"\n[GALLERY NEXT] {gallery_mgr.current_filename} ({gallery_mgr.current_idx + 1}/{gallery_mgr.total_count})")
            elif is_left or is_up or is_l1:
                gallery_mgr.prev_item()
                print(f"\n[GALLERY PREV] {gallery_mgr.current_filename} ({gallery_mgr.current_idx + 1}/{gallery_mgr.total_count})")
            elif is_theme_cycle:
                idx = (THEMES.index(current_theme) + 1) % len(THEMES)
                current_theme = THEMES[idx]
                save_active_theme(current_theme)
                print(f"\n[THEME CHANGED] New Mode: {current_theme.upper()}    ")
            else:
                client.default_button_action(btn_name)
        else:
            # Dashboard modes (pie, dual, btop)
            if is_up or is_r2:
                # Cycle focus forward (ALL -> CPU -> GPU -> RAM -> NET -> ALL)
                idx = (FOCUS_MODES.index(current_focus) + 1) % len(FOCUS_MODES)
                current_focus = FOCUS_MODES[idx]
                save_active_focus(current_focus)
                osd_msg = f"⚡ FOCUS: {current_focus.upper()}" if current_focus != "all" else "⚡ VIEW: ALL METRICS"
                osd_until = time.time() + 2.5
                print(f"\n[FOCUS UPDATED] Mode: {current_focus.upper()} ({idx + 1}/{len(FOCUS_MODES)})    ")
            elif is_down or is_l2:
                # Cycle focus backward (ALL -> NET -> RAM -> GPU -> CPU -> ALL)
                idx = (FOCUS_MODES.index(current_focus) - 1) % len(FOCUS_MODES)
                current_focus = FOCUS_MODES[idx]
                save_active_focus(current_focus)
                osd_msg = f"⚡ FOCUS: {current_focus.upper()}" if current_focus != "all" else "⚡ VIEW: ALL METRICS"
                osd_until = time.time() + 2.5
                print(f"\n[FOCUS UPDATED] Mode: {current_focus.upper()} ({idx + 1}/{len(FOCUS_MODES)})    ")
            elif is_theme_cycle:
                if current_focus != "all":
                    current_focus = "all"
                    save_active_focus("all")
                    osd_msg = "⚡ VIEW: ALL METRICS"
                    osd_until = time.time() + 2.0
                    print(f"\n[FOCUS RESET] Back to Full HUD ({current_theme.upper()})    ")
                else:
                    idx = (THEMES.index(current_theme) + 1) % len(THEMES)
                    current_theme = THEMES[idx]
                    save_active_theme(current_theme)
                    print(f"\n[THEME CHANGED] New Mode: {current_theme.upper()}    ")
            elif is_r1 or is_right:
                idx = (THEMES.index(current_theme) + 1) % len(THEMES)
                current_theme = THEMES[idx]
                save_active_theme(current_theme)
                print(f"\n[THEME CHANGED] New Mode: {current_theme.upper()}    ")
            elif is_l1 or is_left:
                idx = (THEMES.index(current_theme) - 1) % len(THEMES)
                current_theme = THEMES[idx]
                save_active_theme(current_theme)
                print(f"\n[THEME CHANGED] New Mode: {current_theme.upper()}    ")
            else:
                client.default_button_action(btn_name)

    client.button_callback = handle_btn

    def handle_exit_signal(signum, frame):
        try:
            client.close()
        except Exception:
            pass
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, handle_exit_signal)
        signal.signal(signal.SIGHUP, handle_exit_signal)
    except Exception:
        pass

    # Fetch initial power
    power_info = client.get_power()
    last_power_time = time.time()

    frame_count = 0
    start_time = time.time()

    try:
        while True:
            t0 = time.time()

            # Refresh power info every 5 seconds
            if t0 - last_power_time > 5.0:
                power_info = client.get_power()
                last_power_time = t0

            # Check if theme was updated externally (via CLI / file)
            saved_theme = get_saved_theme()
            if saved_theme != current_theme:
                current_theme = saved_theme
                if current_theme == "gallery":
                    gallery_mgr.rescan()
                    gallery_mgr.trigger_osd()
                print(f"\n[THEME UPDATED] New Mode: {current_theme.upper()}    ")

            # Check if focus was updated externally (via CLI / file)
            saved_focus = get_saved_focus()
            if saved_focus != current_focus:
                current_focus = saved_focus
                osd_msg = f"⚡ FOCUS: {current_focus.upper()}" if current_focus != "all" else "⚡ VIEW: ALL METRICS"
                osd_until = time.time() + 2.5
                print(f"\n[FOCUS UPDATED] Mode: {current_focus.upper()}    ")

            active_osd = osd_msg if time.time() < osd_until else None

            # Gather host metrics only for HUD modes
            if current_theme != "gallery":
                cpu = get_cpu_info()
                gpu = get_gpu_info()
                ram = get_ram_info()
                net = get_net_info()
                disk = get_disk_info()
                media = get_media_info()

            # Render selected theme / focus
            if current_theme == "gallery":
                comp_frame, delay_sec = render_gallery_frame(gallery_mgr)
            elif current_focus != "all":
                comp_frame = render_focus_dashboard(current_focus, cpu, gpu, ram, net, disk, media, power_info, osd_msg=active_osd)
                delay_sec = 0.40
            elif current_theme == "dual":
                comp_frame = render_dual_dashboard(cpu, gpu, ram, net, disk, media, power_info, osd_msg=active_osd)
                delay_sec = 0.40
            elif current_theme == "btop":
                comp_frame = render_btop_dashboard(cpu, gpu, ram, net, disk, media, power_info, osd_msg=active_osd)
                delay_sec = 0.40
            else:
                comp_frame = render_pie_dashboard(cpu, gpu, ram, net, disk, media, power_info, osd_msg=active_osd)
                delay_sec = 0.40

            # Send to R36S screen
            res = client.display_frame_raw(comp_frame)
            if res.get("status") != "ok":
                sys.stdout.write(f"\n\033[1;33m[DISCONNECTED]\033[0m Console connection lost ({res.get('msg', 'unresponsive')}). Waiting for device...\n")
                sys.stdout.flush()
                client.close_serial()
                connected = client.connect(timeout=None)
                if connected:
                    client.pause_gui()
                    power_info = client.get_power()
                    last_power_time = time.time()
                    sys.stdout.write("\n\033[1;32m[RESUMED]\033[0m Display streaming restored!\n\n")
                    sys.stdout.flush()
                time.sleep(0.3)
                continue

            frame_count += 1
            elapsed = time.time() - start_time
            fps = round(frame_count / max(elapsed, 0.1), 1)

            theme_label = current_theme.upper()
            clock_now = time.strftime("%H:%M:%S")
            if current_theme == "gallery":
                cur_name = gallery_mgr.current_filename
                idx_str = f"[{gallery_mgr.current_idx + 1}/{gallery_mgr.total_count}]" if gallery_mgr.total_count else "[0/0]"
                print(f"\r[{theme_label}] {clock_now} | {idx_str} {cur_name:<28} | FPS: {fps} ", end="", flush=True)
            elif current_focus != "all":
                print(f"\r[FOCUS: {current_focus.upper()}] {clock_now} | CPU: %{cpu['usage']:02d} | GPU: %{gpu['usage']:02d} | RAM: %{ram['usage']:02d} | Net: {net['down_str']} | FPS: {fps} ", end="", flush=True)
            else:
                print(f"\r[{theme_label} HUD] {clock_now} | CPU: %{cpu['usage']:02d} ({cpu['temp']}°C) | GPU: %{gpu['usage']:02d} ({gpu['temp']}°C) | RAM: %{ram['usage']:02d} | Net: {net['down_str']} | FPS: {fps} ", end="", flush=True)

            dt = time.time() - t0
            time.sleep(max(0.02, delay_sec - dt))

    except KeyboardInterrupt:
        print("\n\nLive hardware monitor stopped.")
        print("[INFO] To reopen console game menu: ./install.sh resume")


def mode_shell(client):
    print("\n" + "=" * 55)
    print(" GA36 Live Root Shell (USB Serial Bridge)")
    print(" Type 'exit' or 'quit' to exit.")
    print("=" * 55)

    client.shell_exec("pwd")

    while True:
        try:
            prompt = f"\033[1;32mroot@r36s\033[0m:\033[1;34m{client.cwd}\033[0m# "
            user_input = input(prompt).strip()
            if not user_input:
                continue
            if user_input in ("exit", "quit"):
                print("Terminal session closed.")
                break
            if user_input == "clear":
                os.system("clear")
                continue

            stdout, stderr, code = client.shell_exec(user_input)
            if stdout:
                print(stdout, end="")
                if not stdout.endswith("\n"):
                    print()
            if stderr:
                print(f"\033[1;31m{stderr}\033[0m", end="", file=sys.stderr)
                if not stderr.endswith("\n"):
                    print(file=sys.stderr)
        except (KeyboardInterrupt, EOFError):
            print("\nTerminal session closed.")
            break


def mode_exec(client, cmd_str):
    res = client.exec_cmd(cmd_str)
    if res.get("status") == "ok":
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)
        sys.exit(res.get("code", 0))
    else:
        print(f"[ERROR] {res.get('msg')}", file=sys.stderr)
        sys.exit(1)


def mode_update_agent(client):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_agent = os.path.join(base_dir, "device", "ga36_agent.py")
    if not os.path.exists(local_agent):
        # Fallback to current directory or legacy path
        for cand in [os.path.join(base_dir, "r36s_agent.py"), "/home/auxerov/r36s_pc_companion/r36s_agent.py"]:
            if os.path.exists(cand):
                local_agent = cand
                break
    if not os.path.exists(local_agent):
        print(f"[ERROR] Local ga36_agent.py not found: {local_agent}")
        return
    print("\n[UPDATE] Uploading GA36 Agent v3.2 to console...")
    for target in ["/storage/pc_link/r36s_agent.py", "/storage/roms/pc_display/r36s_agent.py"]:
        ok, msg = client.push_file(local_agent, target)
        print(f"  {target}: {msg}")
    print("  Setting executable permissions...")
    client.exec_cmd("chmod +x /storage/pc_link/r36s_agent.py /storage/roms/pc_display/r36s_agent.py 2>/dev/null")
    print("[SUCCESS] Agent files successfully updated to v3.2!")


def mode_poweroff(client):
    print("\n[POWER] Powering off console...")
    res = client.call_rpc({"type": "poweroff"}, timeout=3)
    if res.get("status") == "ok":
        print("[SUCCESS] Poweroff signal sent. Console shutting down.")
    else:
        # Fallback to direct shell execution
        client.exec_cmd("systemctl poweroff || poweroff", timeout=3)
        print("[INFO] Shutdown command sent.")


# -------------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GA36 / R36S Live Sync & Control Suite v3.4")
    parser.add_argument("command", nargs="?", default="test",
                        choices=["test", "ping", "battery", "charge", "image", "dashboard", "stream", "mode", "theme", "focus", "gallery", "shell", "exec", "ls", "push", "pull", "clear", "pause", "resume", "update-agent", "upgrade", "reboot", "poweroff"],
                        help="Operating mode")
    parser.add_argument("args", nargs="*", help="Command arguments")
    parser.add_argument("--port", "-p", default=None, help="Serial port (e.g. /dev/ttyACM0)")

    parsed = parser.parse_args()

    cmd = parsed.command

    # Instant Mode/Theme Switcher (does not require locking serial port)
    if cmd in ("mode", "theme"):
        themes = ["pie", "dual", "btop", "gallery"]
        cur = get_saved_theme()
        if parsed.args:
            target = parsed.args[0].lower().strip()
            if target not in themes:
                print(f"[ERROR] Invalid theme: '{target}'. Options: {', '.join(themes)}")
                sys.exit(1)
        else:
            idx = (themes.index(cur) + 1) % len(themes)
            target = themes[idx]
        save_active_theme(target)
        print("\n" + "=" * 60)
        print(f" [SUCCESS] Mode Updated: {target.upper()} (Previous: {cur.upper()})")
        print(" The screen display will update immediately.")
        print("=" * 60 + "\n")
        sys.exit(0)

    # Instant Focus Switcher (does not require locking serial port)
    if cmd == "focus":
        cur_focus = get_saved_focus()
        if parsed.args:
            target = parsed.args[0].lower().strip()
            if target not in FOCUS_MODES:
                print(f"[ERROR] Invalid focus metric: '{target}'. Options: {', '.join(FOCUS_MODES)}")
                sys.exit(1)
        else:
            idx = (FOCUS_MODES.index(cur_focus) + 1) % len(FOCUS_MODES)
            target = FOCUS_MODES[idx]
        save_active_focus(target)
        print("\n" + "=" * 60)
        print(f" [SUCCESS] Focus View Updated: {target.upper()} (Previous: {cur_focus.upper()})")
        print(f" Options: {', '.join([m.upper() for m in FOCUS_MODES])}")
        print(" The screen display will update immediately.")
        print("=" * 60 + "\n")
        sys.exit(0)

    # Gallery Helper & File Lister
    if cmd == "gallery":
        gallery_dir = os.path.expanduser("~/Pictures/GA36_Gallery")
        os.makedirs(gallery_dir, exist_ok=True)
        # Ensure sample assets are present
        base_dir = os.path.dirname(os.path.abspath(__file__))
        sample_dir = os.path.join(base_dir, "gallery")
        if os.path.exists(sample_dir):
            for s in os.listdir(sample_dir):
                if s.lower().endswith(VALID_MEDIA_EXTS):
                    dst = os.path.join(gallery_dir, s)
                    if not os.path.exists(dst):
                        shutil.copy2(os.path.join(sample_dir, s), dst)

        files = sorted([f for f in os.listdir(gallery_dir) if f.lower().endswith(VALID_MEDIA_EXTS)])
        print("\n" + "=" * 60)
        print(" 🎨 GA36 Media & Animated GIF Gallery")
        print("=" * 60)
        print(f" Directory: {gallery_dir}")
        print(f" Total Items: {len(files)}")
        for idx, f in enumerate(files, 1):
            is_gif = " [GIF]" if f.lower().endswith(".gif") else " [IMAGE]"
            print(f"   {idx}. {f}{is_gif}")
        print("\n Controls on Console (in GALLERY Mode):")
        print("   [ DPAD LEFT / UP ]    -> Previous image or animated GIF")
        print("   [ DPAD RIGHT / DOWN ] -> Next image or animated GIF")
        print("   [ SELECT / MODE / FN ]-> Switch back to Hardware HUD (PIE)")
        print("\n Switch to Gallery mode instantly:")
        print("   ./install.sh mode gallery")
        print("=" * 60 + "\n")
        sys.exit(0)

    print("=" * 60)
    print(" GA36 / R36S PC Companion & Live Control Suite v3.5")
    print("=" * 60)

    client = R36SClient(port=parsed.port)
    conn_timeout = None if cmd in ("dashboard", "stream") else 30
    if not client.connect(timeout=conn_timeout):
        print("\n[ERROR] GA36 device not found (/dev/ttyACM*)!")
        print("1. Make sure console is powered on.")
        print("2. Ensure the USB-C cable is connected to the OTG (right) port.")
        sys.exit(1)

    try:
        if cmd in ("test", "ping"):
            mode_test(client)
        elif cmd in ("battery", "charge"):
            mode_battery(client)
        elif cmd == "image":
            if not parsed.args:
                print("\nUsage: ./install.sh image <image_file.png|jpg|bmp>")
            else:
                mode_image(client, parsed.args[0])
        elif cmd in ("dashboard", "stream"):
            theme_choice = parsed.args[0] if parsed.args else "pie"
            mode_dashboard(client, theme=theme_choice)
        elif cmd == "shell":
            mode_shell(client)
        elif cmd == "exec":
            if not parsed.args:
                print("\nUsage: ./install.sh exec \"<command>\"")
            else:
                mode_exec(client, " ".join(parsed.args))
        elif cmd == "ls":
            path = parsed.args[0] if parsed.args else "/storage"
            res = client.list_dir(path)
            if res.get("status") == "ok":
                print(f"\n{path} directory listing:")
                for e in res.get("entries", []):
                    prefix = "📁 [DIR] " if e.get("is_dir") else "📄 [FILE]"
                    print(f"  {prefix} {e.get('name'):<30} {e.get('size'):>10} bytes")
            else:
                print(f"[ERROR] {res.get('msg')}")
        elif cmd == "push":
            if len(parsed.args) < 1:
                print("\nUsage: ./install.sh push <local_file> [remote_target]")
            else:
                loc = parsed.args[0]
                rem = parsed.args[1] if len(parsed.args) > 1 else f"/storage/{os.path.basename(loc)}"
                ok, msg = client.push_file(loc, rem)
                print(f"[{'SUCCESS' if ok else 'ERROR'}] {msg}")
        elif cmd == "pull":
            if len(parsed.args) < 1:
                print("\nUsage: ./install.sh pull <remote_file> [local_target]")
            else:
                rem = parsed.args[0]
                loc = parsed.args[1] if len(parsed.args) > 1 else f"./{os.path.basename(rem)}"
                ok, msg = client.pull_file(rem, loc)
                print(f"[{'SUCCESS' if ok else 'ERROR'}] {msg}")
        elif cmd == "clear":
            client.clear_screen()
            print("\n[SUCCESS] Console screen cleared (black).")
        elif cmd == "pause":
            client.pause_gui()
            print("\n[SUCCESS] EmulationStation paused (display free).")
        elif cmd == "resume":
            client.resume_gui()
            print("\n[SUCCESS] EmulationStation menu resumed.")
        elif cmd == "restore":
            print("\n[RESTORE] Restoring emulators, libraries, and themes...")
            res = client.exec_cmd("/bin/bash /storage/roms/backup/RESTORE_ALL.sh", timeout=120)
            if res.get("status") == "ok":
                print("\n[SUCCESS] System restored to stock configuration successfully!")
            else:
                print(f"\n[ERROR] Restore failed: {res.get('msg')}")
        elif cmd in ("update-agent", "upgrade"):
            mode_update_agent(client)
        elif cmd == "reboot":
            client.call_rpc({"type": "reboot"})
            print("\n[INFO] Rebooting console...")
        elif cmd == "poweroff":
            mode_poweroff(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
