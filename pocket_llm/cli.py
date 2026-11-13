"""Main CLI orchestrator for PocketLLM (human interactive & AI agent JSON modes)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .adb import AdbClient, ensure_adb, wait_for_device
from .console import Console
from .detector import DeviceProfile, inspect_device
from .engine import configure_phone_service, ensure_llama_engine
from .models import (
    MODEL_CATALOG,
    ModelSpec,
    Recommendation,
    ensure_model_downloaded,
    recommend_model,
    resolve_model,
)
from .termux import TermuxBridge, apply_power_optimizations, ensure_termux_installed
from .tunnel import ServerEndpoints, start_cloudflare_tunnel, start_server, stop_server_and_tunnel
from .utils import get_cache_dir

SUBCOMMANDS = ("setup", "inspect", "models", "web", "tunnel", "stop", "status", "chat")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _print_device_summary(profile: DeviceProfile, console: Console) -> None:
    rows = [
        ("Device", f"{profile.manufacturer} {profile.model} (Android {profile.android_version}, SDK {profile.sdk_int})"),
        ("SoC / ABI", f"{profile.soc} ({profile.abi})"),
        ("CPU ISA", profile.arm_arch_label),
        (
            "CPU Pinning",
            f"cores {profile.recommended_cores} (mask 0x{profile.recommended_core_mask}), {profile.recommended_threads} threads"
            if profile.recommended_core_mask
            else f"{profile.recommended_threads} threads across {profile.cpu_cores} cores",
        ),
        ("RAM", f"{profile.total_ram_mb} MB total / {profile.available_ram_mb} MB available (swap {profile.swap_total_mb} MB)"),
        ("Free Storage", f"{profile.free_storage_gb:.2f} GB in /data"),
        ("Wi-Fi IP", profile.wlan_ip or "Not connected to Wi-Fi"),
    ]
    console.table(("Property", "Detected Value"), rows)


def _choose_model_interactively(rec: Recommendation, console: Console) -> ModelSpec:
    print()
    console.ok(f"Recommended AI model: {console.bold(rec.model.name)}")
    console.info(f"Why: {rec.reason}")

    if not sys.stdin.isatty():
        return rec.model

    print()
    rows = []
    for idx, m in enumerate(MODEL_CATALOG, 1):
        tag = " (Recommended)" if m.id == rec.model.id else ""
        rows.append((str(idx), f"{m.id}{tag}", f"{m.size_mb} MB", f"{m.min_ram_mb} MB+", m.description))
    console.table(("#", "Model ID", "Size", "Min RAM", "Notes"), rows)

    default_idx = next(i for i, m in enumerate(MODEL_CATALOG, 1) if m.id == rec.model.id)
    raw = input(f"\n  Select model [1-{len(MODEL_CATALOG)}] (Press Enter for #{default_idx} {rec.model.id}): ").strip()
    if not raw:
        return rec.model
    if raw.isdigit() and 1 <= int(raw) <= len(MODEL_CATALOG):
        return MODEL_CATALOG[int(raw) - 1]
    for m in MODEL_CATALOG:
        if raw.lower() == m.id:
            return m
    console.warn(f"Unrecognized choice '{raw}', using recommended model {rec.model.id}.")
    return rec.model


def _connect_and_probe(
    args: argparse.Namespace,
    console: Console,
    cache_dir: Path,
) -> tuple[AdbClient, DeviceProfile]:
    adb_path = ensure_adb(cache_dir, console)
    adb = wait_for_device(
        adb_path,
        console,
        preferred_serial=args.serial,
        timeout_sec=args.timeout,
        non_interactive=args.non_interactive,
    )
    profile = inspect_device(adb)
    if not profile.is_arm64:
        raise RuntimeError(
            f"Device ABI '{profile.abi}' is not 64-bit ARM (arm64-v8a). "
            "PocketLLM requires a 64-bit ARM Android phone."
        )
    return adb, profile


def _run_setup(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    repo_root = _repo_root()
    cache_dir = get_cache_dir(repo_root)

    console.banner(
        f"PocketLLM v{__version__} — Zero-Root Edge AI Setup",
        "Turns any Android phone into a private local AI server + Web UI",
    )

    console.step(1, 6, "Connecting to Android phone over ADB")
    adb, profile = _connect_and_probe(args, console, cache_dir)

    console.step(2, 6, "Inspecting hardware & selecting best AI model")
    _print_device_summary(profile, console)
    rec = resolve_model(args.model, profile)
    if args.non_interactive or args.model != "auto":
        chosen_model = rec.model
        console.ok(f"Selected model: {chosen_model.name} — {rec.reason}")
    else:
        chosen_model = _choose_model_interactively(rec, console)

    console.step(3, 6, "Provisioning Termux & background persistence")
    ensure_termux_installed(adb, profile, cache_dir, console)
    apply_power_optimizations(adb, profile, console)
    termux = TermuxBridge(adb, cache_dir, repo_root, console)
    termux.initialize()

    console.step(4, 6, "Installing optimized llama.cpp ARMv8 engine")
    engine_method = ensure_llama_engine(
        termux,
        profile,
        cache_dir,
        repo_root,
        console,
        force_compile=args.compile_from_source,
    )

    console.step(5, 6, f"Downloading & pushing {chosen_model.name}")
    local_gguf = ensure_model_downloaded(chosen_model, cache_dir, console)
    console.info("Pushing GGUF model to phone over USB...")
    termux.push_file(local_gguf, f"models/{chosen_model.filename}")
    console.ok("Model copied to phone storage")
    configure_phone_service(termux, profile, chosen_model, repo_root, port=args.port, ctx=args.ctx)

    console.step(6, 6, "Launching AI Web UI server")
    endpoints: ServerEndpoints = start_server(termux, adb, console, port=args.port)

    tunnel_mode = args.tunnel
    if tunnel_mode == "ask":
        if args.non_interactive or not sys.stdin.isatty():
            tunnel_mode = "none"
        else:
            ans = input("\n  Create a free public HTTPS link via Cloudflare Tunnel (no static IP needed)? [y/N]: ").strip().lower()
            tunnel_mode = "quick" if ans in ("y", "yes") else "none"

    if tunnel_mode == "quick":
        endpoints.public_url = start_cloudflare_tunnel(termux, console)
    elif tunnel_mode == "token":
        if not args.tunnel_token:
            raise ValueError("--tunnel token requires --tunnel-token <TOKEN>")
        start_cloudflare_tunnel(termux, console, token=args.tunnel_token)

    if not args.json:
        print()
        console.banner("PocketLLM is Live!", "Open any link below in your browser to start chatting")
        print(f"  • {console.bold('On this computer')}         : {console.green(endpoints.local_url)}")
        if endpoints.lan_url:
            print(f"  • {console.bold('On your Wi-Fi network')}    : {console.green(endpoints.lan_url)}")
        if endpoints.public_url:
            print(f"  • {console.bold('Public HTTPS (Cloudflare)')}: {console.green(endpoints.public_url)}")
        print(f"  • {console.bold('OpenAI-compatible API')}    : {endpoints.local_url}/v1/chat/completions")
        print()
        console.info("Tip: Screen can be locked and USB unplugged — the AI keeps running on Wi-Fi.")
        console.info("Use `python3 run.py status` or `python3 run.py stop` anytime.")

        if not args.no_open:
            target = endpoints.local_url
            console.info(f"Opening {target} in your browser...")
            try:
                webbrowser.open(target)
            except Exception:
                pass

    return {
        "status": "ok",
        "device": profile.to_dict(),
        "model": chosen_model.to_dict(),
        "engine": engine_method,
        "urls": {
            "local": endpoints.local_url,
            "lan": endpoints.lan_url,
            "public": endpoints.public_url,
            "openai_api": f"{endpoints.local_url}/v1/chat/completions",
        },
    }


def _run_inspect(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    cache_dir = get_cache_dir(_repo_root())
    adb, profile = _connect_and_probe(args, console, cache_dir)
    rec = recommend_model(profile)
    if not args.json:
        _print_device_summary(profile, console)
        print()
        console.ok(f"Recommended model: {rec.model.name}")
        console.info(f"Reason: {rec.reason}")
    return {"status": "ok", "device": profile.to_dict(), "recommended_model": rec.model.to_dict(), "reason": rec.reason}


def _run_models(args: argparse.Namespace, console: Console) -> Dict[str, Any]:
    if not args.json:
        rows = [(m.id, m.tier, f"{m.size_mb} MB", f"{m.min_ram_mb} MB+", m.description) for m in MODEL_CATALOG]
        console.table(("ID", "Target Tier", "Size", "Min RAM", "Description"), rows)
    return {"status": "ok", "models": [m.to_dict() for m in MODEL_CATALOG]}


def _run_ctl_action(args: argparse.Namespace, console: Console, action: str) -> Dict[str, Any]:
    repo_root = _repo_root()
    cache_dir = get_cache_dir(repo_root)
    adb, profile = _connect_and_probe(args, console, cache_dir)
    termux = TermuxBridge(adb, cache_dir, repo_root, console)
    termux.initialize()

    if action == "web":
        endpoints = start_server(termux, adb, console, port=args.port)
        if not args.json:
            print(f"  • Local URL : {endpoints.local_url}")
            if endpoints.lan_url:
                print(f"  • Wi-Fi URL : {endpoints.lan_url}")
            if not args.no_open:
                webbrowser.open(endpoints.local_url)
        return {"status": "ok", "urls": {"local": endpoints.local_url, "lan": endpoints.lan_url}}

    if action == "tunnel":
        url = start_cloudflare_tunnel(termux, console, token=getattr(args, "tunnel_token", None))
        return {"status": "ok", "public_url": url}

    if action == "stop":
        stop_server_and_tunnel(termux, adb, console, port=args.port)
        return {"status": "ok", "stopped": True}

    if action == "status":
        res = termux.run("sh \"$HOME/pocket_ctl.sh\" status", timeout=20, check=True)
        if not args.json:
            print(res.stdout)
        return {"status": "ok", "raw": res.stdout, "wlan_ip": profile.wlan_ip}

    if action == "chat":
        console.info("Starting interactive terminal chat on phone (type /exit or Ctrl+C to quit)...")
        if termux.mode == "ssh" and termux.ssh_key:
            ssh_bin = shutil.which("ssh") or "ssh"
            adb.forward(8022, 8022)
            subprocess.run([
                ssh_bin, "-t", "-p", "8022", "-i", str(termux.ssh_key),
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "127.0.0.1",
                f"sh {termux.TERMUX_HOME}/pocket_ctl.sh chat",
            ])
        else:
            cmd = f"run-as com.termux {termux.TERMUX_USR}/bin/sh {termux.TERMUX_HOME}/pocket_ctl.sh chat"
            subprocess.run([str(adb.adb_path), "-s", adb.serial or "", "shell", "-t", cmd])
        return {"status": "ok"}

    raise ValueError(f"Unknown action: {action}")


def build_parser() -> argparse.ArgumentParser:
    # Shared global options usable before OR after a subcommand without clobbering defaults.
    global_parent = argparse.ArgumentParser(add_help=False)
    global_parent.add_argument("--serial", default=argparse.SUPPRESS, help="Target ADB device serial.")
    global_parent.add_argument("--timeout", type=int, default=argparse.SUPPRESS, help="Seconds to wait for USB device.")
    global_parent.add_argument("--non-interactive", action="store_true", default=argparse.SUPPRESS, help="No prompts.")
    global_parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(
        prog="pocket-llm",
        description="Turn any unrooted Android phone into a local AI server + Web UI.",
    )
    parser.add_argument("--serial", default=None, help="Target ADB device serial (when multiple phones connected).")
    parser.add_argument("--timeout", type=int, default=120, help="Seconds to wait for USB device (default: 120).")
    parser.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output (for AI agents).")

    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", parents=[global_parent], help="Auto-detect hardware, install engine & model, start Web UI.")
    p_setup.add_argument("--model", default="auto", help="Model ID or 'auto' (default: auto).")
    p_setup.add_argument("--port", type=int, default=8080, help="Server HTTP port (default: 8080).")
    p_setup.add_argument("--ctx", type=int, default=None, help="Context window size override.")
    p_setup.add_argument("--tunnel", choices=("ask", "quick", "token", "none"), default="ask", help="Cloudflare Tunnel mode.")
    p_setup.add_argument("--tunnel-token", default=None, help="Cloudflare Tunnel token when --tunnel token is used.")
    p_setup.add_argument("--compile-from-source", action="store_true", help="Force source build instead of prebuilt binary.")
    p_setup.add_argument("--no-open", action="store_true", help="Do not automatically open the browser.")

    sub.add_parser("inspect", parents=[global_parent], help="Inspect phone specs and print the recommended AI model.")
    sub.add_parser("models", parents=[global_parent], help="List all supported models and RAM tiers.")

    p_web = sub.add_parser("web", parents=[global_parent], help="Start/resume the Web UI server and forward port.")
    p_web.add_argument("--port", type=int, default=8080)
    p_web.add_argument("--no-open", action="store_true")

    p_tun = sub.add_parser("tunnel", parents=[global_parent], help="Expose running phone AI server via Cloudflare Tunnel.")
    p_tun.add_argument("--tunnel-token", default=None, help="Optional Cloudflare Zero Trust tunnel token.")

    p_stop = sub.add_parser("stop", parents=[global_parent], help="Stop the phone AI server and tunnel.")
    p_stop.add_argument("--port", type=int, default=8080)

    sub.add_parser("status", parents=[global_parent], help="Show running server status, phone IP, and memory usage.")
    sub.add_parser("chat", parents=[global_parent], help="Open an interactive terminal chat directly on the phone.")
    return parser


def _normalize_argv(argv: List[str]) -> List[str]:
    """Default to the `setup` subcommand when no subcommand is passed (unless requesting --help)."""
    if "-h" in argv or "--help" in argv:
        return argv
    for token in argv:
        if token in SUBCOMMANDS:
            return argv
    return ["setup", *argv]


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    norm_argv = _normalize_argv(raw_argv)
    parser = build_parser()
    args = parser.parse_args(norm_argv)

    if getattr(args, "json", False):
        args.non_interactive = True
    console = Console(json_mode=getattr(args, "json", False))

    cmd = args.command or "setup"
    try:
        if cmd == "models":
            result = _run_models(args, console)
        elif cmd == "inspect":
            result = _run_inspect(args, console)
        elif cmd == "setup":
            result = _run_setup(args, console)
        else:
            result = _run_ctl_action(args, console, cmd)

        if args.json:
            print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        console.warn("Interrupted by user.")
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            console.err(str(exc))
        return 1
