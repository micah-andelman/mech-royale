#!/usr/bin/env python3
"""Adapted from jklein07/mech-royale-bot tools/live.py (MIT). Run the bot in a private match on play.joinbuilders.org (empty seats are the server's bots) and log everything.

    python3 tools/live.py --bot ./target/release/bot --out runs/live

Standard library only. One private room at a time; the room is created via /mm/code/new.
"""
import argparse
import base64
import json
import os
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.request

HOST = "play.joinbuilders.org"


class WS:
    def __init__(self, url_path):
        ctx = ssl.create_default_context()
        raw = socket.create_connection((HOST, 443), timeout=15)
        self.s = ctx.wrap_socket(raw, server_hostname=HOST)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (
            f"GET {url_path} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: https://{HOST}\r\n"
            f"User-Agent: mech-bot-bridge\r\n\r\n"
        )
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise RuntimeError("handshake failed")
            buf += chunk
        head, self.rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("handshake: " + head.decode(errors="replace")[:300])
        self.s.settimeout(None)
        self.lock = threading.Lock()

    def _read(self, n):
        while len(self.rest) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError()
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def recv(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            fin, op = b0 & 0x80, b0 & 0x0F
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(ln)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if op == 8:
                raise EOFError("closed")
            if op == 9:
                self._send(10, data)
                continue
            if op == 10:
                continue
            msg += data
            if fin:
                return msg.decode()

    def _send(self, op, data):
        hdr = bytes([0x80 | op])
        n = len(data)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = secrets.token_bytes(4)
        body = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        with self.lock:
            self.s.sendall(hdr + mask + body)

    def send(self, obj):
        self._send(1, json.dumps(obj, separators=(",", ":")).encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True)
    ap.add_argument("--map", required=True, help="local arena dir passed to the bot as hello.map.dir")
    ap.add_argument("--out", default="runs/live")
    ap.add_argument("--name", default="fpt-bot")
    ap.add_argument("--hello-map-dir", default=None, help="map dir to tell the bot (e.g. /arena for a docker bot)")
    ap.add_argument("--spectate", action="store_true", help="also record the public spectator feed")
    ap.add_argument("--code", default=None, help="join an existing private match code instead of creating one")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sid = secrets.token_hex(16)
    if a.code:
        url = f"https://{HOST}/mm/code/join?c={a.code}&sid={sid}"
    else:
        url = f"https://{HOST}/mm/code/new?sid={sid}"
    info = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mech-bot-bridge"}), timeout=10).read())
    print("room", info, file=sys.stderr)
    room, code = info["room"], info["code"]
    path = f"/r/{room}/play?token=&sid={sid}&name={a.name}&code={code}"
    ws = WS(path)
    log = open(os.path.join(a.out, f"{time.strftime('%Y%m%d-%H%M%S')}-{code}.jsonl"), "w")
    # spectator feed (full-state replay frames) for offline analysis only; the bot never sees it
    def spectate():
        sid2 = secrets.token_hex(16)
        try:
            lw = WS(f"/r/{room}/live?token=&code={code}&sid={sid2}")
        except Exception as e:  # noqa: BLE001
            print("live feed failed:", e, file=sys.stderr)
            return
        with open(os.path.join(a.out, f"{time.strftime('%Y%m%d-%H%M%S')}-{code}.live.jsonl"), "w") as lf:
            while True:
                try:
                    raw = lw.recv()
                except (EOFError, OSError):
                    break
                lf.write(raw.rstrip("\n") + "\n")

    if a.spectate:
        threading.Thread(target=spectate, daemon=True).start()
    bot = None
    started = False
    out_q = []

    def bot_reader(proc):
        for line in proc.stdout:
            line = line.decode().strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("type") == "ready":
                continue
            ws.send(m)
            log.write(json.dumps({"dir": "act", "at": time.time(), "m": m}) + "\n")

    t_end = time.time() + 20 * 60
    while time.time() < t_end:
        try:
            raw = ws.recv()
        except EOFError:
            print("socket closed", file=sys.stderr)
            break
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        ty = m.get("type")
        if ty != "obs":
            log.write(json.dumps({"dir": "in", "at": time.time(), "m": m}) + "\n")
        if ty == "lobby":
            you = m.get("you", {})
            if not you.get("ready"):
                ws.send({"type": "ready", "name": a.name})
            if you.get("host") and m.get("phase") == "waiting" and not started:
                started = True
                time.sleep(1.0)
                ws.send({"type": "start"})
                print("start sent", file=sys.stderr)
        elif ty == "hello":
            m["map"]["dir"] = a.hello_map_dir or os.path.abspath(a.map)
            bot = subprocess.Popen(a.bot.split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(a.out, "bot.stderr"), "a"))
            threading.Thread(target=bot_reader, args=(bot,), daemon=True).start()
            bot.stdin.write((json.dumps(m) + "\n").encode())
            bot.stdin.flush()
            ws.send({"type": "loaded"})
            print("hello slot", m.get("slot"), file=sys.stderr)
        elif ty == "obs":
            log.write(json.dumps({"dir": "in", "at": time.time(), "m": m}) + "\n")
            if bot:
                try:
                    bot.stdin.write((raw + "\n").encode())
                    bot.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
        elif ty == "end":
            print("end", m, file=sys.stderr)
            if bot:
                try:
                    bot.stdin.write((raw + "\n").encode())
                    bot.stdin.close()
                except (BrokenPipeError, ValueError):
                    pass
                bot = None
        elif ty == "results":
            print("results received", file=sys.stderr)
            break
    log.flush()
    log.close()
    try:
        ws.s.close()
    except OSError:
        pass


if __name__ == "__main__":
    main()
