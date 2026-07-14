import json
import subprocess
import sys

from gpu import get_gpu_stats


def test_linux_gpu_stats_via_rocm_smi(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    rocm_json = json.dumps({
        "card0": {"GPU use (%)": "42%", "GPU Memory Allocated (VRAM%)": "68%"}
    })

    class MockProc:
        returncode = 0
        stdout = rocm_json

    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: MockProc())

    monkeypatch.setattr("gpu.subprocess", MockSP())
    stats = get_gpu_stats()
    assert stats["gpu_percent"] == 42
    assert stats["vram_percent"] == 68
    assert stats["available"] is True


def test_linux_gpu_stats_rocm_smi_absent(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError("rocm-smi not found")))

    monkeypatch.setattr("gpu.subprocess", MockSP())
    stats = get_gpu_stats()
    assert stats["gpu_percent"] == 0
    assert stats["vram_percent"] == 0
    assert stats["available"] is False


def test_darwin_gpu_stats_via_system_profiler(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    sp_output = """Graphics/Displays:

    Apple M2 Pro:

      Chipset Model: Apple M2 Pro
      Type: GPU
      Bus: Built-In
      Total Number of Cores: 19
      VRAM (Dynamic, Max): 16 GB
      Displays:
        Color LCD:
          Display Type: Built-In Retina LCD
          Resolution: 3456x2234
"""

    class MockProc:
        returncode = 0
        stdout = sp_output

    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: MockProc())

    monkeypatch.setattr("gpu.subprocess", MockSP())
    stats = get_gpu_stats()
    assert stats["available"] is True
    assert stats["gpu_model"] == "Apple M2 Pro"
    assert stats["vram_total_mb"] == 16384
    assert stats["gpu_percent"] == 0
    assert stats["vram_percent"] == 0


def test_darwin_gpu_stats_system_profiler_absent(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError("no system_profiler")))

    monkeypatch.setattr("gpu.subprocess", MockSP())
    stats = get_gpu_stats()
    assert stats["available"] is False
    assert stats["gpu_percent"] == 0
    assert stats["vram_percent"] == 0


def test_darwin_no_gpu_found(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    sp_output = "Graphics/Displays:\n\n    No displays found.\n"

    class MockProc:
        returncode = 0
        stdout = sp_output

    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: MockProc())

    monkeypatch.setattr("gpu.subprocess", MockSP())
    stats = get_gpu_stats()
    assert stats["available"] is False
    assert stats["gpu_percent"] == 0
    assert stats["vram_percent"] == 0
