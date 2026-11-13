"""Cross-platform ADB discovery, automatic provisioning, and resilient device wrappers."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .console import Console
from .utils import download_file, host_platform

PLATFORM_TOOLS_URLS = {
    "macos": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
}


@dataclass
class AdbDevice:
    """Represents an Android device reported by `adb devices -l`."""

    serial: str
    state: str
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == "device"


def _candidate_adb_paths(cache_dir: Path) -> List[Path]:
    plat = host_platform()
    exe = "adb.exe" if plat == "windows" else "adb"
    candidates: List[Path] = [cache_dir / "platform-tools" / exe]

    in_path = shutil.which("adb")
    if in_path:
        candidates.append(Path(in_path))

    home = Path.home()
    if plat == "macos":
        candidates.extend([
            home / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
            Path("/opt/homebrew/bin/adb"),
            Path("/usr/local/bin/adb"),
        ])
    elif plat == "linux":
        candidates.extend([
            home / "Android" / "Sdk" / "platform-tools" / "adb",
            Path("/usr/bin/adb"),
            Path("/usr/local/bin/adb"),
        ])
    else:
        local_app = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        candidates.extend([
            local_app / "Android" / "Sdk" / "platform-tools" / "adb.exe",
            Path("C:/platform-tools/adb.exe"),
            Path("C:/Android/platform-tools/adb.exe"),
        ])
    return candidates


def find_adb(cache_dir: Path) -> Optional[Path]:
    """Locate an existing working `adb` executable on the host."""
    for path in _candidate_adb_paths(cache_dir):
        if path.is_file():
            return path
    return None


def ensure_adb(cache_dir: Path, console: Console) -> Path:
    """Return path to `adb`, automatically downloading Google Platform-Tools if missing."""
    existing = find_adb(cache_dir)
    if existing:
        console.ok(f"Found ADB: {existing}")
        return existing

    plat = host_platform()
    url = PLATFORM_TOOLS_URLS[plat]
    zip_path = cache_dir / f"platform-tools-{plat}.zip"
    extract_dir = cache_dir / "platform-tools"

    console.info("ADB not found on system — downloading official Android Platform-Tools...")
    download_file(url, zip_path, console, label="platform-tools.zip", min_bytes=1_000_000)

    if extract_dir.exists():
        shutil.rmtree(extract_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cache_dir)

    exe_name = "adb.exe" if plat == "windows" else "adb"
    adb_path = extract_dir / exe_name
    if not adb_path.is_file():
        raise RuntimeError(f"Expected {adb_path} after extracting platform-tools")

    if plat != "windows":
        for item in extract_dir.iterdir():
            if item.is_file():
                item.chmod(item.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    console.ok(f"Installed standalone ADB to {adb_path}")
    return adb_path


def parse_adb_devices(output: str) -> List[AdbDevice]:
    """Parse stdout of `adb devices -l` into AdbDevice objects."""
    devices: List[AdbDevice] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        meta = {}
        for token in parts[2:]:
            if ":" in token:
                k, v = token.split(":", 1)
                meta[k] = v
        devices.append(
            AdbDevice(
                serial=serial,
                state=state,
                model=meta.get("model", ""),
                product=meta.get("product", ""),
                device=meta.get("device", ""),
                transport_id=meta.get("transport_id", ""),
            )
        )
    return devices


class AdbClient:
    """Thin wrapper around the `adb` binary bound to a specific device serial."""

    def __init__(self, adb_path: Path, serial: Optional[str] = None) -> None:
        self.adb_path = adb_path
        self.serial = serial

    def run_raw(
        self,
        args: List[str],
        with_serial: bool = True,
        timeout: int = 30,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [str(self.adb_path)]
        if with_serial and self.serial:
            cmd.extend(["-s", self.serial])
        cmd.extend(args)
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"ADB command failed ({' '.join(args)}): {err}")
        return proc

    def restart_server(self) -> None:
        self.run_raw(["kill-server"], with_serial=False, timeout=10)
        time.sleep(0.5)
        self.run_raw(["start-server"], with_serial=False, timeout=15)

    def list_devices(self) -> List[AdbDevice]:
        proc = self.run_raw(["devices", "-l"], with_serial=False, timeout=15)
        return parse_adb_devices(proc.stdout)

    def shell(self, command: str, timeout: int = 30, check: bool = False) -> subprocess.CompletedProcess[str]:
        return self.run_raw(["shell", command], timeout=timeout, check=check)

    def push(self, local_path: Path, remote_path: str, timeout: int = 600) -> None:
        self.run_raw(["push", str(local_path), remote_path], timeout=timeout, check=True)

    def install_apk(self, apk_path: Path, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return self.run_raw(["install", "-r", "-d", str(apk_path)], timeout=timeout, check=False)

    def forward(self, local_port: int, remote_port: int) -> None:
        self.run_raw(["forward", f"tcp:{local_port}", f"tcp:{remote_port}"], timeout=10, check=True)

    def remove_forward(self, local_port: int) -> None:
        self.run_raw(["forward", "--remove", f"tcp:{local_port}"], timeout=10, check=False)


def wait_for_device(
    adb_path: Path,
    console: Console,
    preferred_serial: Optional[str] = None,
    timeout_sec: int = 120,
    non_interactive: bool = False,
) -> AdbClient:
    """Wait until an authorized Android device is connected and return an AdbClient."""
    probe = AdbClient(adb_path)
    deadline = time.monotonic() + timeout_sec
    warned_waiting = False
    warned_unauth = False
    restarted_once = False

    while time.monotonic() < deadline:
        devices = probe.list_devices()
        if preferred_serial:
            devices = [d for d in devices if d.serial == preferred_serial]

        ready = [d for d in devices if d.is_ready]
        if ready:
            if len(ready) > 1 and not preferred_serial and not non_interactive and os.isatty(0):
                console.info("Multiple Android devices detected:")
                for i, d in enumerate(ready, 1):
                    print(f"    {i}. {d.serial} ({d.model or 'Android'})")
                choice = input("  Select device number [1]: ").strip() or "1"
                idx = max(1, min(len(ready), int(choice))) - 1
                chosen = ready[idx]
            else:
                # Prefer physical USB serials over TCP/IP (ip:port) when both are present.
                usb_ready = [d for d in ready if ":" not in d.serial]
                chosen = usb_ready[0] if usb_ready else ready[0]

            console.ok(f"Connected: {chosen.model or 'Android'} (serial: {chosen.serial})")
            return AdbClient(adb_path, chosen.serial)

        unauth = [d for d in devices if d.state == "unauthorized"]
        if unauth and not warned_unauth:
            console.warn(
                "Phone detected but USB debugging is UNAUTHORIZED.\n"
                "    → Check your phone screen, tick 'Always allow from this computer', and tap Allow."
            )
            warned_unauth = True
        elif not devices and not warned_waiting:
            if not restarted_once:
                restarted_once = True
                probe.restart_server()
                continue
            console.warn(
                "No Android phone detected yet. Waiting for USB connection...\n"
                "    1. Plug in your phone using a data-capable USB cable\n"
                "    2. Enable Developer Options → USB Debugging\n"
                "    3. Set USB mode to 'File transfer' if prompted"
            )
            warned_waiting = True

        if non_interactive and not devices and restarted_once and time.monotonic() + timeout_sec - 10 < deadline:
            # Give a short grace window in non-interactive mode, then continue polling up to timeout_sec
            pass
        time.sleep(2)

    raise TimeoutError(
        "Timed out waiting for an authorized Android device over ADB. "
        "Run 'adb devices' to verify USB debugging and drivers."
    )
