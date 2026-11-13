"""Unit tests for PocketLLM hardware detection, model selection, packaging, and CLI parsing."""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from pocket_llm.adb import parse_adb_devices
from pocket_llm.cli import _normalize_argv, build_parser
from pocket_llm.detector import parse_probe_output, select_core_pinning
from pocket_llm.engine import build_slim_runtime_tarball
from pocket_llm.models import recommend_model, resolve_model


EXYNOS_7884_PROBE = """
__PROPS__
ro.product.manufacturer=samsung
ro.product.model=SM-A205F
ro.build.version.release=11
ro.build.version.sdk=30
ro.product.cpu.abi=arm64-v8a
ro.board.platform=exynos5
ro.soc.model=
ro.hardware=exynos7884B
__MEM__
MemTotal:        2790632 kB
MemFree:          180000 kB
MemAvailable:    1357824 kB
SwapTotal:       1572860 kB
__DF__
/dev/block/mmcblk0p32 24000000 6600000 17400000 28% /data
__CPU__
processor\t: 0
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32
CPU architecture: 8
processor\t: 7
__FREQ__
cpu0=1352000
cpu1=1352000
cpu2=1352000
cpu3=1352000
cpu4=1352000
cpu5=1352000
cpu6=1560000
cpu7=1560000
__IP__
12: wlan0    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0
"""

SNAPDRAGON_8G2_PROBE = """
__PROPS__
ro.product.manufacturer=Xiaomi
ro.product.model=2211133G
ro.build.version.release=14
ro.build.version.sdk=34
ro.product.cpu.abi=arm64-v8a
ro.board.platform=kalama
ro.soc.model=SM8550
ro.hardware=qcom
__MEM__
MemTotal:       11800000 kB
MemAvailable:    7500000 kB
SwapTotal:       4194300 kB
__DF__
/dev/block/sda34 220000000 80000000 140000000 37% /data
__CPU__
processor\t: 0
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 svei8mm i8mm bf16
__FREQ__
cpu0=2016000
cpu1=2016000
cpu2=2016000
cpu3=2803200
cpu4=2803200
cpu5=2803200
cpu6=2803200
cpu7=3187200
__IP__
wlan0: flags=4163<UP> mtu 1500
        inet 10.0.0.42  netmask 255.255.255.0
"""

PIXEL_6GB_PROBE = """
__PROPS__
ro.product.manufacturer=Google
ro.product.model=Pixel 6a
ro.build.version.release=14
ro.build.version.sdk=34
ro.product.cpu.abi=arm64-v8a
ro.soc.model=Tensor
__MEM__
MemTotal:        5734400 kB
MemAvailable:    2900000 kB
__DF__
/dev/block/dm-5 110000000 50000000 60000000 45% /data
__CPU__
processor\t: 0
Features\t: fp asimd aes fphp asimddp
__FREQ__
cpu0=1803000
cpu1=1803000
cpu2=1803000
cpu3=1803000
cpu4=2253000
cpu5=2253000
cpu6=2802000
cpu7=2802000
__IP__
"""


