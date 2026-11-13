"""Terminal UI helpers with zero third-party dependencies and cross-platform color support."""

from __future__ import annotations

import os
import sys
from typing import Iterable, Sequence


def _enable_windows_vt() -> bool:
    """Enable ANSI escape sequences on Windows 10+ consoles when attached to a TTY."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return "WT_SESSION" in os.environ or "TERM" in os.environ


class Console:
    """Lightweight console printer supporting quiet/JSON modes and clean tables."""

    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode
        is_tty = sys.stdout.isatty() and not json_mode
        self.color = is_tty and _enable_windows_vt() and not os.environ.get("NO_COLOR")

    def _c(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._c("1", text)

    def dim(self, text: str) -> str:
        return self._c("2", text)

    def green(self, text: str) -> str:
        return self._c("32", text)

    def yellow(self, text: str) -> str:
        return self._c("33", text)

    def red(self, text: str) -> str:
        return self._c("31", text)

    def cyan(self, text: str) -> str:
        return self._c("36", text)

    def banner(self, title: str, subtitle: str = "") -> None:
        if self.json_mode:
            return
        line = "=" * 62
        print(self.cyan(line))
        print(self.bold(f"  {title}"))
        if subtitle:
            print(self.dim(f"  {subtitle}"))
        print(self.cyan(line))

    def step(self, idx: int, total: int, message: str) -> None:
        if self.json_mode:
            return
        tag = self.cyan(f"[{idx}/{total}]")
        print(f"\n{tag} {self.bold(message)}")

    def ok(self, message: str) -> None:
        if not self.json_mode:
            print(f"  {self.green('✓')} {message}")

    def info(self, message: str) -> None:
        if not self.json_mode:
            print(f"  {self.cyan('•')} {message}")

    def warn(self, message: str) -> None:
        if not self.json_mode:
            print(f"  {self.yellow('!')} {message}", file=sys.stderr)

    def err(self, message: str) -> None:
        if not self.json_mode:
            print(f"  {self.red('✗')} {message}", file=sys.stderr)

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
        if self.json_mode:
            return
        rows_list = [list(map(str, r)) for r in rows]
        widths = [len(h) for h in headers]
        for row in rows_list:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(cell))

        fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
        print(self.bold(fmt.format(*headers)))
        print("  " + "  ".join("-" * w for w in widths))
        for row in rows_list:
            padded = row + [""] * (len(headers) - len(row))
            print(fmt.format(*padded))

    def progress(self, prefix: str, done: int, total: int) -> None:
        if self.json_mode or not sys.stdout.isatty():
            return
        if total <= 0:
            mb = done / (1024 * 1024)
            sys.stdout.write(f"\r  {prefix}: {mb:.1f} MB")
            sys.stdout.flush()
            return
        pct = min(100.0, done * 100.0 / total)
        done_mb = done / (1024 * 1024)
        tot_mb = total / (1024 * 1024)
        bar_len = 24
        filled = int(bar_len * pct / 100.0)
        bar = "#" * filled + "-" * (bar_len - filled)
        sys.stdout.write(f"\r  {prefix} [{bar}] {pct:5.1f}% ({done_mb:.1f}/{tot_mb:.1f} MB)")
        sys.stdout.flush()

    def progress_done(self) -> None:
        if not self.json_mode and sys.stdout.isatty():
            sys.stdout.write("\n")
            sys.stdout.flush()
