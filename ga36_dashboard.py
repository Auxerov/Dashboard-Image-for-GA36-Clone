#!/usr/bin/env python3
"""
R36S PC Companion & Live Control Suite v3.0
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
from PIL import Image, ImageDraw, ImageFont

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
    info = {"title": "Müzik çalmıyor", "artist": "", "playing": False}
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
                info["title"] = title if title else "Medya"
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
            print(f"\n[BAĞLANDI] R36S TCP Ağ Portu: {self.port}")
            return True
        except Exception:
            return False

    def reconnect(self, timeout=None):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.pending_responses.clear()
        return self.connect(timeout=timeout)

    def connect(self, timeout=30):
        start = time.time()
        while self.running and (timeout is None or (time.time() - start < timeout)):
            # 1. Try Serial
            p = self.find_port()
            if p:
                try:
                    s = serial.Serial(p, 115200, timeout=0.5, write_timeout=1.5)
                    s.dtr = True
                    s.rts = True
                    s.reset_input_buffer()
                    s.reset_output_buffer()
                    time.sleep(0.1)
                    self.ser = s
                    self.port = p
                    self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
                    self.reader_thread.start()
                    print(f"\n[BAĞLANDI] R36S Seri Portu: {self.port}")
                    return True
                except Exception:
                    pass

            # 2. Try TCP
            if self.try_tcp_connect():
                return True

            if timeout is not None:
                rem = max(0, int(timeout - (time.time() - start)))
                print(f"\rR36S cihazı aranıyor (/dev/ttyACM*)... (Kalan: {rem:02d}s) ", end="", flush=True)
            else:
                print("\rR36S cihazının açılması/bağlanması bekleniyor (/dev/ttyACM*)... ", end="", flush=True)
            time.sleep(0.5)
        print()
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
        print(f"\n[BUTON BASILDI] R36S Donanım Tuşu: {btn_name}")
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

    def ping(self):
        res = self.call_rpc({"type": "ping"}, timeout=5)
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
            return False, f"Yerel dosya bulunamadı: {local_path}"
        try:
            with open(local_path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode("ascii")
            res = self.call_rpc({"type": "write_file", "path": remote_path, "content_b64": b64}, timeout=30)
            if res.get("status") == "ok":
                return True, f"Yüklendi: {len(data)} bayt -> {remote_path}"
            return False, f"Hata: {res.get('msg')}"
        except Exception as e:
            return False, str(e)

    def pull_file(self, remote_path, local_path):
        res = self.call_rpc({"type": "read_file", "path": remote_path}, timeout=30)
        if res.get("status") != "ok":
            return False, f"Hata: {res.get('msg')}"
        try:
            data = base64.b64decode(res.get("content_b64", ""))
            os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(data)
            return True, f"İndirildi: {len(data)} bayt -> {local_path}"
        except Exception as e:
            return False, str(e)

    def list_dir(self, remote_path):
        return self.call_rpc({"type": "ls", "path": remote_path})

    def close(self):
        self.running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


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


def render_pie_dashboard(cpu, gpu, ram, net, disk, media, power_info):
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

    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


def render_dual_dashboard(cpu, gpu, ram, net, disk, media, power_info):
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
    p_str = f"[⚡ OTG ŞARJ: {cap}% ({v}V)]" if is_charging else f"[BATARYA: {cap}% ({v}V)]"
    draw.text((435, 12), p_str, fill=C_GREEN if is_charging else C_WHITE, font=FONT_BOLD)

    # 4 Quadrants
    # Q1: CPU (10, 42, 315, 222)
    draw.rectangle([10, 42, 315, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    temp_pct = min(100, int((cpu["temp"] / 90.0) * 100))
    draw_dual_pie(draw, 88, 132, 52, 40, cpu["usage"], temp_pct, C_CYAN, (0, 180, 216), title="CPU", val_str=f"{cpu['usage']}%", subtitle=f"{cpu['temp']}°C // {cpu['freq']}G")
    draw.text((158, 62), f"{cpu['name']}", fill=C_CYAN, font=FONT_BOLD)
    draw.text((158, 88), f"Dış Halka : %{cpu['usage']} Yük", fill=C_WHITE, font=FONT_REG)
    draw.text((158, 110), f"İç Halka  : {cpu['temp']}°C Isı", fill=(0, 180, 216), font=FONT_REG)
    draw.text((158, 132), f"Saat Hızı : {cpu['freq']} GHz", fill=C_DIM, font=FONT_REG)
    draw.text((158, 154), f"Çekirdek  : {cpu['cores']} Thread", fill=C_DIM, font=FONT_SMALL)

    # Q2: GPU (325, 42, 630, 222)
    draw.rectangle([325, 42, 630, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    vram_pct = int((gpu["vram_used"] / max(gpu["vram_total"], 0.1)) * 100)
    draw_dual_pie(draw, 403, 132, 52, 40, gpu["usage"], vram_pct, C_PINK, (255, 110, 180), title="GPU", val_str=f"{gpu['usage']}%", subtitle=f"{gpu['temp']}°C // RTX")
    draw.text((473, 62), f"{gpu['name']}", fill=C_PINK, font=FONT_BOLD)
    draw.text((473, 88), f"Dış Halka : %{gpu['usage']} Yük", fill=C_WHITE, font=FONT_REG)
    draw.text((473, 110), f"İç Halka  : %{vram_pct} VRAM", fill=(255, 110, 180), font=FONT_REG)
    draw.text((473, 132), f"Sıcaklık  : {gpu['temp']}°C", fill=C_DIM, font=FONT_REG)
    draw.text((473, 154), f"Bellek    : {gpu['vram_used']}/{gpu['vram_total']} GB", fill=C_DIM, font=FONT_SMALL)

    # Q3: RAM (10, 230, 315, 398)
    draw.rectangle([10, 230, 315, 398], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw_dual_pie(draw, 88, 314, 52, 40, ram["usage"], ram["swap_usage"], C_GREEN, (0, 180, 140), title="RAM", val_str=f"{ram['usage']}%", subtitle=f"{ram['used_gb']}G")
    draw.text((158, 250), "BELLEK VE SWAP", fill=C_GREEN, font=FONT_BOLD)
    draw.text((158, 276), f"Dış Halka : %{ram['usage']} RAM", fill=C_WHITE, font=FONT_REG)
    draw.text((158, 298), f"İç Halka  : %{ram['swap_usage']} SWAP", fill=(0, 180, 140), font=FONT_REG)
    draw.text((158, 320), f"Kullanılan: {ram['used_gb']}/{ram['total_gb']} GB", fill=C_DIM, font=FONT_REG)
    draw.text((158, 342), f"SWAP      : {ram['swap_used_gb']} GB", fill=C_DIM, font=FONT_SMALL)

    # Q4: DISK & NET (325, 230, 630, 398)
    draw.rectangle([325, 230, 630, 398], fill=C_CARD_BG, outline=C_CARD_BORDER)
    net_activity = min(100, int((net["down_mb"] / 10.0) * 100))
    draw_dual_pie(draw, 403, 314, 52, 40, disk["usage"], net_activity, C_ORANGE, C_CYAN, title="DISK", val_str=f"{disk['usage']}%", subtitle=f"{disk['used_gb']}G")
    draw.text((473, 250), "DEPOLAMA VE AĞ", fill=C_ORANGE, font=FONT_BOLD)
    draw.text((473, 276), f"Dış Halka : %{disk['usage']} Disk", fill=C_WHITE, font=FONT_REG)
    draw.text((473, 298), f"İndirme   : ↓ {net['down_str']}", fill=C_CYAN, font=FONT_BOLD)
    draw.text((473, 320), f"Yükleme   : ↑ {net['up_str']}", fill=C_ORANGE, font=FONT_BOLD)
    draw.text((473, 342), f"Depolama  : {disk['used_gb']}/{disk['total_gb']} GB", fill=C_DIM, font=FONT_SMALL)

    # Bottom Bar
    draw.rectangle([10, 406, 630, 474], fill=C_CARD_BG, outline=C_CARD_BORDER)
    m_icon = "▶ " if media["playing"] else "■ "
    m_text = f"{m_icon}{media['title']}"
    if media["artist"]:
        m_text += f" - {media['artist']}"
    draw.text((22, 414), m_text[:50], fill=C_CYAN if media["playing"] else C_DIM, font=FONT_BOLD)
    draw.text((22, 438), "[A] Oynat/Duraklat  [B] Sonraki  [X] Mikrofon  [Y] Ekran Görüntüsü  [SELECT] Tema Değiştir", fill=C_DIM, font=FONT_SMALL)

    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


def render_btop_dashboard(cpu, gpu, ram, net, disk, media, power_info):
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
        p_str = f"[⚡ OTG ŞARJ: {cap}% ({v}V)]"
        p_col = C_GREEN
    else:
        p_str = f"[BATARYA: {cap}% ({v}V)]"
        p_col = C_YELLOW if cap < 30 else C_WHITE
    draw.text((440, 12), p_str, fill=p_col, font=FONT_BOLD)

    # 2. CPU Card (Top Left: 10, 42, 315, 222)
    draw.rectangle([10, 42, 315, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((22, 50), f"CPU: {cpu['name']}", fill=C_CYAN, font=FONT_BOLD)
    draw.text((250, 50), f"{cpu['usage']}%", fill=C_WHITE, font=FONT_BOLD)
    draw_gauge_bar(draw, 22, 70, 275, 14, cpu["usage"], C_CYAN)

    draw.text((22, 94), f"Sıcaklık  : {cpu['temp']}°C", fill=C_WHITE, font=FONT_REG)
    draw.text((170, 94), f"Saat Hızı: {cpu['freq']} GHz", fill=C_WHITE, font=FONT_REG)
    draw.text((22, 114), f"Çekirdek  : {cpu['cores']} Thread", fill=C_DIM, font=FONT_REG)
    draw.text((22, 138), "CPU Yük Geçmişi:", fill=C_DIM, font=FONT_SMALL)
    draw_sparkline(draw, 22, 155, 275, 50, cpu.get("history", []), 100, C_CYAN)

    # 3. GPU Card (Top Right: 325, 42, 630, 222)
    draw.rectangle([325, 42, 630, 222], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((337, 50), f"GPU: {gpu['name']}", fill=C_PINK, font=FONT_BOLD)
    draw.text((565, 50), f"{gpu['usage']}%", fill=C_WHITE, font=FONT_BOLD)
    draw_gauge_bar(draw, 337, 70, 275, 14, gpu["usage"], C_PINK)

    draw.text((337, 94), f"Sıcaklık  : {gpu['temp']}°C", fill=C_WHITE, font=FONT_REG)
    vram_pct = int((gpu["vram_used"] / max(gpu["vram_total"], 0.1)) * 100)
    draw.text((337, 114), f"VRAM      : {gpu['vram_used']} / {gpu['vram_total']} GB ({vram_pct}%)", fill=C_WHITE, font=FONT_REG)
    draw_gauge_bar(draw, 337, 134, 275, 10, vram_pct, C_PINK)
    draw.text((337, 150), "GPU Yük Geçmişi:", fill=C_DIM, font=FONT_SMALL)
    draw_sparkline(draw, 337, 166, 275, 40, gpu.get("history", []), 100, C_PINK)

    # 4. RAM & SWAP Card (Bottom Left: 10, 230, 315, 396)
    draw.rectangle([10, 230, 315, 396], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((22, 238), "BELLEK (RAM & SWAP)", fill=C_GREEN, font=FONT_BOLD)
    draw.text((22, 260), f"RAM: {ram['used_gb']} / {ram['total_gb']} GB ({ram['usage']}%)", fill=C_WHITE, font=FONT_REG)
    draw_gauge_bar(draw, 22, 280, 275, 14, ram["usage"], C_GREEN)

    draw.text((22, 308), f"SWAP: {ram['swap_used_gb']} / {ram['swap_total_gb']} GB ({ram['swap_usage']}%)", fill=C_DIM, font=FONT_REG)
    draw_gauge_bar(draw, 22, 328, 275, 10, ram["swap_usage"], (0, 180, 216))

    draw.text((22, 355), f"Kullanılabilir: {round(ram['total_gb'] - ram['used_gb'], 1)} GB", fill=C_DIM, font=FONT_SMALL)

    # 5. NETWORK & DISK Card (Bottom Right: 325, 230, 630, 396)
    draw.rectangle([325, 230, 630, 396], fill=C_CARD_BG, outline=C_CARD_BORDER)
    draw.text((337, 238), "AĞ VE DEPOLAMA", fill=C_ORANGE, font=FONT_BOLD)

    draw.text((337, 262), f"İndirme (Down) : ↓ {net['down_mb']} MB/s", fill=C_CYAN, font=FONT_REG)
    draw.text((337, 282), f"Yükleme (Up)   : ↑ {net['up_mb']} MB/s", fill=C_ORANGE, font=FONT_REG)

    draw.text((337, 312), f"Depolama (/)   : {disk['used_gb']} / {disk['total_gb']} GB ({disk['usage']}%)", fill=C_WHITE, font=FONT_REG)
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
    btn_guide = "[A] Oynat/Duraklat   [B] Sonraki Parça   [X] Mikrofon   [Y] Ekran Görüntüsü"
    draw.text((22, 436), btn_guide, fill=C_DIM, font=FONT_SMALL)

    # Convert to 32-bit BGRA
    r, g, b = img.split()
    bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (640, 480), 255)))
    return zlib.compress(bgra.tobytes(), level=1)


# -------------------------------------------------------------
# Command Modes
# -------------------------------------------------------------
def mode_test(client):
    print("\n[TEST] R36S Bağlantı ve Durum Analizi Yapılıyor...")
    res = client.ping()
    if res.get("status") == "ok":
        p = res.get("power", {})
        print("\n" + "=" * 55)
        print(" [BAŞARILI] R36S USB İLETİŞİM PROTOKOLÜ VE AJAN AKTİF!")
        print("=" * 55)
        print(f" Ajan Sürümü  : {res.get('agent')} v{res.get('version')}")
        print(f" Yetki        : root ({res.get('user')})")
        print(f" Konsol Saati : {res.get('time')}")
        print(f" Çalışma Süresi: {int(res.get('uptime_sec', 0))} saniye")
        print(f" Ekran        : {res.get('screen', {}).get('width')}x{res.get('screen', {}).get('height')} IPS (32-bit)")
        print(f" OTG Şarj     : {'AKTİF (USB Gücü Alınıyor)' if p.get('online') else 'Pilden Çalışıyor'}")
        print(f" Batarya      : %{p.get('capacity')} ({p.get('status')}) - {p.get('voltage_v')}V")
        print("=" * 55)

        if res.get("version") != "3.2":
            print("\n[OTOMATİK GÜNCELLEME] Ajan v3.2 sürümüne yükseltiliyor (Güç senkronizasyonu & ultra temiz göstergeler)...")
            mode_update_agent(client)

        print("\nKullanabileceğiniz Komutlar:")
        print("  ./r36s.sh dashboard       -> Canlı 'btop' donanım ekranını başlat (titremesiz)")
        print("  ./r36s.sh image <dosya>   -> Ekrana özel resim / duvar kağıdı bas ve sabitle")
        print("  ./r36s.sh resume          -> EmulationStation oyun menüsünü geri getir")
        print("  ./r36s.sh battery         -> Şarj ve güç durumunu detaylı izle")
        print("  ./r36s.sh restore         -> Silinen emülatör ve temaları yedekten geri yükle")
        print("  ./r36s.sh shell           -> Tam yetkili canlı root terminali")
        print("  ./r36s.sh clear           -> Konsol ekranını temizle")
    else:
        print(f"\n[HATA] R36S yanıt vermedi: {res.get('msg')}")
        print("\nÇözüm Adımları:")
        print("  1. Type-C OTG kablosunu bilgisayardan çıkarıp tekrar takın (USB arabelleği sıfırlansın).")
        print("  2. Konsolu EmulationStation üzerinden yeniden başlatın: (START tuşu -> QUIT -> RESTART SYSTEM).")


def mode_battery(client):
    print("\n" + "=" * 50)
    print(" R36S Güç ve Şarj Denetimi (AXP223 PMIC)")
    print("=" * 50)
    p = client.get_power()
    print(f" OTG Girişi       : {'Bağlı ve Aktif' if p.get('online') else 'Bağlantı Yok'}")
    print(f" Şarj Durumu      : {p.get('status')}")
    print(f" Batarya Seviyesi : %{p.get('capacity')}")
    print(f" Gerilim (Volt)   : {p.get('voltage_v')} V")
    print(f" Akım (Miliamper) : {p.get('current_ma')} mA")
    if p.get("temp_c"):
        print(f" Pil Sıcaklığı    : {p.get('temp_c')} °C")
    print("=" * 50)


def mode_image(client, image_path):
    if not os.path.exists(image_path):
        print(f"\n[HATA] Belirtilen resim dosyası bulunamadı: {image_path}")
        return
    print(f"\n[RESİM] '{image_path}' yükleniyor ve 640x480 formatına işleniyor...")
    try:
        compressed = prepare_image_frame(image_path)
        print(f"  Görüntü sıkıştırıldı ({len(compressed)} bayt). Ekrana aktarılıyor...")
        client.pause_gui()
        res = client.display_frame_raw(compressed)
        if res.get("status") == "ok":
            print("\n[BAŞARILI] Resim R36S ekranına anında basıldı ve sabitlendi! (640x480 IPS)")
            print("  Konsol menüsüne dönmek için Ctrl+C tuşlayın...")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\nEmulationStation menüsüne dönülüyor...")
                client.resume_gui()
        else:
            print(f"\n[HATA] Resim ekrana verilemedi: {res.get('msg')}")
    except Exception as e:
        print(f"\n[HATA] Resim işleme hatası: {e}")


def mode_dashboard(client, theme="pie"):
    current_theme = theme.lower() if theme in ("pie", "dual", "btop") else "pie"

    print("\n" + "=" * 60)
    print(" R36S CYBER PIE / CANLI DONANIM HUD AKTİF!")
    print(f" Aktif Tema: {current_theme.upper()} (Dairesel Pasta Göstergeler)")
    print(" R36S ekranında gerçek zamanlı veriler akmaktadır.")
    print(" Fiziksel Gamepad Tuşları:")
    print("   [ A ]      -> Medya Oynat / Duraklat")
    print("   [ B ]      -> Sonraki Şarkı")
    print("   [ X ]      -> Mikrofonu Sustur / Aç")
    print("   [ Y ]      -> Ekran Görüntüsü Al")
    print("   [ SELECT ] -> Temayı Değiştir (PIE ➔ DUAL ➔ BTOP)")
    print(" Çıkmak için terminalde Ctrl+C tuşlarına basın.")
    print("=" * 60 + "\n")

    print("[BİLGİ] Titremeyi önlemek için EmulationStation duraklatılıyor...")
    client.pause_gui()

    def handle_btn(btn_name):
        nonlocal current_theme
        if btn_name in ("SELECT", "KEY_314"):
            themes = ["pie", "dual", "btop"]
            idx = (themes.index(current_theme) + 1) % len(themes)
            current_theme = themes[idx]
            print(f"\n[TEMA DEĞİŞTİ] Yeni HUD Teması: {current_theme.upper()}    ")
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

            # Gather host metrics
            cpu = get_cpu_info()
            gpu = get_gpu_info()
            ram = get_ram_info()
            net = get_net_info()
            disk = get_disk_info()
            media = get_media_info()

            # Render selected theme
            if current_theme == "dual":
                comp_frame = render_dual_dashboard(cpu, gpu, ram, net, disk, media, power_info)
            elif current_theme == "btop":
                comp_frame = render_btop_dashboard(cpu, gpu, ram, net, disk, media, power_info)
            else:
                comp_frame = render_pie_dashboard(cpu, gpu, ram, net, disk, media, power_info)

            # Send to R36S screen
            res = client.display_frame_raw(comp_frame)
            if res.get("status") != "ok":
                print(f"\r[BAĞLANTI YENİLENİYOR] Port yanıt vermedi ({res.get('msg')}), yeniden bağlanılıyor... ", end="", flush=True)
                client.reconnect(timeout=2)
                time.sleep(0.3)
                continue

            frame_count += 1
            elapsed = time.time() - start_time
            fps = round(frame_count / max(elapsed, 0.1), 1)

            theme_label = current_theme.upper()
            clock_now = time.strftime("%H:%M:%S")
            print(f"\r[{theme_label} HUD] {clock_now} | CPU: %{cpu['usage']:02d} ({cpu['temp']}°C) | GPU: %{gpu['usage']:02d} ({gpu['temp']}°C) | RAM: %{ram['usage']:02d} | Net: {net['down_str']} | FPS: {fps} ", end="", flush=True)

            dt = time.time() - t0
            time.sleep(max(0.05, 0.40 - dt))  # ~2.5 FPS Ultra Eco-Smooth (R36S CPU < %1.5, ısınmaz, bataryayı tüketmez!)

    except KeyboardInterrupt:
        print("\n\nCanlı donanım monitörü sonlandırıldı.")
        print("[BİLGİ] Konsol oyun menüsünü geri açmak isterseniz: ./r36s.sh resume")


def mode_shell(client):
    print("\n" + "=" * 55)
    print(" R36S Canlı Root Terminali (USB Serial Köprüsü)")
    print(" Çıkmak için 'exit' veya 'quit' yazın.")
    print("=" * 55)

    client.shell_exec("pwd")

    while True:
        try:
            prompt = f"\033[1;32mroot@r36s\033[0m:\033[1;34m{client.cwd}\033[0m# "
            user_input = input(prompt).strip()
            if not user_input:
                continue
            if user_input in ("exit", "quit"):
                print("Terminal oturumu sonlandırıldı.")
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
            print("\nTerminal oturumu kapatıldı.")
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
        print(f"[HATA] {res.get('msg')}", file=sys.stderr)
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
        print(f"[HATA] Yerel ga36_agent.py bulunamadı: {local_agent}")
        return
    print("\n[GÜNCELLEME] GA36/R36S Agent v3.2 konsola yükleniyor...")
    for target in ["/storage/pc_link/r36s_agent.py", "/storage/roms/pc_display/r36s_agent.py"]:
        ok, msg = client.push_file(local_agent, target)
        print(f"  {target}: {msg}")
    print("  Ajan izinleri ayarlanıyor...")
    client.exec_cmd("chmod +x /storage/pc_link/r36s_agent.py /storage/roms/pc_display/r36s_agent.py 2>/dev/null")
    print("[BAŞARILI] R36S Agent dosyaları v3.2 sürümüne güncellendi!")


def mode_poweroff(client):
    print("\n[GÜÇ] R36S konsolu kapatılıyor...")
    res = client.call_rpc({"type": "poweroff"}, timeout=3)
    if res.get("status") == "ok":
        print("[BAŞARILI] R36S kapatma sinyali iletildi. Cihaz güvenle kapanıyor.")
    else:
        # Fallback to direct shell execution
        client.exec_cmd("systemctl poweroff || poweroff", timeout=3)
        print("[BİLGİ] Kapatma komutu iletildi.")


# -------------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="R36S Live Sync & Control Suite v3.0")
    parser.add_argument("command", nargs="?", default="test",
                        choices=["test", "ping", "battery", "charge", "image", "dashboard", "stream", "shell", "exec", "ls", "push", "pull", "clear", "pause", "resume", "update-agent", "upgrade", "reboot", "poweroff"],
                        help="Çalıştırma modu")
    parser.add_argument("args", nargs="*", help="Komut parametreleri")
    parser.add_argument("--port", "-p", default=None, help="Seri port (örn: /dev/ttyACM0)")

    parsed = parser.parse_args()

    print("=" * 60)
    print(" R36S PC Companion & Live Control Suite v3.0")
    print("=" * 60)

    cmd = parsed.command
    client = R36SClient(port=parsed.port)
    conn_timeout = None if cmd in ("dashboard", "stream") else 30
    if not client.connect(timeout=conn_timeout):
        print("\n[HATA] R36S cihazı bulunamadı (/dev/ttyACM*)!")
        print("1. Konsolun açık olduğundan emin olun.")
        print("2. Type-C kablosunun R36S üzerindeki OTG (sağdaki) porta takılı olduğunu kontrol edin.")
        sys.exit(1)

    try:
        if cmd in ("test", "ping"):
            mode_test(client)
        elif cmd in ("battery", "charge"):
            mode_battery(client)
        elif cmd == "image":
            if not parsed.args:
                print("\nKullanım: ./r36s.sh image <resim_dosyası.png|jpg|bmp>")
            else:
                mode_image(client, parsed.args[0])
        elif cmd in ("dashboard", "stream"):
            theme_choice = parsed.args[0] if parsed.args else "pie"
            mode_dashboard(client, theme=theme_choice)
        elif cmd == "shell":
            mode_shell(client)
        elif cmd == "exec":
            if not parsed.args:
                print("\nKullanım: ./r36s.sh exec \"<komut>\"")
            else:
                mode_exec(client, " ".join(parsed.args))
        elif cmd == "ls":
            path = parsed.args[0] if parsed.args else "/storage"
            res = client.list_dir(path)
            if res.get("status") == "ok":
                print(f"\n{path} dizin içeriği:")
                for e in res.get("entries", []):
                    prefix = "📁 [DIR] " if e.get("is_dir") else "📄 [FILE]"
                    print(f"  {prefix} {e.get('name'):<30} {e.get('size'):>10} bayt")
            else:
                print(f"[HATA] {res.get('msg')}")
        elif cmd == "push":
            if len(parsed.args) < 1:
                print("\nKullanım: ./r36s.sh push <yerel_dosya> [uzak_hedef]")
            else:
                loc = parsed.args[0]
                rem = parsed.args[1] if len(parsed.args) > 1 else f"/storage/{os.path.basename(loc)}"
                ok, msg = client.push_file(loc, rem)
                print(f"[{'BAŞARILI' if ok else 'HATA'}] {msg}")
        elif cmd == "pull":
            if len(parsed.args) < 1:
                print("\nKullanım: ./r36s.sh pull <uzak_dosya> [yerel_hedef]")
            else:
                rem = parsed.args[0]
                loc = parsed.args[1] if len(parsed.args) > 1 else f"./{os.path.basename(rem)}"
                ok, msg = client.pull_file(rem, loc)
                print(f"[{'BAŞARILI' if ok else 'HATA'}] {msg}")
        elif cmd == "clear":
            client.clear_screen()
            print("\n[BAŞARILI] R36S ekranı temizlendi (siyah).")
        elif cmd == "pause":
            client.pause_gui()
            print("\n[BAŞARILI] EmulationStation duraklatıldı (ekran artık serbest).")
        elif cmd == "resume":
            client.resume_gui()
            print("\n[BAŞARILI] EmulationStation menüsü geri getirildi.")
        elif cmd == "restore":
            print("\n[GERİ YÜKLEME] Emülatörler, RetroArch kütüphaneleri ve temalar geri yükleniyor...")
            res = client.exec_cmd("/bin/bash /storage/roms/backup/RESTORE_ALL.sh", timeout=120)
            if res.get("status") == "ok":
                print("\n[BAŞARILI] Sistem yedeği eksiksiz olarak orijinal haline döndürüldü!")
            else:
                print(f"\n[HATA] Geri yükleme başarısız: {res.get('msg')}")
        elif cmd in ("update-agent", "upgrade"):
            mode_update_agent(client)
        elif cmd == "reboot":
            client.call_rpc({"type": "reboot"})
            print("\n[BİLGİ] R36S konsolu yeniden başlatılıyor...")
        elif cmd == "poweroff":
            mode_poweroff(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
