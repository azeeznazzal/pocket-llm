"""Model catalog, hardware-aware recommendation engine, and GGUF downloader."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .console import Console
from .detector import DeviceProfile
from .utils import download_file


@dataclass(frozen=True)
class ModelSpec:
    """Metadata for a supported quantized GGUF model."""

    id: str
    name: str
    filename: str
    url: str
    size_mb: int
    min_ram_mb: int
    recommended_ctx: int
    tier: str
    description: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


MODEL_CATALOG: Tuple[ModelSpec, ...] = (
    ModelSpec(
        id="smollm2-360m",
        name="SmolLM2-360M-Instruct (Q4_K_M)",
        filename="SmolLM2-360M-Instruct-Q4_K_M.gguf",
        url="https://huggingface.co/bartowski/SmolLM2-360M-Instruct-GGUF/resolve/main/SmolLM2-360M-Instruct-Q4_K_M.gguf",
        size_mb=258,
        min_ram_mb=1400,
        recommended_ctx=2048,
        tier="ultra-lite (<2 GB RAM)",
        description="Ultra-lightweight model for older 1.5-2 GB RAM devices.",
    ),
    ModelSpec(
        id="qwen2.5-0.5b",
        name="Qwen2.5-0.5B-Instruct (Q4_K_M)",
        filename="qwen2.5-0.5b-instruct-q4_k_m.gguf",
        url="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        size_mb=469,
        min_ram_mb=2200,
        recommended_ctx=2048,
        tier="budget (3 GB RAM)",
        description="Fast, coherent model tuned for 3 GB RAM phones (e.g. Galaxy A20).",
    ),
    ModelSpec(
        id="llama3.2-1b",
        name="Llama-3.2-1B-Instruct (Q4_K_M)",
        filename="Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        url="https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        size_mb=770,
        min_ram_mb=3300,
        recommended_ctx=2048,
        tier="midrange (4 GB RAM)",
        description="Balanced 1B general assistant for 4 GB RAM smartphones.",
    ),
    ModelSpec(
        id="qwen2.5-1.5b",
        name="Qwen2.5-1.5B-Instruct (Q4_K_M)",
        filename="qwen2.5-1.5b-instruct-q4_k_m.gguf",
        url="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        size_mb=1066,
        min_ram_mb=4800,
        recommended_ctx=4096,
        tier="upper-midrange (6 GB RAM)",
        description="Strong reasoning, coding, and multilingual model for 6 GB RAM phones.",
    ),
    ModelSpec(
        id="llama3.2-3b",
        name="Llama-3.2-3B-Instruct (Q4_K_M)",
        filename="Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        url="https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        size_mb=1926,
        min_ram_mb=6800,
        recommended_ctx=4096,
        tier="flagship (8 GB RAM)",
        description="High-capability 3B assistant for 8 GB RAM devices with dotprod.",
    ),
    ModelSpec(
        id="qwen2.5-3b",
        name="Qwen2.5-3B-Instruct (Q4_K_M)",
        filename="qwen2.5-3b-instruct-q4_k_m.gguf",
        url="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        size_mb=2008,
        min_ram_mb=7000,
        recommended_ctx=4096,
        tier="flagship (8 GB RAM)",
        description="Top-tier 3B model for coding and structured instructions on 8 GB phones.",
    ),
    ModelSpec(
        id="qwen2.5-7b",
        name="Qwen2.5-7B-Instruct (Q4_K_M)",
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        url="https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        size_mb=4466,
        min_ram_mb=10200,
        recommended_ctx=4096,
        tier="ultra-flagship (12 GB+ RAM)",
        description="Desktop-class 7B assistant for 12-16 GB RAM flagship phones.",
    ),
)

CATALOG_BY_ID: Dict[str, ModelSpec] = {m.id: m for m in MODEL_CATALOG}


@dataclass
class Recommendation:
    """Best-fit model selection with human-readable rationale."""

    model: ModelSpec
    reason: str
    alternatives: List[ModelSpec]


def recommend_model(profile: DeviceProfile) -> Recommendation:
    """
    Determine the strongest model a device can run comfortably without Android LMK kills.

    Accounts for Android kernel memory reserve (~10% below marketed RAM), OS overhead,
    free storage space, and CPU SIMD capability (ARMv8.2+ dotprod vs ARMv8.0).
    """
    ram = profile.total_ram_mb
    storage_mb = int(profile.free_storage_gb * 1024)

    # Filter models that fit both total RAM and free storage (+450 MB safety margin for runtime/Termux).
    viable = [
        m for m in MODEL_CATALOG
        if ram >= m.min_ram_mb and (storage_mb <= 0 or storage_mb >= m.size_mb + 450)
    ]

    if not viable:
        chosen = MODEL_CATALOG[0]
        reason = (
            f"Device reports {ram} MB total RAM and {profile.free_storage_gb:.1f} GB free storage; "
            f"selecting smallest ultra-lite model ({chosen.size_mb} MB) to prevent out-of-memory kills."
        )
        return Recommendation(model=chosen, reason=reason, alternatives=list(MODEL_CATALOG[:2]))

    # On older ARMv8.0 CPUs without dotprod, cap auto-pick at 1.5B so token generation stays interactive (>5 t/s).
    if not profile.has_dotprod:
        snappy = [m for m in viable if m.size_mb <= 1100]
        if snappy:
            chosen = snappy[-1]
            reason = (
                f"{ram} MB RAM + {profile.arm_arch_label} (no dotprod): "
                f"{chosen.name} maximizes quality while keeping generation responsive without thermal lag."
            )
            return Recommendation(model=chosen, reason=reason, alternatives=viable)

    chosen = viable[-1]
    # Prefer Qwen2.5-3B or Llama-3.2-3B cleanly at the 8 GB tier; both are in viable[-2:].
    reason = (
        f"{ram} MB kernel RAM, {profile.free_storage_gb:.1f} GB free storage, and {profile.arm_arch_label}: "
        f"fits cleanly in memory without Android Low Memory Killer eviction."
    )
    return Recommendation(model=chosen, reason=reason, alternatives=viable)


def resolve_model(model_arg: str, profile: DeviceProfile) -> Recommendation:
    """Resolve '--model auto' or a specific model ID from the catalog."""
    if not model_arg or model_arg.lower() == "auto":
        return recommend_model(profile)
    key = model_arg.lower().strip()
    if key not in CATALOG_BY_ID:
        valid = ", ".join(CATALOG_BY_ID.keys())
        raise ValueError(f"Unknown model '{model_arg}'. Choose 'auto' or one of: {valid}")
    chosen = CATALOG_BY_ID[key]
    return Recommendation(
        model=chosen,
        reason=f"Explicitly selected by user/agent (--model {chosen.id}).",
        alternatives=list(MODEL_CATALOG),
    )


def ensure_model_downloaded(model: ModelSpec, cache_dir: Path, console: Console) -> Path:
    """Download the GGUF model to host cache if not already present."""
    models_dir = cache_dir / "models"
    dest = models_dir / model.filename
    console.info(f"Preparing model {model.name} (~{model.size_mb} MB)...")
    return download_file(
        model.url,
        dest,
        console,
        label=model.filename,
        min_bytes=50_000_000,
        timeout=120,
    )
