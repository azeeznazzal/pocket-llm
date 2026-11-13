"""Android device hardware profiler via non-root ADB shell inspection."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Tuple

from .adb import AdbClient

# Single atomic probe command minimized for fast execution across Android 8-15.
_PROBE_CMD = (
    "echo __PROPS__; "
    "for k in ro.product.manufacturer ro.product.model ro.build.version.release "
    "ro.build.version.sdk ro.product.cpu.abi ro.board.platform ro.soc.model ro.hardware; do "
    "echo \"$k=$(getprop $k)\"; done; "
    "echo __MEM__; cat /proc/meminfo | head -n 15; "
    "echo __DF__; df -k /data | tail -n 1; "
    "echo __CPU__; grep -E 'processor|Features|CPU architecture' /proc/cpuinfo | head -n 30; "
    "echo __FREQ__; for c in /sys/devices/system/cpu/cpu[0-9]*; do "
    "f=$(cat $c/cpufreq/cpuinfo_max_freq 2>/dev/null || echo 0); "
    "echo \"$(basename $c)=$f\"; done; "
    "echo __IP__; ip -4 -o addr show wlan0 2>/dev/null || ifconfig wlan0 2>/dev/null || true"
)


@dataclass
class DeviceProfile:
    """Hardware profile of a connected Android phone."""

    serial: str
    manufacturer: str
    model: str
    android_version: str
    sdk_int: int
    abi: str
    soc: str
    total_ram_mb: int
    available_ram_mb: int
    swap_total_mb: int
    free_storage_gb: float
    cpu_cores: int
    core_max_freqs_mhz: Dict[int, int] = field(default_factory=dict)
    cpu_features: List[str] = field(default_factory=list)
    recommended_cores: str = ""
    recommended_core_mask: str = ""
    recommended_threads: int = 4
    wlan_ip: str = ""

    @property
    def is_arm64(self) -> bool:
        return self.abi.startswith("arm64") or "aarch64" in self.abi

    @property
    def has_dotprod(self) -> bool:
        return "asimddp" in self.cpu_features or "dotprod" in self.cpu_features

    @property
    def has_fp16(self) -> bool:
        return "fphp" in self.cpu_features or "asimdhp" in self.cpu_features

    @property
    def has_i8mm(self) -> bool:
        return "i8mm" in self.cpu_features

    @property
    def arm_arch_label(self) -> str:
        if self.has_i8mm:
            return "ARMv8.6+ (i8mm/dotprod/fp16)"
        if self.has_dotprod:
            return "ARMv8.2+ (dotprod/fp16)"
        return "ARMv8.0-A (NEON/ASIMD baseline)"

    @property
    def oem_key(self) -> str:
        m = self.manufacturer.strip().lower()
        for key in ("samsung", "xiaomi", "redmi", "poco", "google", "oneplus", "oppo", "realme", "motorola", "huawei"):
            if key in m:
                if key in ("redmi", "poco"):
                    return "xiaomi"
                return key
        return "generic"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_arm64"] = self.is_arm64
        d["arm_arch_label"] = self.arm_arch_label
        d["has_dotprod"] = self.has_dotprod
        d["has_fp16"] = self.has_fp16
        return d


def select_core_pinning(core_freqs_mhz: Dict[int, int], total_cores: int) -> Tuple[str, str, int]:
    """
    Pick optimal CPU core mask and thread count to balance throughput and thermals.

    Returns (human_ranges, hex_bitmask, thread_count).
    """
    valid = {cid: freq for cid, freq in core_freqs_mhz.items() if freq > 0}
    if not valid or len(valid) < 2:
        threads = max(1, min(4, total_cores or 4))
        return "", "", threads

    min_freq = min(valid.values())
    max_freq = max(valid.values())
    # Homogeneous SMP (all cores same clock)
    if max_freq <= min_freq * 1.05:
        threads = min(4, len(valid))
        return "", "", threads

    # Sort cores fastest-first, breaking ties by higher core ID (typical Android big-cluster ordering)
    ordered = sorted(valid.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    fast_cores = [cid for cid, freq in ordered if freq >= min_freq * 1.08]

    if len(fast_cores) >= 4:
        chosen = sorted(fast_cores[:4])
    elif len(ordered) >= 4:
        # e.g. 2x big A73 + 6x little A53 -> take top 4 cores (2x big + 2x little)
        chosen = sorted(cid for cid, _ in ordered[:4])
    else:
        chosen = sorted(valid.keys())

    mask = sum(1 << cid for cid in chosen)
    return _format_core_list(chosen), f"{mask:x}", len(chosen)


def _format_core_list(cores: List[int]) -> str:
    """Format sorted integer core IDs concisely, e.g. [4,5,6,7] -> '4-7'."""
    if not cores:
        return ""
    ranges: List[str] = []
    start = prev = cores[0]
    for cid in cores[1:]:
        if cid == prev + 1:
            prev = cid
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = cid
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def parse_probe_output(serial: str, raw: str) -> DeviceProfile:
    """Parse raw output from `_PROBE_CMD` into a structured DeviceProfile."""
    sections: Dict[str, List[str]] = {}
    current = ""
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("__") and s.endswith("__"):
            current = s
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(s)

    props: Dict[str, str] = {}
    for line in sections.get("__PROPS__", []):
        if "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    mem: Dict[str, int] = {}
    for line in sections.get("__MEM__", []):
        m = re.match(r"^(\w+):\s+(\d+)\s+kB", line)
        if m:
            mem[m.group(1)] = int(m.group(2)) // 1024

    free_gb = 0.0
    for line in sections.get("__DF__", []):
        parts = line.split()
        # POSIX df -k columns: Filesystem 1K-blocks Used Available Use% Mounted
        if len(parts) >= 4 and parts[3].isdigit():
            free_gb = round(int(parts[3]) / (1024 * 1024), 2)

    features: List[str] = []
    proc_count = 0
    for line in sections.get("__CPU__", []):
        if line.startswith("processor"):
            proc_count += 1
        elif line.lower().startswith("features") and ":" in line:
            feats = line.split(":", 1)[1].strip().split()
            if feats:
                features = feats

    freqs: Dict[int, int] = {}
    for line in sections.get("__FREQ__", []):
        m = re.match(r"^cpu(\d+)=(\d+)$", line)
        if m:
            cid, khz = int(m.group(1)), int(m.group(2))
            if khz > 0:
                freqs[cid] = khz // 1000

    total_cores = max(proc_count, len(freqs), 1)
    core_mask_str, core_mask_hex, threads = select_core_pinning(freqs, total_cores)

    wlan_ip = ""
    ip_text = "\n".join(sections.get("__IP__", []))
    m_ip = re.search(r"inet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)", ip_text)
    if m_ip:
        wlan_ip = m_ip.group(1)

    soc = props.get("ro.soc.model") or props.get("ro.board.platform") or props.get("ro.hardware") or "unknown"
    try:
        sdk_int = int(props.get("ro.build.version.sdk") or 0)
    except ValueError:
        sdk_int = 0

    return DeviceProfile(
        serial=serial,
        manufacturer=props.get("ro.product.manufacturer") or "Android",
        model=props.get("ro.product.model") or "Unknown",
        android_version=props.get("ro.build.version.release") or "?",
        sdk_int=sdk_int,
        abi=props.get("ro.product.cpu.abi") or "unknown",
        soc=soc,
        total_ram_mb=mem.get("MemTotal", 0),
        available_ram_mb=mem.get("MemAvailable", mem.get("MemFree", 0)),
        swap_total_mb=mem.get("SwapTotal", 0),
        free_storage_gb=free_gb,
        cpu_cores=total_cores,
        core_max_freqs_mhz=freqs,
        cpu_features=features,
        recommended_cores=core_mask_str,
        recommended_core_mask=core_mask_hex,
        recommended_threads=threads,
        wlan_ip=wlan_ip,
    )


def inspect_device(adb: AdbClient) -> DeviceProfile:
    """Run hardware probe on the connected Android phone via ADB."""
    res = adb.shell(_PROBE_CMD, timeout=20, check=True)
    return parse_probe_output(adb.serial or "unknown", res.stdout)
