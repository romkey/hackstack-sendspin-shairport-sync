"""Diagnostics parse cgroup and procfs files that are awkward to fake live."""

from __future__ import annotations

import time

from nowplaying.diagnostics import Diagnostics

MEMINFO = "MemTotal:        8019876 kB\nMemFree:  566908 kB\nMemAvailable:    4009938 kB\n"


def _cgroup(tmp_path, usage_usec=0, current=None, maximum="max"):
    (tmp_path / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 1\nsystem_usec 1\n")
    if current is not None:
        (tmp_path / "memory.current").write_text(str(current))
    (tmp_path / "memory.max").write_text(maximum)
    return tmp_path


def test_cpu_needs_two_samples_then_reports_a_percentage(tmp_path):
    cgroup = _cgroup(tmp_path, usage_usec=0)
    diagnostics = Diagnostics(cgroup=cgroup, thermal=tmp_path / "nothermal")

    assert diagnostics.sample().cpu_percent is None  # nothing to compare against

    time.sleep(0.05)
    (cgroup / "cpu.stat").write_text("usage_usec 1000000\n")
    percent = diagnostics.sample().cpu_percent
    assert percent is not None
    assert 0.0 <= percent <= 100.0


def test_memory_uses_the_cgroup_limit_when_one_is_set(tmp_path):
    cgroup = _cgroup(tmp_path, current=256 * 1024 * 1024, maximum=str(512 * 1024 * 1024))
    sample = Diagnostics(cgroup=cgroup, thermal=tmp_path / "nothermal").sample()

    assert sample.memory_used_mb == 256.0
    assert sample.memory_total_mb == 512.0
    assert sample.memory_percent == 50.0


def test_memory_falls_back_to_the_host_when_unlimited(tmp_path):
    # "max" means no container limit, so the container's own usage is not the
    # interesting number and the host's totals are reported instead.
    cgroup = _cgroup(tmp_path, current=1024, maximum="max")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(MEMINFO)

    sample = Diagnostics(cgroup=cgroup, thermal=tmp_path / "nothermal", meminfo=meminfo).sample()

    # 8019876 kB total with 4009938 kB available -> almost exactly half used.
    assert sample.memory_total_mb == 7831.9
    assert sample.memory_percent == 50.0


def test_temperature_is_read_from_the_first_thermal_zone(tmp_path):
    thermal = tmp_path / "thermal"
    (thermal / "thermal_zone0").mkdir(parents=True)
    (thermal / "thermal_zone0" / "temp").write_text("47300\n")

    sample = Diagnostics(cgroup=_cgroup(tmp_path), thermal=thermal).sample()
    assert sample.cpu_temperature_c == 47.3


def test_missing_files_degrade_to_none_rather_than_raising(tmp_path):
    sample = Diagnostics(
        cgroup=tmp_path / "absent",
        thermal=tmp_path / "absent",
        meminfo=tmp_path / "absent",
        loadavg=tmp_path / "absent",
    ).sample()

    assert sample.cpu_percent is None
    assert sample.cpu_temperature_c is None
    assert sample.uptime_seconds is not None  # this one never depends on the filesystem
