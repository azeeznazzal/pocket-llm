"""Server lifecycle, ADB port forwarding, and Cloudflare Tunnel integration."""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .adb import AdbClient
from .console import Console
from .termux import TermuxBridge


@dataclass
class ServerEndpoints:
    """Reachable URLs for the running llama-server instance."""

    port: int
    local_url: str
    lan_url: Optional[str]
    public_url: Optional[str] = None
    pid: Optional[int] = None


def _parse_kv_line(text: str, prefix: str) -> dict[str, str]:
    for line in text.splitlines():
        if prefix in line:
            return dict(re.findall(r"(\w+)=([^\s]+)", line))
    return {}


def verify_local_health(port: int, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_server(
    termux: TermuxBridge,
    adb: AdbClient,
    console: Console,
    port: int = 8080,
) -> ServerEndpoints:
    """Start `llama-server` inside Termux with wake-lock and forward port via ADB."""
    console.info(f"Starting llama-server on phone (port {port}, wake-lock active)...")
    res = termux.run(f"sh \"$HOME/pocket_ctl.sh\" server", timeout=90, check=True)
    kv = _parse_kv_line(res.stdout, "SERVER_READY")

    pid = int(kv["pid"]) if kv.get("pid", "").isdigit() else None
    wlan_ip = kv.get("wlan_ip", "")
    if wlan_ip in ("", "unknown"):
        wlan_ip = ""

    adb.forward(port, port)
    local_url = f"http://127.0.0.1:{port}"
    lan_url = f"http://{wlan_ip}:{port}" if wlan_ip else None

    if verify_local_health(port):
        console.ok(f"AI Web UI is online and responding at {local_url}")
    else:
        console.warn(f"Server started (pid={pid}), waiting for forwarded port {local_url} to respond...")

    return ServerEndpoints(port=port, local_url=local_url, lan_url=lan_url, pid=pid)


def start_cloudflare_tunnel(
    termux: TermuxBridge,
    console: Console,
    token: Optional[str] = None,
) -> Optional[str]:
    """Install/launch `cloudflared` on the phone to expose the Web UI publicly without a static IP."""
    console.info("Starting Cloudflare Tunnel on phone (works without static IP / router config)...")
    arg = f"'{token}'" if token else ""
    res = termux.run(f"sh \"$HOME/pocket_ctl.sh\" tunnel {arg}", timeout=180, check=True)
    if token:
        console.ok("Named Cloudflare Tunnel started with provided token")
        return None
    kv = _parse_kv_line(res.stdout, "TUNNEL_READY")
    url = kv.get("url")
    if url:
        console.ok(f"Public HTTPS tunnel live: {url}")
    return url


def stop_server_and_tunnel(termux: TermuxBridge, adb: AdbClient, console: Console, port: int = 8080) -> None:
    """Stop background inference server, tunnel, and remove ADB forward."""
    termux.run("sh \"$HOME/pocket_ctl.sh\" stop", timeout=30, check=False)
    adb.remove_forward(port)
    console.ok("Stopped AI server and released Termux wake-lock")
