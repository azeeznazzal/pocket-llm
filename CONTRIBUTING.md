# Contributing to PocketLLM

Contributions are welcome! Whether it's tuning model tiers for new SoCs, adding OEM-specific compatibility fixes, or expanding tests, thank you for helping make edge AI accessible on everyday phones.

## Guidelines
1. Keep the host runner **zero-dependency** (Python 3.8+ standard library only) so anyone can run it on macOS, Linux, or Windows without `pip install`.
2. Keep all phone-side scripts POSIX `sh` compatible (`/system/bin/sh` and Termux `sh`), avoiding GNU-only flags that fail on Android `toybox`.
3. Add unit tests under `tests/` for any new hardware detection or model recommendation logic.

## Running Tests Locally
```bash
python3 -m unittest discover -s tests -v
```
