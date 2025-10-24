#!/usr/bin/env python3
"""Toy UDP <-> TUN gateway for development-only end-to-end testing.

The gateway bridges UDP datagrams carrying raw IPv4 packets (in a minimal
custom frame) to a Linux TUN interface. It is intentionally simple, has no
encryption or authentication and MUST NOT be exposed to untrusted networks.
"""

import argparse
import asyncio
import fcntl
import logging
import os
import socket
import struct
import subprocess
import sys
import signal
import time
from logging.handlers import SysLogHandler, WatchedFileHandler
from typing import Dict, Optional, Tuple

MAGIC = 0x5459
VERSION = 0x01
FRAME_HEADER = struct.Struct("<HBBI")
FRAME_TYPE_DATA_IP = 0x00
FRAME_TYPE_PING = 0x01
FRAME_TYPE_PONG = 0x02

IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
TUNSETIFF = 0x400454ca

DEFAULT_LISTEN = "0.0.0.0:35000"
DEFAULT_TUN = "toy0"
DEFAULT_MTU = 1380

ClientAddr = Tuple[str, int]


def load_env(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path or not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def parse_listen(value: str) -> Tuple[str, int]:
    if ":" not in value:
        raise ValueError("listen must be formatted as host:port")
    host, port_str = value.rsplit(":", 1)
    port = int(port_str)
    if not (0 < port < 65536):
        raise ValueError("port must be within 1-65535")
    host = host or "0.0.0.0"
    return host, port


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    if len(payload) > 1600:
        raise ValueError("payload too large")
    header = FRAME_HEADER.pack(MAGIC, VERSION, frame_type, len(payload))
    return header + payload


def parse_frame(data: bytes) -> Tuple[int, bytes]:
    if len(data) < FRAME_HEADER.size:
        raise ValueError("datagram too small")
    magic, version, frame_type, length = FRAME_HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError("invalid magic")
    if version != VERSION:
        raise ValueError("unsupported version")
    if frame_type not in (FRAME_TYPE_DATA_IP, FRAME_TYPE_PING, FRAME_TYPE_PONG):
        raise ValueError(f"unknown frame type {frame_type}")
    payload = data[FRAME_HEADER.size:FRAME_HEADER.size + length]
    if len(payload) != length:
        raise ValueError("payload truncated")
    return frame_type, payload


class ToyTunGateway:
    def __init__(self, listen: Tuple[str, int], tun_name: str, mtu: int, logger: logging.Logger) -> None:
        self.listen_host, self.listen_port = listen
        self.tun_name = tun_name
        self.mtu = mtu
        self.logger = logger

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.udp_sock: Optional[socket.socket] = None
        self.tun_fd: Optional[int] = None
        self.stats_task: Optional[asyncio.Task] = None

        self.clients: Dict[ClientAddr, float] = {}
        self.last_pong: Dict[ClientAddr, float] = {}
        self.client_ports: Dict[str, int] = {}
        self.pong_alerted: Dict[ClientAddr, bool] = {}
        self.shutdown_event = asyncio.Event()
        self.running = False

        self.udp_in_packets = 0
        self.udp_in_bytes = 0
        self.udp_out_packets = 0
        self.udp_out_bytes = 0
        self.tun_in_packets = 0
        self.tun_out_packets = 0

    def log(self, message: str, level: int = logging.INFO) -> None:
        self.logger.log(level, message)

    def log_debug(self, message: str) -> None:
        self.logger.debug(message)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.running = True
        self.setup_udp_socket()
        self.setup_tun_device()

        assert self.loop is not None
        assert self.udp_sock is not None
        assert self.tun_fd is not None

        self.loop.add_reader(self.udp_sock.fileno(), self.on_udp_readable)
        self.loop.add_reader(self.tun_fd, self.on_tun_readable)
        self.stats_task = self.loop.create_task(self.print_stats())

        self.log(
            f"Toy gateway ready — listening on udp://{self.listen_host}:{self.listen_port}"
            f" and bridging to TUN '{self.tun_name}' (MTU {self.mtu})."
        )

        await self.shutdown_event.wait()
        await self.stop()

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self.loop and self.udp_sock:
            self.loop.remove_reader(self.udp_sock.fileno())
        if self.loop and self.tun_fd is not None:
            self.loop.remove_reader(self.tun_fd)
        if self.stats_task:
            self.stats_task.cancel()
            self.stats_task = None
        if self.udp_sock:
            self.udp_sock.close()
            self.udp_sock = None
        if self.tun_fd is not None:
            os.close(self.tun_fd)
            self.tun_fd = None
        self.log("Toy gateway stopped.")

    def signal_stop(self, *_: object) -> None:
        self.log("Received termination signal, shutting down...")
        self.shutdown_event.set()

    def setup_udp_socket(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, self.listen_port))
        sock.setblocking(False)
        self.udp_sock = sock
        self.log(f"UDP socket bound to {self.listen_host}:{self.listen_port}")

    def setup_tun_device(self) -> None:
        fd = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack("16sH", self.tun_name.encode("utf-8"), IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(fd, TUNSETIFF, ifr)
        fcntl.fcntl(fd, fcntl.F_SETFL, os.O_NONBLOCK)
        self.tun_fd = fd
        self.log(f"TUN device '{self.tun_name}' opened")

        if self.mtu:
            try:
                subprocess.run(["ip", "link", "set", "dev", self.tun_name, "mtu", str(self.mtu)], check=True)
            except FileNotFoundError:
                self.log("'ip' command not found — skipping MTU configuration.", level=logging.WARNING)
            except subprocess.CalledProcessError as exc:
                self.log(f"Failed to set MTU: {exc}", level=logging.WARNING)

    def on_udp_readable(self) -> None:
        if not self.udp_sock:
            return
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(65535)
            except BlockingIOError:
                break
            except ConnectionResetError:
                continue
            if not data:
                continue
            try:
                frame_type, payload = parse_frame(data)
            except ValueError as exc:
                self.log(f"Invalid frame from {addr}: {exc}", level=logging.WARNING)
                continue

            previous_seen = self.clients.get(addr)
            previous_port = self.client_ports.get(addr[0])
            self.clients[addr] = time.time()
            self.client_ports[addr[0]] = addr[1]
            self.pong_alerted.pop(addr, None)
            if previous_seen is None:
                self.log(f"Client connected from {addr[0]}:{addr[1]}", level=logging.INFO)
            elif previous_port is not None and previous_port != addr[1]:
                self.log(
                    f"Client {addr[0]} changed source port from {previous_port} to {addr[1]}",
                    level=logging.WARNING,
                )

            if frame_type == FRAME_TYPE_DATA_IP:
                self.handle_data_from_udp(payload)
            elif frame_type == FRAME_TYPE_PING:
                self.log_debug(f"Ping from {addr}")
                self.send_udp_frame(FRAME_TYPE_PONG, b"", addr)
            elif frame_type == FRAME_TYPE_PONG:
                self.log_debug(f"Pong from {addr}")
                self.last_pong[addr] = time.time()
                self.pong_alerted[addr] = False

    def on_tun_readable(self) -> None:
        if self.tun_fd is None:
            return
        while True:
            try:
                packet = os.read(self.tun_fd, 65535)
            except BlockingIOError:
                break
            if not packet:
                break
            self.tun_in_packets += 1
            self.forward_packet_to_clients(packet)

    def handle_data_from_udp(self, payload: bytes) -> None:
        if self.tun_fd is None:
            return
        try:
            os.write(self.tun_fd, payload)
        except OSError as exc:
            self.log(f"Failed to write to TUN: {exc}", level=logging.ERROR)
            return
        self.udp_in_packets += 1
        self.udp_in_bytes += len(payload)
        self.tun_out_packets += 1
        self.log_debug(f"UDP -> TUN: {len(payload)} bytes")

    def forward_packet_to_clients(self, packet: bytes) -> None:
        active_clients = []
        now = time.time()
        for addr, last_seen in list(self.clients.items()):
            if now - last_seen > 120:
                self.clients.pop(addr, None)
                self.last_pong.pop(addr, None)
                self.pong_alerted.pop(addr, None)
                self.log(f"Client {addr[0]}:{addr[1]} timed out", level=logging.WARNING)
                continue
            active_clients.append(addr)

        if not active_clients:
            self.log_debug("No active clients to forward TUN packet")
            return

        try:
            datagram = encode_frame(FRAME_TYPE_DATA_IP, packet)
        except ValueError as exc:
            self.log(f"Failed to encode packet for UDP: {exc}", level=logging.ERROR)
            return
        for addr in active_clients:
            try:
                if self.udp_sock:
                    self.udp_sock.sendto(datagram, addr)
                    self.udp_out_packets += 1
                    self.udp_out_bytes += len(packet)
                    self.log_debug(f"TUN -> UDP {addr}: {len(packet)} bytes")
            except OSError as exc:
                self.log(f"Failed to send to {addr}: {exc}", level=logging.WARNING)

    def send_udp_frame(self, frame_type: int, payload: bytes, addr: ClientAddr) -> None:
        if not self.udp_sock:
            return
        try:
            datagram = encode_frame(frame_type, payload)
            self.udp_sock.sendto(datagram, addr)
        except OSError as exc:
            self.log(f"Failed to send frame to {addr}: {exc}", level=logging.WARNING)
        except ValueError as exc:
            self.log(f"Failed to encode frame: {exc}", level=logging.ERROR)

    async def print_stats(self) -> None:
        try:
            while self.running:
                await asyncio.sleep(10)
                self.log(
                    "Stats — UDP in/out: %s/%s packets (%s/%s bytes) | TUN in/out: %s/%s packets" % (
                        self.udp_in_packets,
                        self.udp_out_packets,
                        self.udp_in_bytes,
                        self.udp_out_bytes,
                        self.tun_in_packets,
                        self.tun_out_packets,
                    )
                )
                now = time.time()
                for addr in list(self.clients.keys()):
                    last_pong = self.last_pong.get(addr)
                    if last_pong is None:
                        continue
                    if now - last_pong > 30 and not self.pong_alerted.get(addr):
                        self.pong_alerted[addr] = True
                        self.log(
                            f"PONG timeout for {addr[0]}:{addr[1]} (last pong {int(now - last_pong)}s ago)",
                            level=logging.WARNING,
                        )
        except asyncio.CancelledError:
            pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bridge UDP toy frames with a Linux TUN device (development only)."
    )
    parser.add_argument("--env-file", default=".env", help="Optional env file with defaults (default: .env)")
    parser.add_argument("--listen", help="UDP listen address, host:port format (default: 0.0.0.0:35000)")
    parser.add_argument("--tun", help="TUN device name (default: toy0)")
    parser.add_argument("--mtu", type=int, help="MTU to set on the TUN interface (default: 1380)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--log-file", help="File to write logs (default: /var/log/private-tunnel/toy-gateway.log)")
    parser.add_argument("--log-level", help="Logging level INFO/WARNING/ERROR/DEBUG")
    parser.add_argument("--syslog", action="store_true", help="Send logs to system syslog")
    return parser


