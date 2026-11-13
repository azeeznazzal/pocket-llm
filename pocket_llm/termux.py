"""Termux provisioning, Android background survival tuning, and command bridge."""

from __future__ import annotations

import base64
import posixpath
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .adb import AdbClient
from .console import Console
from .detector import DeviceProfile
from .utils import download_file, fetch_json

TERMUX_RELEASE_API = "https://api.github.com/repos/termux/termux-app/releases/latest"
TERMUX_FALLBACK_URL = (
    "https://github.com/termux/termux-app/releases/download/v0.118.3/"
    "termux-app_v0.118.3%2Bgithub-debug_arm64-v8a.apk"
)

OEM_GUIDES = {
    "xiaomi": (
        "Xiaomi / MIUI / HyperOS note: Enable 'Install via USB' in Developer Options if APK install fails, "
        "and set Termux Battery Saver → 'No restrictions' if killed in background."
    ),
    "oppo": (
        "Oppo / Realme / ColorOS note: Enable 'Disable Permission Monitoring' in Developer Options if "
        "ADB install is blocked, and allow background activity for Termux in Battery settings."
    ),
    "oneplus": (
        "OnePlus / OxygenOS note: If the server sleeps after long idle, lock Termux in Recent Apps or "
        "set Battery Optimization → 'Don't optimize'."
    ),
    "huawei": (
        "Huawei / EMUI note: Disable automatic 'App launch' management for Termux in Battery settings "
        "and enable Run in background."
    ),
    "samsung": (
        "Samsung One UI note: Termux has been whitelisted from Doze and App Standby. If needed later, "
        "ensure Termux is not in 'Deep sleeping apps' under Device Care → Battery."
    ),
}


def resolve_termux_apk_url() -> str:
    """Resolve the latest GitHub debug arm64-v8a Termux APK URL, with safe fallback."""
    try:
        data = fetch_json(TERMUX_RELEASE_API, timeout=12)
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if "arm64-v8a" in name and name.endswith(".apk"):
                return str(asset["browser_download_url"])
    except Exception:
        pass
    return TERMUX_FALLBACK_URL


def is_termux_installed(adb: AdbClient) -> bool:
    res = adb.shell("pm list packages com.termux", timeout=15)
    return "package:com.termux" in res.stdout


def can_run_as_termux(adb: AdbClient) -> bool:
    res = adb.shell("run-as com.termux id", timeout=10)
    return res.returncode == 0 and "uid=" in res.stdout


def ensure_termux_installed(adb: AdbClient, profile: DeviceProfile, cache_dir: Path, console: Console) -> None:
    """Install Termux (GitHub arm64-v8a debug build) if not already installed."""
    if is_termux_installed(adb):
        console.ok("Termux is already installed")
        return

    url = resolve_termux_apk_url()
    apk_path = cache_dir / "termux-arm64-v8a.apk"
    console.info("Downloading Termux APK (GitHub arm64-v8a build)...")
    download_file(url, apk_path, console, label="termux.apk", min_bytes=5_000_000)

    console.info("Installing Termux onto phone via ADB...")
    for attempt in range(1, 4):
        res = adb.install_apk(apk_path)
        combined = f"{res.stdout}\n{res.stderr}"
        if res.returncode == 0 or is_termux_installed(adb):
            console.ok("Termux installed successfully")
            return

        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in combined:
            raise RuntimeError(
                "An incompatible Play Store version of Termux is installed. "
                "Please uninstall Termux on the phone and re-run setup."
            )

        if "INSTALL_FAILED_USER_RESTRICTED" in combined or "SecurityException" in combined:
            console.warn(
                f"OEM blocked USB install (attempt {attempt}/3). "
                "Check your phone screen for Play Protect / USB install prompts.\n"
                f"    {OEM_GUIDES.get(profile.oem_key, 'Enable Install via USB in Developer Options.')}"
            )
            time.sleep(8)
            continue

        # Handle transient Play Protect confirmation or ADB hiccup
        console.warn(f"ADB install returned error (attempt {attempt}/3): {combined.strip() or 'unknown'}")
        time.sleep(5)
        if is_termux_installed(adb):
            console.ok("Termux installation detected")
            return

    raise RuntimeError(
        "Failed to install Termux APK via ADB. You can install it manually from "
        f"{apk_path} or GitHub releases, then re-run."
    )