class PocketLlmTests(unittest.TestCase):
    def test_parse_adb_devices(self) -> None:
        sample = (
            "List of devices attached\n"
            "* daemon started successfully\n"
            "SERIAL123456            device usb:1-1 product:a20dd model:SM_A205F device:a20 transport_id:2\n"
            "192.168.1.50:5555      unauthorized\n"
        )
        devs = parse_adb_devices(sample)
        self.assertEqual(len(devs), 2)
        self.assertTrue(devs[0].is_ready)
        self.assertEqual(devs[0].serial, "SERIAL123456")
        self.assertEqual(devs[0].model, "SM_A205F")
        self.assertFalse(devs[1].is_ready)
        self.assertEqual(devs[1].state, "unauthorized")

    def test_exynos_7884_detection_and_model_pick(self) -> None:
        profile = parse_probe_output("SERIAL123456", EXYNOS_7884_PROBE)
        self.assertTrue(profile.is_arm64)
        self.assertFalse(profile.has_dotprod)
        self.assertEqual(profile.oem_key, "samsung")
        self.assertEqual(profile.recommended_cores, "4-7")
        self.assertEqual(profile.recommended_core_mask, "f0")
        self.assertEqual(profile.recommended_threads, 4)
        self.assertEqual(profile.wlan_ip, "192.168.1.50")

        rec = recommend_model(profile)
        self.assertEqual(rec.model.id, "qwen2.5-0.5b")

    def test_snapdragon_flagship_detection_and_model_pick(self) -> None:
        profile = parse_probe_output("XIAOMI123", SNAPDRAGON_8G2_PROBE)
        self.assertTrue(profile.is_arm64)
        self.assertTrue(profile.has_dotprod)
        self.assertTrue(profile.has_i8mm)
        self.assertEqual(profile.oem_key, "xiaomi")
        # 5 performance/prime cores (3..7): picks top 4 -> cores 4-7 (mask 0xf0)
        self.assertEqual(profile.recommended_cores, "4-7")
        self.assertEqual(profile.recommended_core_mask, "f0")
        self.assertEqual(profile.wlan_ip, "10.0.0.42")

        rec = recommend_model(profile)
        self.assertEqual(rec.model.id, "qwen2.5-7b")

    def test_pixel_6gb_detection_and_model_pick(self) -> None:
        profile = parse_probe_output("PIX123", PIXEL_6GB_PROBE)
        self.assertEqual(profile.oem_key, "google")
        self.assertEqual(profile.recommended_cores, "4-7")
        rec = recommend_model(profile)
        self.assertEqual(rec.model.id, "qwen2.5-1.5b")

    def test_storage_constraint_downgrades_model(self) -> None:
        # 12 GB RAM phone, but only ~1.14 GB free storage -> fits 469 MB qwen2.5-0.5b (+450 MB margin), not 1B+
        low_disk_1g = SNAPDRAGON_8G2_PROBE.replace("140000000", "1200000")
        profile_1g = parse_probe_output("LOWDISK1", low_disk_1g)
        rec_1g = recommend_model(profile_1g)
        self.assertEqual(rec_1g.model.id, "qwen2.5-0.5b")

        # 12 GB RAM phone with only ~780 MB free -> downgrades to 258 MB smollm2-360m
        low_disk_780m = SNAPDRAGON_8G2_PROBE.replace("140000000", "800000")
        profile_780m = parse_probe_output("LOWDISK2", low_disk_780m)
        rec_780m = recommend_model(profile_780m)
        self.assertLessEqual(rec_780m.model.size_mb + 450, int(profile_780m.free_storage_gb * 1024))
        self.assertEqual(rec_780m.model.id, "smollm2-360m")

    def test_unreadable_sysfs_fallback(self) -> None:
        cores, mask, threads = select_core_pinning({}, 8)
        self.assertEqual(cores, "")
        self.assertEqual(mask, "")
        self.assertEqual(threads, 4)

    def test_resolve_model(self) -> None:
        profile = parse_probe_output("SERIAL123456", EXYNOS_7884_PROBE)
        self.assertEqual(resolve_model("auto", profile).model.id, "qwen2.5-0.5b")
        self.assertEqual(resolve_model("llama3.2-1b", profile).model.id, "llama3.2-1b")
        with self.assertRaises(ValueError):
            resolve_model("nonexistent-model", profile)

    def test_cli_argument_normalization(self) -> None:
        parser = build_parser()
        args_default = parser.parse_args(_normalize_argv(["--non-interactive", "--model", "qwen2.5-0.5b", "--json"]))
        self.assertEqual(args_default.command, "setup")
        self.assertTrue(args_default.non_interactive)
        self.assertEqual(args_default.model, "qwen2.5-0.5b")
        self.assertTrue(args_default.json)

        args_sub = parser.parse_args(_normalize_argv(["--json", "inspect"]))
        self.assertEqual(args_sub.command, "inspect")
        self.assertTrue(args_sub.json)

    def test_build_slim_runtime_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_tar = tmp_path / "full.tar.gz"
            slim_tar = tmp_path / "slim.tar.gz"
            with tarfile.open(src_tar, "w:gz") as tf:
                for name in (
                    "llama-b1/llama-server",
                    "llama-b1/llama-cli",
                    "llama-b1/llama-bench",
                    "llama-b1/libggml-base.so",
                    "llama-b1/libggml-cpu-android_armv8.0_1.so",
                    "llama-b1/libllama.so",
                    "llama-b1/llama-tts",
                    "llama-b1/llama-llava-cli",
                ):
                    data = b"ELF"
                    ti = tarfile.TarInfo(name=name)
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))

            build_slim_runtime_tarball(src_tar, slim_tar)
            with tarfile.open(slim_tar, "r:gz") as out:
                names = set(out.getnames())
            self.assertIn("llama-server", names)
            self.assertIn("libggml-cpu-android_armv8.0_1.so", names)
            self.assertNotIn("llama-tts", names)
            self.assertNotIn("llama-llava-cli", names)


if __name__ == "__main__":
    unittest.main()
