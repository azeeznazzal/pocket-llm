# PocketLLM

**Turn any Android phone into a private, local AI server & ChatGPT-style Web UI in minutes — no root required.**

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Host OS](https://img.shields.io/badge/host-macOS%20%7C%20Linux%20%7C%20Windows-informational)](#quickstart-for-humans)
[![Android](https://img.shields.io/badge/target-Android%209%2B%20(arm64)--no--root-brightgreen)](#supported-phones--model-tiers)
[![Agent Friendly](https://img.shields.io/badge/AI%20Agent-Ready-purple)](AGENT.md)

Plug in an old Android phone via USB, run **one command**, and open a browser link to start chatting with the best local LLM your phone's hardware can handle.

- **100% Automated:** Auto-installs `adb` if missing, installs Termux, detects CPU/RAM/SIMD hardware, picks the optimal quantized GGUF model, deploys prebuilt ARMv8 `llama.cpp` binaries, and launches the Web UI.
- **No Root Required:** Works on stock locked-bootloader phones (Samsung, Google Pixel, Xiaomi, OnePlus, Oppo, Motorola, Nothing).
- **Cross-Platform Host:** Runs identically on **macOS**, **Linux**, and **Windows** using zero external Python packages.
- **Works With Screen Locked & Unplugged:** Configures Android Doze whitelisting, `termux-wake-lock`, standby exemption, and Wi-Fi persistence so any device on your network can keep chatting after you unplug the cable.
- **Optional Public URL (No Static IP Needed):** One-click integration with **Cloudflare Tunnel** (`trycloudflare.com` or your own domain).

---

## Quickstart for Humans

### 1. Enable USB Debugging on Your Phone
1. Open **Settings → About phone** and tap **Build number** 7 times.
2. Open **Settings → Developer options** and turn on **USB debugging**.
3. Connect the phone to your computer via USB *(set USB mode to **File transfer** and tap **Allow** on the phone prompt)*.

### 2. Clone & Run

**macOS / Linux:**
```bash
git clone https://github.com/azeeznazzal/pocket-llm.git
cd pocket-llm
./setup.sh
```

**Windows (Command Prompt or PowerShell):**
```powershell
git clone https://github.com/azeeznazzal/pocket-llm.git
cd pocket-llm
setup.bat
```

Your browser will automatically open the Web UI (`http://127.0.0.1:8080`), and you can also connect from any phone, tablet, or laptop on the same Wi-Fi at `http://<PHONE_WIFI_IP>:8080`.

---

## Quickstart for AI Agents (Zero-Touch Setup)

Have **Antigravity, Claude Code, Cursor, or Codex** set everything up for you. Just plug your phone in via USB and paste this prompt to your AI agent:

> Set up a local AI server on my connected Android phone using `https://github.com/azeeznazzal/pocket-llm`. Follow `AGENT.md` in the repository and give me the Web UI URL when ready.

---

## How Hardware Auto-Detection Works

Every Android SoC has different CPU extensions and memory limits. Running an `ARMv8.2+ dotprod` binary on an older `ARMv8.0` phone crashes with `SIGILL`, while running a 3B model on a 3 GB phone triggers Android's Low Memory Killer.

`pocket-llm` probes your device before installing anything:
1. **CPU Instruction Set:** Detects `asimd`, `dotprod` (`asimddp`), `fp16` (`fphp`), and `i8mm` via `/proc/cpuinfo` and dynamically loads the matching backend (`armv8.0` up to `armv9.2`).
2. **big.LITTLE Core Pinning:** Reads `/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq` to pin inference threads onto performance cores using a universal `taskset` bitmask.
3. **RAM & Storage Matching:** Inspects kernel `MemTotal` and `/data` free space to select the strongest model that stays comfortably inside physical RAM without swap thrashing.

### Supported Phones & Model Tiers

| Usable Kernel RAM | Typical Devices | Auto-Selected Model | Size |
|---|---|---|---|
| **< 2.2 GB** | Older 2 GB budget phones | `SmolLM2-360M-Instruct` (`Q4_K_M`) | 258 MB |
| **2.2 – 3.3 GB** | 3 GB phones (e.g. Samsung Galaxy A20) | `Qwen2.5-0.5B-Instruct` (`Q4_K_M`) | 469 MB |
| **3.3 – 4.8 GB** | 4 GB midrange phones | `Llama-3.2-1B-Instruct` (`Q4_K_M`) | 770 MB |
| **4.8 – 6.8 GB** | 6 GB phones | `Qwen2.5-1.5B-Instruct` (`Q4_K_M`) | 1.04 GB |
| **6.8 – 10.2 GB** | 8 GB phones (Pixel, Galaxy S series) | `Llama-3.2-3B` / `Qwen2.5-3B` (`Q4_K_M`) | ~1.9 GB |
| **10.2 GB+** | 12–16 GB flagships | `Qwen2.5-7B-Instruct` (`Q4_K_M`) | 4.36 GB |

*(You can inspect your phone first without changing anything via `python3 run.py inspect`, or override the model anytime with `--model <id>`).*

---

## Real-World Benchmark (Stock Unrooted Samsung Galaxy A20)

Tested on an **Exynos 7884** (2× Cortex-A73 @ 1.56 GHz + 6× Cortex-A53 @ 1.35 GHz, **3 GB RAM**, Android 11, no root) running `Qwen2.5-0.5B-Instruct-Q4_K_M`:

| Configuration | Threads | Prompt Eval (`pp64`) | Generation (`tg32`) |
|---|---|---|---|
| **Auto-Pinned (`cores 4-7`, default)** | 4 | **18.17 tok/s** | **10.28 tok/s** |
| Big Cores Only (`cores 6-7`) | 2 | 12.81 tok/s | 8.95 tok/s |
| Little Cores Only (`cores 0-3`) | 4 | 10.62 tok/s | 6.68 tok/s |

- **RAM Before Model:** 1,326 MiB available
- **RAM During Active Inference:** 1,224 MiB available
- **Overnight Persistence:** Verified running unplugged with screen locked for 16+ hours on Wi-Fi.

---

## Everyday Commands

```bash
python3 run.py inspect      # Show detected phone specs & recommended model
python3 run.py models       # List all supported models and RAM tiers
python3 run.py web          # Start Web UI server & open in browser
python3 run.py chat         # Interactive terminal chat right in your console
python3 run.py tunnel       # Create a public https://*.trycloudflare.com URL
python3 run.py status       # Check server PID, Wi-Fi IP, and free RAM
python3 run.py stop         # Stop AI server & release wake-lock to save battery
```

---

## Expose Publicly Without a Static IP (Cloudflare Tunnel)

Want to access your phone's AI from anywhere in the world — even on cellular data behind CGNAT?

1. **Instant Temporary Link (no Cloudflare account required):**
   ```bash
   python3 run.py tunnel
   ```
   Outputs a public `https://random-words.trycloudflare.com` URL.

2. **Permanent Custom Domain (Cloudflare Zero Trust):**
   ```bash
   python3 run.py tunnel --tunnel-token <YOUR_CLOUDFLARE_TUNNEL_TOKEN>
   ```
   > **Security tip:** When exposing publicly, enable **Cloudflare Access** (Email OTP / Google login) in your Cloudflare Zero Trust dashboard so strangers cannot burn your phone's CPU or battery.

---

## OEM-Specific Tips & Troubleshooting

- **Xiaomi / Redmi / POCO (MIUI & HyperOS):** If APK installation is rejected (`INSTALL_FAILED_USER_RESTRICTED`), enable **Developer options → Install via USB** (and **USB debugging (Security settings)** if prompted). Also set Termux Battery Saver to **No restrictions**.
- **Samsung One UI:** `pocket-llm` automatically adds Termux to the Doze whitelist and sets `wifi_sleep_policy=2`. If Samsung later flags Termux under *Device Care → Battery → Sleeping apps*, move it to *Never sleeping apps*.
- **Oppo / Realme / OnePlus:** Enable **Disable Permission Monitoring** in Developer Options if ADB install fails.
- **Windows:** If your phone doesn't appear under `adb devices`, install your phone brand's USB driver (or Google USB Driver) and ensure you're using a data-capable USB cable.
- **Android 7 / 8 (`SDK < 28`):** Prebuilt binaries require Android 9+. Older devices automatically fall back to building `llama.cpp` directly inside Termux (takes ~15 minutes).

---

## License

MIT © [Azeez Nazzal](https://github.com/azeeznazzal) — Powered by [llama.cpp](https://github.com/ggml-org/llama.cpp) and [Termux](https://github.com/termux/termux-app).