def apply_power_optimizations(adb: AdbClient, profile: DeviceProfile, console: Console) -> None:
    """Configure Android power manager so Termux stays alive with the screen locked."""
    cmds = [
        "dumpsys deviceidle whitelist +com.termux",
        "am set-standby-bucket com.termux active",
        "cmd appops set com.termux RUN_IN_BACKGROUND allow",
        "cmd appops set com.termux RUN_ANY_IN_BACKGROUND allow",
        "settings put global wifi_sleep_policy 2",
    ]
    for cmd in cmds:
        adb.shell(f"{cmd} >/dev/null 2>&1 || true", timeout=10)
    console.ok("Applied background persistence tweaks (Doze whitelist, standby exempt, Wi-Fi keep-alive)")
    hint = OEM_GUIDES.get(profile.oem_key)
    if hint:
        console.info(hint)


class TermuxBridge:
    """Executes shell commands and transfers files into Termux via `run-as` or SSH fallback."""

    TERMUX_HOME = "/data/data/com.termux/files/home"
    TERMUX_USR = "/data/data/com.termux/files/usr"

    def __init__(
        self,
        adb: AdbClient,
        cache_dir: Path,
        repo_root: Path,
        console: Console,
    ) -> None:
        self.adb = adb
        self.cache_dir = cache_dir
        self.repo_root = repo_root
        self.console = console
        self.mode: str = ""  # "run-as" or "ssh"
        self.ssh_key: Optional[Path] = None

    def initialize(self) -> None:
        """Launch Termux to ensure bootstrap extraction completes and select command transport."""
        self.console.info("Launching Termux on phone to verify bootstrap...")
        self.adb.shell(
            "input keyevent KEYCODE_WAKEUP >/dev/null 2>&1 || true; "
            "am start -n com.termux/.app.TermuxActivity >/dev/null 2>&1",
            timeout=15,
        )

        if can_run_as_termux(self.adb):
            self.mode = "run-as"
            for _ in range(30):
                probe = self.adb.shell(
                    f"run-as com.termux test -x {self.TERMUX_USR}/bin/sh && echo READY",
                    timeout=10,
                )
                if "READY" in probe.stdout:
                    self.console.ok("Termux shell ready (direct ADB run-as transport)")
                    self.run("termux-wake-lock >/dev/null 2>&1 || true", timeout=15)
                    return
                time.sleep(2)
            raise RuntimeError("Termux did not finish first-launch bootstrap within 60s.")

        # Fallback for non-debuggable (F-Droid) Termux builds or OEMs restricting run-as:
        self.console.info("Direct run-as unavailable; using Termux SSH fallback transport...")
        self.mode = "ssh"
        self._bootstrap_ssh_fallback()

    def _ensure_ssh_key(self) -> Path:
        keys_dir = self.cache_dir / "keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        key_path = keys_dir / "id_pocket"
        if not key_path.is_file():
            ssh_keygen = shutil.which("ssh-keygen")
            if not ssh_keygen:
                raise RuntimeError("ssh-keygen not found on host; needed for non-debuggable Termux SSH fallback.")
            subprocess.run(
                [ssh_keygen, "-t", "ed25519", "-N", "", "-C", "pocket-llm", "-f", str(key_path), "-q"],
                check=True,
            )
            try:
                key_path.chmod(0o600)
            except Exception:
                pass
        self.ssh_key = key_path
        return key_path

    def _ssh_exec(self, command: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        assert self.ssh_key is not None
        ssh_bin = shutil.which("ssh")
        if not ssh_bin:
            raise RuntimeError("OpenSSH client (`ssh`) not found on host.")
        self.adb.forward(8022, 8022)
        return subprocess.run(
            [
                ssh_bin,
                "-p",
                "8022",
                "-i",
                str(self.ssh_key),
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "LogLevel=ERROR",
                "-o",
                "ConnectTimeout=10",
                "127.0.0.1",
                command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    def _bootstrap_ssh_fallback(self) -> None:
        key_path = self._ensure_ssh_key()
        pub_path = key_path.with_suffix(".pub")

        # If sshd is already up with our key, reuse immediately.
        try:
            probe = self._ssh_exec("echo SSH_OK", timeout=10)
            if probe.returncode == 0 and "SSH_OK" in probe.stdout:
                self.console.ok("Termux SSH bridge active")
                return
        except Exception:
            pass

        bootstrap_script = self.repo_root / "scripts" / "termux_bootstrap.sh"
        self.adb.push(pub_path, "/data/local/tmp/pocket_host_key.pub")
        self.adb.push(bootstrap_script, "/data/local/tmp/pocket_bootstrap.sh")
        self.adb.shell("chmod 644 /data/local/tmp/pocket_host_key.pub /data/local/tmp/pocket_bootstrap.sh")

        # Give first-run Termux time to finish extracting rootfs before typing command
        time.sleep(8)
        self.adb.shell("input text 'sh%s/data/local/tmp/pocket_bootstrap.sh' && input keyevent 66", timeout=15)

        self.console.info("Waiting for Termux SSH daemon to start (installing OpenSSH)...")
        for _ in range(45):
            try:
                probe = self._ssh_exec("echo SSH_OK", timeout=6)
                if probe.returncode == 0 and "SSH_OK" in probe.stdout:
                    self.console.ok("Termux SSH bridge ready")
                    return
            except Exception:
                pass
            time.sleep(3)

        raise RuntimeError(
            "Timed out waiting for Termux SSH bootstrap. On Xiaomi/MIUI, ensure "
            "'USB debugging (Security settings)' is enabled or use the GitHub Termux APK."
        )

    def run(self, script: str, timeout: int = 180, check: bool = False) -> subprocess.CompletedProcess[str]:
        """Run a multi-line shell script inside Termux with proper PREFIX/HOME/PATH."""
        wrapped = (
            f"export PREFIX={self.TERMUX_USR}\n"
            f"export HOME={self.TERMUX_HOME}\n"
            f"export PATH=\"$PREFIX/bin:$PATH\"\n"
            f"export TMPDIR=\"$PREFIX/tmp\"\n"
            f"export LD_LIBRARY_PATH=\"$PREFIX/lib\"\n"
            f"cd \"$HOME\"\n"
            f"{script}\n"
        )
        if self.mode == "ssh":
            b64 = base64.b64encode(wrapped.encode("utf-8")).decode("ascii")
            res = self._ssh_exec(f"echo {b64} | base64 -d | sh -s", timeout=timeout)
        else:
            b64 = base64.b64encode(wrapped.encode("utf-8")).decode("ascii")
            cmd = f"echo {b64} | base64 -d | run-as com.termux {self.TERMUX_USR}/bin/sh -s"
            res = self.adb.shell(cmd, timeout=timeout, check=False)

        if check and res.returncode != 0:
            err = (res.stderr or res.stdout).strip()
            raise RuntimeError(f"Termux command failed (exit {res.returncode}): {err}")
        return res

    def push_file(self, local_path: Path, termux_rel_path: str, mode: str = "644") -> str:
        """Push a file from host to `/data/data/com.termux/files/home/<termux_rel_path>`."""
        remote_tmp = f"/data/local/tmp/pocket_xfer_{local_path.name}"
        dest = f"{self.TERMUX_HOME}/{termux_rel_path.lstrip('/')}"
        dest_dir = posixpath.dirname(dest)

        self.adb.push(local_path, remote_tmp, timeout=900)
        self.adb.shell(f"chmod 644 {remote_tmp}", timeout=15)

        if self.mode == "ssh":
            self.run(
                f"mkdir -p '{dest_dir}' && cp '{remote_tmp}' '{dest}' && chmod {mode} '{dest}'",
                timeout=120,
                check=True,
            )
        else:
            # Pipe through shell stdin so SELinux on Android 11+ never blocks Termux from reading /data/local/tmp
            self.adb.shell(
                f"run-as com.termux {self.TERMUX_USR}/bin/sh -c "
                f"\"mkdir -p '{dest_dir}' && cat > '{dest}' && chmod {mode} '{dest}'\" < '{remote_tmp}'",
                timeout=300,
                check=True,
            )

        self.adb.shell(f"rm -f '{remote_tmp}'", timeout=15)
        return dest
