# AGENT.md — Instructions for AI Coding Agents

If a user gives you this repository link and asks you to set up a local AI model on their Android phone, follow this protocol verbatim.

## Capabilities & Requirements
- **Zero root required:** Works on stock, locked-bootloader Android 9+ phones (`arm64-v8a` / `aarch64`) via Termux.
- **Zero host dependencies:** Requires only Python 3.8+ (`adb` is automatically downloaded if missing).
- **Platforms:** Works identically on macOS, Linux, and Windows.

---

## Step 1: Clone the Repository
```bash
git clone https://github.com/azeeznazzal/pocket-llm.git
cd pocket-llm
```

## Step 2: Inspect the Connected Phone
Ask the user to plug in their Android phone via USB and enable **USB Debugging** (Developer Options). Then run:
```bash
python3 run.py inspect --json
```
- If it reports `"unauthorized"`, instruct the user to check their phone screen, tick **"Always allow from this computer"**, and tap **Allow**, then re-run.
- Check `device.is_arm64 == true`. (32-bit `armeabi-v7a` devices are unsupported).

## Step 3: Run Full Autonomous Setup
Run non-interactively so no prompts hang your shell:
```bash
python3 run.py setup --non-interactive --model auto --tunnel none --no-open --json
```
*(Pass `--tunnel quick` instead of `--tunnel none` if the user explicitly requested a public `https://*.trycloudflare.com` URL).*

### Expected JSON Output Schema
```json
{
  "status": "ok",
  "device": {
    "manufacturer": "samsung",
    "model": "SM-A205F",
    "total_ram_mb": 2725,
    "arm_arch_label": "ARMv8.0-A (NEON/ASIMD baseline)",
    "recommended_cores": "4-7",
    "recommended_core_mask": "f0",
    "recommended_threads": 4,
    "wlan_ip": "192.168.1.50"
  },
  "model": {
    "id": "qwen2.5-0.5b",
    "name": "Qwen2.5-0.5B-Instruct (Q4_K_M)"
  },
  "engine": "prebuilt",
  "urls": {
    "local": "http://127.0.0.1:8080",
    "lan": "http://192.168.1.50:8080",
    "public": null,
    "openai_api": "http://127.0.0.1:8080/v1/chat/completions"
  }
}
```

## Step 4: Verify & Present to User
1. Confirm the server responds with `{"status":"ok"}`:
   ```bash
   curl -s http://127.0.0.1:8080/health
   ```
2. Present the exact **Web UI URLs** (`urls.local`, `urls.lan`, and `urls.public` if requested) along with the selected model and hardware summary.

---

## OEM-Specific Failure Recovery
- **`INSTALL_FAILED_USER_RESTRICTED` on Xiaomi / Redmi / POCO (MIUI / HyperOS):** Tell the user to open *Developer Options → Install via USB* (and *USB debugging (Security settings)* if falling back to SSH), then re-run Step 3.
- **`INSTALL_FAILED_UPDATE_INCOMPATIBLE`:** User has an older Play Store Termux installed. Ask them to uninstall Termux before re-running.
- **No device found on Windows:** Ask the user to install the OEM/Google USB driver or switch USB mode on the phone to *File Transfer (MTP)*.

## Management Subcommands
- Start / resume Web UI: `python3 run.py web --no-open`
- Expose via Cloudflare Tunnel: `python3 run.py tunnel --json`
- Check status & live RAM: `python3 run.py status`
- Stop server & release wake-lock: `python3 run.py stop`
