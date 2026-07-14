"""
cAIc - GPU stats: rocm-smi (AMD/Linux), system_profiler (Apple Silicon/macOS).
"""
import json
import logging
import platform
import re
import subprocess
import sys

log = logging.getLogger("caic")


def _parse_darwin_gpu_stats() -> dict:
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
        text = result.stdout
        gpu_model = ""
        vram_mb = 0
        for line in text.splitlines():
            m = re.match(r"\s+Chipset Model:\s+(.+)", line)
            if m:
                gpu_model = m.group(1).strip()
            m = re.match(r"\s+VRAM \(Dynamic, Max\):\s+(\d+)\s+GB", line)
            if m:
                vram_mb = int(m.group(1)) * 1024
        if gpu_model:
            return {
                "gpu_percent": 0,
                "vram_percent": 0,
                "available": True,
                "gpu_model": gpu_model,
                "vram_total_mb": vram_mb,
            }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    except Exception as e:
        log.warning("Darwin GPU stats error: %s", e)
    return {}


def _parse_linux_gpu_stats() -> dict:
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse", "--showmemuse", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            gpu_info = data.get("card0", {})
            gpu_use = gpu_info.get("GPU use (%)", 0)
            vram_use = gpu_info.get("GPU Memory Allocated (VRAM%)", 0)
            if isinstance(gpu_use, str):
                gpu_use = int(gpu_use.replace("%", "").strip() or 0)
            if isinstance(vram_use, str):
                vram_use = int(vram_use.replace("%", "").strip() or 0)
            return {"gpu_percent": gpu_use, "vram_percent": vram_use, "available": True}
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        log.warning("Linux GPU stats error: %s", e)
    return {}


def get_gpu_stats() -> dict:
    if sys.platform == "darwin":
        stats = _parse_darwin_gpu_stats()
        if stats:
            return stats
    stats = _parse_linux_gpu_stats()
    if stats:
        return stats
    return {"gpu_percent": 0, "vram_percent": 0, "available": False}
