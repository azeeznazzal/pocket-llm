"""Shared host utilities: HTTP downloads, JSON fetches, and cache directory resolution."""

from __future__ import annotations

import json
import os
import platform
import shutil
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .console import Console

USER_AGENT = "pocket-llm/1.0 (+https://github.com/azeeznazzal/pocket-llm)"


def get_cache_dir(repo_root: Optional[Path] = None) -> Path:
    """Return the local cache directory for downloaded tools, APKs, keys, and models."""
    env_dir = os.environ.get("POCKET_LLM_CACHE")
    if env_dir:
        path = Path(env_dir).expanduser().resolve()
    elif repo_root is not None:
        path = repo_root / ".pocket_llm"
    else:
        path = Path.home() / ".pocket_llm"
    path.mkdir(parents=True, exist_ok=True)
    return path


def host_platform() -> str:
    """Return normalized host OS name: 'macos', 'linux', or 'windows'."""
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return "macos"
    if sys_name.startswith("win"):
        return "windows"
    return "linux"


def _ssl_context() -> ssl.SSLContext:
    """Build default SSL context, falling back to certifi if system certs fail on macOS."""
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def fetch_json(url: str, timeout: int = 20) -> Any:
    """Fetch and parse JSON from an HTTP(S) endpoint."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error fetching {url}: {exc.reason}") from exc


def download_file(
    url: str,
    dest: Path,
    console: Console,
    label: Optional[str] = None,
    min_bytes: int = 1024,
    timeout: int = 60,
) -> Path:
    """Download a file atomically with progress display and partial-file resume support."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size >= min_bytes:
        console.ok(f"Using cached {label or dest.name} ({dest.stat().st_size / (1024 * 1024):.1f} MB)")
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    existing = part.stat().st_size if part.exists() else 0
    headers: Dict[str, str] = {"User-Agent": USER_AGENT}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"

    req = urllib.request.Request(url, headers=headers)
    display = label or dest.name

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            status = getattr(resp, "status", 200)
            if status == 206 and existing > 0:
                mode = "ab"
                total = existing + int(resp.headers.get("Content-Length", "0") or 0)
                done = existing
            else:
                mode = "wb"
                total = int(resp.headers.get("Content-Length", "0") or 0)
                done = 0

            with open(part, mode) as fh:
                while True:
                    chunk = resp.read(1024 * 128)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    console.progress(display, done, total)
        console.progress_done()
    except urllib.error.HTTPError as exc:
        console.progress_done()
        if exc.code == 416 and part.exists() and part.stat().st_size >= min_bytes:
            # Partial file already matched remote size.
            pass
        else:
            if part.exists() and exc.code == 416:
                part.unlink()
            raise RuntimeError(f"Failed downloading {url}: HTTP {exc.code} {exc.reason}") from exc
    except Exception as exc:
        console.progress_done()
        raise RuntimeError(f"Failed downloading {url}: {exc}") from exc

    if not part.exists() or part.stat().st_size < min_bytes:
        raise RuntimeError(f"Downloaded file {dest.name} is empty or smaller than expected ({min_bytes} bytes)")

    shutil.move(str(part), str(dest))
    return dest
