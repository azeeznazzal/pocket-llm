"""llama.cpp binary installer (official multi-CPU Android prebuilts + source fallback)."""

from __future__ import annotations

import os
import posixpath
import re
import tarfile
from pathlib import Path
from typing import Optional, Tuple

from .console import Console
from .detector import DeviceProfile
from .models import ModelSpec
from .termux import TermuxBridge
from .utils import download_file, fetch_json

LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
LLAMA_FALLBACK_TAG = "b12360"
LLAMA_FALLBACK_ASSET = "llama-b12360-bin-android-arm64.tar.gz"
LLAMA_FALLBACK_URL = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_FALLBACK_TAG}/{LLAMA_FALLBACK_ASSET}"
)

_RUNTIME_EXACT = {
    "llama-server",
    "llama-cli",
    "llama-bench",
    "LICENSE",
    "libggml.so",
    "libggml-base.so",
    "libllama.so",
    "libmtmd.so",
    "libllama-common.so",
    "libllama-server-impl.so",
    "libllama-cli-impl.so",
    "libllama-bench-impl.so",
}
_RUNTIME_PREFIXES = ("libggml-cpu-",)


def resolve_llama_android_asset() -> Tuple[str, str]:
    """Return (filename, download_url) for the latest official android-arm64 llama.cpp release."""
    pattern = re.compile(r"^llama-.*-bin-android-arm64\.tar\.gz$")
    try:
        releases = fetch_json(LLAMA_RELEASES_API, timeout=15)
        for rel in releases:
            for asset in rel.get("assets", []):
                name = asset.get("name", "")
                if pattern.match(name):
                    return name, str(asset["browser_download_url"])
    except Exception:
        pass
    return LLAMA_FALLBACK_ASSET, LLAMA_FALLBACK_URL


def _is_runtime_member(basename: str) -> bool:
    if basename in _RUNTIME_EXACT:
        return True
    return basename.endswith(".so") and basename.startswith(_RUNTIME_PREFIXES)


def build_slim_runtime_tarball(full_tarball: Path, slim_tarball: Path) -> Path:
    """Repack only server/cli/bench and runtime .so plugins into a flat, slim tarball."""
    slim_tarball.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = Path(f"{slim_tarball}.part")
    count = 0
    with tarfile.open(full_tarball, "r:gz") as src, tarfile.open(tmp_out, "w:gz", compresslevel=3) as dst:
        for member in src.getmembers():
            if not (member.isfile() or member.issym()):
                continue
            base = posixpath.basename(member.name)
            if not _is_runtime_member(base):
                continue
            member_copy = tarfile.TarInfo(name=base)
            member_copy.size = 0 if member.issym() else member.size
            member_copy.mode = 0o755 if base != "LICENSE" else 0o644
            member_copy.type = member.type
            member_copy.linkname = member.linkname
            if member.isfile():
                fh = src.extractfile(member)
                dst.addfile(member_copy, fh)
            else:
                dst.addfile(member_copy)
            count += 1
    if count < 5:
        raise RuntimeError(f"Unexpected tarball layout in {full_tarball.name}: matched only {count} runtime files.")
    os.replace(tmp_out, slim_tarball)
    return slim_tarball


def _verify_engine_on_phone(termux: TermuxBridge) -> bool:
    res = termux.run(
        "LD_LIBRARY_PATH=\"$HOME/llama:$PREFIX/lib\" \"$HOME/llama/llama-server\" --version >/dev/null 2>&1 "
        "&& LD_LIBRARY_PATH=\"$HOME/llama:$PREFIX/lib\" \"$HOME/llama/llama-cli\" --version",
        timeout=20,
    )
    return res.returncode == 0


def ensure_llama_engine(
    termux: TermuxBridge,
    profile: DeviceProfile,
    cache_dir: Path,
    repo_root: Path,
    console: Console,
    force_compile: bool = False,
) -> str:
    """Install and verify `llama.cpp` binaries inside Termux `~/llama`. Returns method used."""
    if not force_compile and _verify_engine_on_phone(termux):
        console.ok("Verified existing llama.cpp binaries on phone")
        return "existing"

    if not force_compile and profile.sdk_int >= 28 and profile.is_arm64:
        llama_cache = cache_dir / "llama"
        asset_name, url = resolve_llama_android_asset()
        full_tar = llama_cache / asset_name
        slim_tar = llama_cache / f"slim-{asset_name}"

        try:
            console.info(f"Fetching official precompiled llama.cpp Android ARM64 ({asset_name})...")
            download_file(url, full_tar, console, label=asset_name, min_bytes=10_000_000)

            if not slim_tar.is_file():
                console.info("Packaging minimal runtime bundle (server + cli + multi-ARMv8 CPU backends)...")
                build_slim_runtime_tarball(full_tar, slim_tar)

            console.info(f"Pushing engine bundle ({slim_tar.stat().st_size / (1024 * 1024):.1f} MB) to phone...")
            remote_tar = termux.push_file(slim_tar, "llama-runtime.tar.gz")
            termux.run(
                f"mkdir -p \"$HOME/llama\" && tar -xzf '{remote_tar}' -C \"$HOME/llama\" && rm -f '{remote_tar}'",
                timeout=120,
                check=True,
            )

            if _verify_engine_on_phone(termux):
                console.ok("Precompiled llama.cpp engine verified on device (zero-compile install!)")
                return "prebuilt"
            console.warn("Precompiled binary verification failed on this ROM; falling back to source build...")
        except Exception as exc:
            console.warn(f"Prebuilt install failed ({exc}); falling back to on-device source compile...")

    # Fallback: compile directly inside Termux tailored to detected ARM flags.
    arch_flag = "armv8.2-a+dotprod" if profile.has_dotprod else "armv8-a"
    console.info(f"Compiling llama.cpp on device for {arch_flag} (-j4 thermal cap, ~10-20 min)...")
    build_script = repo_root / "scripts" / "build_llama.sh"
    remote_build = termux.push_file(build_script, "build_llama.sh", mode="755")
    termux.run(f"sh '{remote_build}' '{arch_flag}'", timeout=2400, check=True)
    if not _verify_engine_on_phone(termux):
        raise RuntimeError("llama.cpp build finished but binaries failed verification.")
    console.ok("On-device llama.cpp build verified")
    return "compiled"


def configure_phone_service(
    termux: TermuxBridge,
    profile: DeviceProfile,
    model: ModelSpec,
    repo_root: Path,
    port: int = 8080,
    ctx: Optional[int] = None,
) -> None:
    """Upload `pocket_ctl.sh` and write `~/.pocket_config.env` inside Termux."""
    ctl_script = repo_root / "scripts" / "pocket_ctl.sh"
    termux.push_file(ctl_script, "pocket_ctl.sh", mode="755")

    effective_ctx = ctx or model.recommended_ctx
    remote_model = f"{termux.TERMUX_HOME}/models/{model.filename}"
    cfg = (
        f"LLAMA_DIR='{termux.TERMUX_HOME}/llama'\n"
        f"MODEL='{remote_model}'\n"
        f"CORE_MASK='{profile.recommended_core_mask}'\n"
        f"THREADS='{profile.recommended_threads}'\n"
        f"CTX='{effective_ctx}'\n"
        f"PORT='{port}'\n"
    )
    termux.run(f"cat > \"$HOME/.pocket_config.env\" << 'EOF'\n{cfg}EOF\n", timeout=15, check=True)