def resolve_options() -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args()
    env = load_env(args.env_file)

    listen_value = args.listen or env.get("TOY_UDP_LISTEN")
    if not listen_value:
        try:
            port_env = int(env.get("TOY_UDP_PORT", "35000"))
        except ValueError as exc:
            parser.error(f"Invalid TOY_UDP_PORT: {exc}")
        listen_value = f"0.0.0.0:{port_env}"

    tun_value = args.tun or env.get("TOY_TUN", DEFAULT_TUN)

    if args.mtu is not None:
        mtu_value = args.mtu
    else:
        try:
            mtu_value = int(env.get("MTU", str(DEFAULT_MTU)))
        except ValueError as exc:
            parser.error(f"Invalid MTU value: {exc}")

    try:
        host, port = parse_listen(listen_value)
    except ValueError as exc:
        parser.error(str(exc))

    args.listen = (host, port)
    args.tun = tun_value
    args.mtu = mtu_value

    log_file_value = args.log_file or env.get("TOY_LOG_FILE")
    if not log_file_value:
        log_file_value = "/var/log/private-tunnel/toy-gateway.log"
    if str(log_file_value).lower() in {"-", "stdout"}:
        log_file_value = None
    args.log_file = log_file_value

    log_level_value = args.log_level or env.get("TOY_LOG_LEVEL")
    if log_level_value:
        args.log_level = log_level_value.upper()
    elif args.verbose:
        args.log_level = "DEBUG"
    else:
        args.log_level = "INFO"

    if env.get("TOY_SYSLOG", "").lower() in {"1", "true", "yes"}:
        args.syslog = True

    return args


def configure_logging(args: argparse.Namespace) -> logging.Logger:
    logger = logging.getLogger("toy_gateway")
    logger.handlers.clear()

    try:
        level = getattr(logging, args.log_level.upper())
    except AttributeError:
        level = logging.INFO
    logger.setLevel(level)

    handlers = []
    if args.log_file:
        log_dir = os.path.dirname(args.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(WatchedFileHandler(args.log_file))
    if args.syslog:
        handlers.append(SysLogHandler(address="/dev/log"))
    if not handlers:
        handlers.append(logging.StreamHandler(sys.stdout))

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False
    return logger


def main() -> None:
    args = resolve_options()
    logger = configure_logging(args)
    logger.info(
        "Starting toy gateway listen=%s:%s tun=%s mtu=%s", args.listen[0], args.listen[1], args.tun, args.mtu
    )

    gateway = ToyTunGateway(args.listen, args.tun, args.mtu, logger=logger)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, gateway.signal_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: gateway.signal_stop())

    try:
        loop.run_until_complete(gateway.start())
    finally:
        loop.run_until_complete(gateway.stop())
        loop.close()


if __name__ == "__main__":
    main()
