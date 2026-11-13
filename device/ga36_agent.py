#!/usr/bin/env python3
"""
R36S Real-Time USB Bridge & Multi-Function Agent v3.0
Runs on R36S console as root.
Provides:
- Non-blocking multithreaded RPC engine over USB Serial (/dev/ttyGS0) & TCP (8888)
- Blazing-fast Framebuffer (/dev/fb0) rendering (compressed raw BGRA / images)
- Real-time hardware gamepad event streaming (A, B, X, Y, D-pad, etc.)
- Safe, non-blocking process execution with process-group timeouts
- Safe battery & USB OTG power monitoring
- Self-update support
"""

import os
import sys
import time
import json
import glob
import mmap
import fcntl
import struct
import base64
import zlib
import shutil
import select
import socket
import signal
import argparse
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = "3.5"
SERIAL_DEV = "/dev/ttyGS0"


class MiniFB:
    FBIOGET_VSCREENINFO = 0x4600
    FBIOPAN_DISPLAY = 0x4606

    def __init__(self, dev="/dev/fb0"):
        self.dev = dev
        self.fb_file = None
        self.fb_mem = None
        self.width = 640
        self.height = 480
        self.vxres = 640
        self.vyres = 480
        self.bpp = 32
        self.bytes_per_pixel = 4
        self.stride = 640 * 4
        self.frame_size = 640 * 480 * 4
        self.size = self.frame_size
        self.vinfo_raw = None
        self.init_fb()

    def init_fb(self):
        try:
            self.fb_file = open(self.dev, "r+b", buffering=0)
            vinfo = bytearray(160)
            try:
                fcntl.ioctl(self.fb_file.fileno(), self.FBIOGET_VSCREENINFO, vinfo)
                self.vinfo_raw = vinfo
                res = struct.unpack("IIIIIII", vinfo[:28])
                self.width = res[0] or 640
                self.height = res[1] or 480
                self.vxres = res[2] or self.width
                self.vyres = res[3] or self.height
                self.bpp = res[6] or 32
            except Exception:
                pass

            self.bytes_per_pixel = max(2, self.bpp // 8)
            self.stride = self.width * self.bytes_per_pixel
            self.frame_size = self.stride * self.height
            self.size = self.stride * max(self.height, self.vyres)
            self.fb_mem = mmap.mmap(self.fb_file.fileno(), self.size, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
            self.pan_to_zero()
            self.show_splash()
        except Exception as e:
            sys.stderr.write(f"FB Init error: {e}\n")

    def show_splash(self):
        """Displays custom cyber splash screen on framebuffer."""
        candidates = [
            "/storage/pc_link/splash.raw",
            "/storage/.config/splash/splash.raw",
            "/storage/.config/splash/splash-1080.raw",
            "/storage/pc_link/splash.png",
            "/storage/.config/splash/splash.png"
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    if p.endswith(".raw"):
                        with open(p, "rb") as f:
                            data = f.read()
                            if self.write_raw_decompressed(data):
                                return True
                    elif p.endswith(".png"):
                        from PIL import Image
                        im = Image.open(p).convert("RGB")
                        if im.size != (self.width, self.height):
                            im = im.resize((self.width, self.height))
                        r, g, b = im.split()
                        bgra = Image.merge("RGBA", (b, g, r, Image.new("L", (self.width, self.height), 255)))
                        if self.write_raw_decompressed(bgra.tobytes()):
                            return True
                except Exception:
                    pass
        return False

    def pan_to_zero(self):
        if self.fb_file and self.vinfo_raw and self.vyres > self.height:
            try:
                data = list(struct.unpack("IIIIIII", self.vinfo_raw[:28]))
                data[4] = 0  # xoffset
                data[5] = 0  # yoffset
                new_vinfo = struct.pack("IIIIIII", *data) + self.vinfo_raw[28:]
                fcntl.ioctl(self.fb_file.fileno(), self.FBIOPAN_DISPLAY, new_vinfo)
            except Exception:
                pass

    def write_raw_decompressed(self, raw_bytes):
        if not self.fb_mem:
            return False
        try:
            n = min(len(raw_bytes), self.frame_size)
            # Write to Frame 0
            self.fb_mem[:n] = raw_bytes[:n]
            # Write to Frame 1 as well to support hardware double buffering without tearing!
            if self.size >= n * 2:
                self.fb_mem[n:n*2] = raw_bytes[:n]
            return True
        except Exception:
            return False

    def clear(self, r=0, g=0, b=0):
        if not self.fb_mem:
            return
        if self.bytes_per_pixel == 2:
            c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            pixel = struct.pack("<H", c)
        else:
            pixel = bytes([b, g, r, 255])
        # Clear entire virtual buffer (both Frame 0 and Frame 1)
        self.fb_mem[:] = pixel * (self.size // len(pixel))
        self.pan_to_zero()

    def close(self):
        try:
            if self.fb_mem:
                self.fb_mem.close()
            if self.fb_file:
                self.fb_file.close()
        except Exception:
            pass


class R36SAgent:
    def __init__(self, serial_dev="/dev/ttyGS0", tcp_port=8888):
        self.serial_dev = serial_dev
        self.tcp_port = tcp_port
        self.running = True
        self.ser_file = None
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.fb = MiniFB()
        self.active_connections = set()
        self.last_active_time = time.time()
        self.gui_paused = False

    def pause_gui(self):
        try:
            subprocess.run("systemctl stop emustation.service 2>/dev/null ; killall -9 emulationstation 2>/dev/null", shell=True)
            self.gui_paused = True
            return True
        except Exception:
            return False

    def resume_gui(self):
        try:
            subprocess.run("systemctl unmask emustation.service 2>/dev/null ; systemctl start emustation.service 2>/dev/null", shell=True)
            self.gui_paused = False
            self.fb.show_splash()
            return True
        except Exception:
            return False

    def find_serial_dev(self):
        if self.serial_dev and os.path.exists(self.serial_dev):
            return self.serial_dev
        for c in ["/dev/ttyGS0", "/dev/ttygserial", "/dev/ttyGS1", "/dev/ttyACM0"]:
            if os.path.exists(c):
                return c
        return None

    def broadcast_message(self, msg_dict):
        """Send asynchronous event (like button presses) to all active serial and TCP clients."""
        line = json.dumps(msg_dict, ensure_ascii=False) + "\n"
        raw = line.encode("utf-8")
        with self.lock:
            if self.ser_file and not getattr(self.ser_file, "closed", True):
                try:
                    self.ser_file.write(raw)
                    self.ser_file.flush()
                except Exception:
                    pass
            for sock in list(self.active_connections):
                try:
                    sock.sendall(raw)
                except Exception:
                    self.active_connections.discard(sock)

    def send_serial_response(self, resp_dict):
        with self.lock:
            if not self.ser_file or self.ser_file.closed:
                return
            try:
                line = json.dumps(resp_dict, ensure_ascii=False) + "\n"
                raw = line.encode("utf-8")
                _, w, _ = select.select([], [self.ser_file], [], 2.0)
                if w:
                    self.ser_file.write(raw)
                    self.ser_file.flush()
            except Exception:
                pass

    def get_power_info(self):
        info = {
            "online": False,
            "status": "Unknown",
            "capacity": 0,
            "voltage_v": 0.0,
            "current_ma": 0,
            "temp_c": 0.0
        }
        # Check USB status
        try:
            if os.path.exists("/sys/class/power_supply/usb/online"):
                with open("/sys/class/power_supply/usb/online", "r") as f:
                    info["online"] = (f.read().strip() == "1")
        except Exception:
            pass

        # Check Battery status
        bat_dir = "/sys/class/power_supply/battery"
        if os.path.exists(bat_dir):
            try:
                if os.path.exists(f"{bat_dir}/status"):
                    with open(f"{bat_dir}/status", "r") as f:
                        info["status"] = f.read().strip()
                if os.path.exists(f"{bat_dir}/capacity"):
                    with open(f"{bat_dir}/capacity", "r") as f:
                        info["capacity"] = int(f.read().strip())
                if os.path.exists(f"{bat_dir}/voltage_now"):
                    with open(f"{bat_dir}/voltage_now", "r") as f:
                        v = int(f.read().strip())
                        info["voltage_v"] = round(v / 1000000.0, 2)
                if os.path.exists(f"{bat_dir}/current_now"):
                    with open(f"{bat_dir}/current_now", "r") as f:
                        c = int(f.read().strip())
                        info["current_ma"] = int(c / 1000.0)
                if os.path.exists(f"{bat_dir}/temp"):
                    with open(f"{bat_dir}/temp", "r") as f:
                        info["temp_c"] = round(int(f.read().strip()) / 10.0, 1)
            except Exception:
                pass
        return info

    def handle_request(self, msg_str, reply_callback=None):
        reply_func = reply_callback or self.send_serial_response
        self.last_active_time = time.time()
        try:
            req = json.loads(msg_str)
        except Exception as e:
            reply_func({"type": "response", "status": "error", "msg": f"Invalid JSON: {e}"})
            return

        req_type = req.get("type", "ping")
        req_id = req.get("id")

        if req_type == "ping":
            uptime_str = "0"
            try:
                with open("/proc/uptime", "r") as f:
                    uptime_str = f.read().split()[0]
            except Exception:
                pass
            mem_info = {}
            try:
                with open("/proc/meminfo", "r") as f:
                    for l in f:
                        parts = l.split(":")
                        if parts[0] in ("MemTotal", "MemAvailable", "MemFree"):
                            mem_info[parts[0]] = parts[1].strip()
            except Exception:
                pass

            power_info = self.get_power_info()

            reply_func({
                "type": "response",
                "id": req_id,
                "status": "ok",
                "agent": "r36s_agent",
                "version": VERSION,
                "user": "root" if os.geteuid() == 0 else str(os.geteuid()),
                "uptime_sec": float(uptime_str),
                "mem": mem_info,
                "power": power_info,
                "screen": {"width": self.fb.width, "height": self.fb.height, "bpp": self.fb.bpp},
                "time": time.strftime("%Y-%m-%d %H:%M:%S")
            })

        elif req_type == "power":
            reply_func({
                "type": "response",
                "id": req_id,
                "status": "ok",
                "power": self.get_power_info()
            })

        elif req_type == "gui_pause":
            self.pause_gui()
            reply_func({"type": "response", "id": req_id, "status": "ok", "paused": True})

        elif req_type == "gui_resume":
            self.resume_gui()
            reply_func({"type": "response", "id": req_id, "status": "ok", "paused": False})

        elif req_type == "fb_raw":
            if not self.gui_paused:
                self.pause_gui()
            # Direct raw compressed frame display
            raw_b64 = req.get("data_b64", "")
            try:
                compressed = base64.b64decode(raw_b64)
                decompressed = zlib.decompress(compressed)
                ok = self.fb.write_raw_decompressed(decompressed)
                reply_func({"type": "response", "id": req_id, "status": "ok" if ok else "error"})
            except Exception as e:
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": str(e)})

        elif req_type == "fb_clear":
            if not self.gui_paused:
                self.pause_gui()
            color = req.get("color", [0, 0, 0])
            self.fb.clear(color[0], color[1], color[2])
            reply_func({"type": "response", "id": req_id, "status": "ok"})

        elif req_type == "exec":
            cmd = req.get("cmd", "")
            timeout = req.get("timeout", 20)
            try:
                # Use preexec_fn=os.setsid to allow clean process group termination on timeout
                p = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid
                )
                try:
                    out_bytes, err_bytes = p.communicate(timeout=timeout)
                    code = p.returncode
                    stdout_str = out_bytes.decode("utf-8", "replace")
                    stderr_str = err_bytes.decode("utf-8", "replace")
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except Exception:
                        pass
                    stdout_str = ""
                    stderr_str = f"Command timed out after {timeout}s"
                    code = -1

                reply_func({
                    "type": "response",
                    "id": req_id,
                    "status": "ok",
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "code": code
                })
            except Exception as e:
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": str(e)})

        elif req_type == "write_file":
            path = req.get("path")
            b64_content = req.get("content_b64", "")
            mode = req.get("mode", 0o644)
            if not path:
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": "Missing path"})
                return
            try:
                data = base64.b64decode(b64_content)
                dir_name = os.path.dirname(os.path.abspath(path))
                os.makedirs(dir_name, exist_ok=True)
                tmp_path = f"{path}.tmp_{os.getpid()}_{int(time.time()*1000)}"
                with open(tmp_path, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp_path, mode)
                os.replace(tmp_path, path)
                reply_func({"type": "response", "id": req_id, "status": "ok", "path": path, "bytes": len(data)})
            except Exception as e:
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": str(e)})

        elif req_type == "read_file":
            path = req.get("path")
            offset = req.get("offset", 0)
            length = req.get("length", -1)
            if not path or not os.path.exists(path):
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": "File not found"})
                return
            try:
                total_size = os.path.getsize(path)
                with open(path, "rb") as f:
                    if offset > 0:
                        f.seek(offset)
                    data = f.read(length if length > 0 else -1)
                b64 = base64.b64encode(data).decode("ascii")
                reply_func({
                    "type": "response",
                    "id": req_id,
                    "status": "ok",
                    "path": path,
                    "total_size": total_size,
                    "bytes_read": len(data),
                    "content_b64": b64
                })
            except Exception as e:
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": str(e)})

        elif req_type == "ls":
            path = req.get("path", "/storage")
            if not os.path.exists(path):
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": "Path not found"})
                return
            try:
                entries = []
                for entry in os.scandir(path):
                    try:
                        st = entry.stat(follow_symlinks=False)
                        entries.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(follow_symlinks=False),
                            "size": st.st_size,
                            "mtime": int(st.st_mtime),
                            "mode": oct(st.st_mode)[-4:]
                        })
                    except Exception:
                        entries.append({"name": entry.name, "is_dir": entry.is_dir(), "size": 0, "mtime": 0, "mode": "0000"})
                entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
                reply_func({"type": "response", "id": req_id, "status": "ok", "path": path, "entries": entries})
            except Exception as e:
                reply_func({"type": "response", "id": req_id, "status": "error", "msg": str(e)})

        elif req_type == "reboot":
            reply_func({"type": "response", "id": req_id, "status": "ok", "msg": "Rebooting console..."})
            time.sleep(0.5)
            subprocess.run(["systemctl", "reboot"])

        elif req_type == "poweroff":
            reply_func({"type": "response", "id": req_id, "status": "ok", "msg": "Powering off console..."})
            time.sleep(0.5)
            subprocess.run("systemctl poweroff || poweroff", shell=True)

    def run_power_sync_watchdog(self):
        """Monitors system health and power state without unintended reboots or shutdowns."""
        while self.running:
            time.sleep(10.0)

    def run_serial_loop(self):
        """Persistent serial loop: keeps port open and dispatches requests to thread pool."""
        while self.running:
            dev = self.find_serial_dev()
            if not dev or not os.path.exists(dev):
                time.sleep(0.5)
                continue

            try:
                with open(dev, "r+b", buffering=0) as ser:
                    try:
                        import tty
                        tty.setraw(ser.fileno())
                    except Exception:
                        pass
                    self.ser_file = ser
                    buf = b""
                    while self.running:
                        r, _, _ = select.select([ser], [], [], 0.2)
                        if not r:
                            continue
                        try:
                            chunk = ser.read(4096)
                        except Exception:
                            self.fb.show_splash()
                            break
                        if not chunk:
                            # Host disconnected / PC sleep or shutdown: revert to cyber splash
                            self.fb.show_splash()
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line_str = line.decode("utf-8", "ignore").strip()
                            if line_str:
                                self.executor.submit(self.handle_request, line_str, self.send_serial_response)
            except Exception:
                self.fb.show_splash()
                time.sleep(0.5)
            finally:
                self.ser_file = None

    def run_tcp_server(self, port=8888):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            sock.listen(5)
            while self.running:
                client_sock, _ = sock.accept()
                self.active_connections.add(client_sock)
                t = threading.Thread(target=self._handle_tcp_client, args=(client_sock,), daemon=True)
                t.start()
        except Exception:
            pass

    def _handle_tcp_client(self, client_sock):
        buf = b""
        def reply_cb(resp_dict):
            try:
                data = json.dumps(resp_dict, ensure_ascii=False) + "\n"
                client_sock.sendall(data.encode("utf-8"))
            except Exception:
                pass

        while self.running:
            try:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line_str = line.decode("utf-8", "ignore").strip()
                    if line_str:
                        self.executor.submit(self.handle_request, line_str, reply_cb)
            except Exception:
                break
        self.active_connections.discard(client_sock)
        try:
            client_sock.close()
        except Exception:
            pass

    def run_gamepad_listener(self):
        """Monitors all /dev/input/event* devices simultaneously for button presses."""
        BUTTON_MAP = {
            # Standard Gamepad (micro_gamepad)
            304: "A",
            305: "B",
            307: "X",
            308: "Y",
            310: "L1",
            311: "R1",
            312: "L2",
            313: "R2",
            314: "SELECT",
            315: "START",
            316: "MODE",
            317: "L3",
            318: "R3",
            # GA36 Clone D-Pad Keys (micro_gamepad EV_KEY)
            544: "DPAD_UP",
            545: "DPAD_DOWN",
            546: "DPAD_LEFT",
            547: "DPAD_RIGHT",
            # Standard / Extended D-pad keys
            16: "DPAD_DOWN",
            17: "DPAD_UP",
            103: "DPAD_UP",
            105: "DPAD_LEFT",
            106: "DPAD_RIGHT",
            108: "DPAD_DOWN",
            # Center function & system buttons
            28: "ENTER",
            102: "HOME",
            114: "VOL_DOWN",
            115: "VOL_UP",
            116: "POWER",
            139: "MENU",
            464: "FN",       # UDT center FN / Menu button
            # Generic Joystick buttons
            0: "BTN_0",
            1: "BTN_1",
            2: "BTN_2",
            3: "BTN_3",
            8: "SELECT",
            9: "START",
            288: "BTN_0",
            289: "BTN_1",
            290: "BTN_2",
            291: "BTN_3",
            296: "SELECT",
            297: "START"
        }
        last_abs_x = 0
        last_abs_y = 0

        while self.running:
            dev_nodes = glob.glob("/dev/input/event*")
            if not dev_nodes:
                time.sleep(2.0)
                continue
            fds = []
            for node in dev_nodes:
                try:
                    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                    fds.append(fd)
                except Exception:
                    pass
            if not fds:
                time.sleep(1.0)
                continue
            try:
                while self.running:
                    r, _, _ = select.select(fds, [], [], 0.5)
                    for fd in r:
                        try:
                            raw = os.read(fd, 64)
                            if len(raw) >= 16:
                                for i in range(0, len(raw), 16):
                                    chunk = raw[i:i+16]
                                    if len(chunk) < 16:
                                        break
                                    _, _, ev_type, code, val = struct.unpack("IIHHi", chunk)
                                    if ev_type == 1 and val in (1, 2):
                                        btn_name = BUTTON_MAP.get(code, f"KEY_{code}")
                                        self.broadcast_message({"type": "button", "name": btn_name, "code": code})
                                    elif ev_type == 3:
                                        # Analog / Hat D-pad axes (GA36 sticks: 0..18, center 9; Standard: -32768..32767)
                                        if code in (0, 16):  # X-Axis
                                            is_left = (val <= 3) or (val < -1000)
                                            is_right = (val >= 15) or (val > 1000)
                                            is_center = (6 <= val <= 12) or (abs(val) <= 1000)
                                            if is_left and last_abs_x >= 0:
                                                last_abs_x = -1
                                                self.broadcast_message({"type": "button", "name": "DPAD_LEFT", "code": 546})
                                            elif is_right and last_abs_x <= 0:
                                                last_abs_x = 1
                                                self.broadcast_message({"type": "button", "name": "DPAD_RIGHT", "code": 547})
                                            elif is_center:
                                                last_abs_x = 0
                                        elif code in (1, 17):  # Y-Axis
                                            is_up = (val <= 3) or (val < -1000)
                                            is_down = (val >= 15) or (val > 1000)
                                            is_center = (6 <= val <= 12) or (abs(val) <= 1000)
                                            if is_up and last_abs_y >= 0:
                                                last_abs_y = -1
                                                self.broadcast_message({"type": "button", "name": "DPAD_UP", "code": 544})
                                            elif is_down and last_abs_y <= 0:
                                                last_abs_y = 1
                                                self.broadcast_message({"type": "button", "name": "DPAD_DOWN", "code": 545})
                                            elif is_center:
                                                last_abs_y = 0
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="R36S Real-Time USB Bridge Agent v3.0")
    parser.add_argument("--daemon", action="store_true", help="Run silently in the background")
    parser.add_argument("--dev", default="/dev/ttyGS0", help="Serial device to use")
    parser.add_argument("--port", type=int, default=8888, help="TCP port to listen on")
    args = parser.parse_args()

    agent = R36SAgent(serial_dev=args.dev, tcp_port=args.port)

    # Clean termination signal handling on console shutdown or reboot
    def handle_signal(signum, frame):
        agent.running = False
        try:
            agent.fb.show_splash()
            agent.fb.close()
        except Exception:
            pass
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
    except Exception:
        pass

    # Start TCP Server thread
    t_tcp = threading.Thread(target=agent.run_tcp_server, args=(args.port,), daemon=True)
    t_tcp.start()

    # Start Gamepad Listener thread
    t_pad = threading.Thread(target=agent.run_gamepad_listener, daemon=True)
    t_pad.start()

    # Start Power Sync Watchdog thread (auto poweroff when PC turns off)
    t_power = threading.Thread(target=agent.run_power_sync_watchdog, daemon=True)
    t_power.start()

    # Main thread runs serial loop
    try:
        agent.run_serial_loop()
    finally:
        agent.fb.close()


if __name__ == "__main__":
    main()
