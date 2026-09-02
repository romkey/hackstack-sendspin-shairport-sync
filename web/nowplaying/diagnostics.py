"""System metrics readable from inside the container.

cgroup v2 exposes the container's own CPU and memory accounting, and on a
Raspberry Pi the SoC thermal zone is readable too. When the container has no
memory limit, ``memory.max`` reads ``max`` and the host's totals from
``/proc/meminfo`` are the meaningful figure instead.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

CGROUP = Path("/sys/fs/cgroup")
THERMAL = Path("/sys/class/thermal")
MEMINFO = Path("/proc/meminfo")
LOADAVG = Path("/proc/loadavg")


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


@dataclass
class Sample:
    """One reading of the container's resource use."""

    cpu_percent: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    memory_percent: float | None = None
    disk_used_percent: float | None = None
    cpu_temperature_c: float | None = None
    load_1m: float | None = None
    uptime_seconds: int | None = None

    def to_dict(self) -> dict[str, float | int | None]:
        """Render as JSON-serialisable data."""
        return asdict(self)


class Diagnostics:
    """Samples container CPU, memory, disk and temperature."""

    def __init__(
        self,
        cgroup: Path = CGROUP,
        thermal: Path = THERMAL,
        meminfo: Path = MEMINFO,
        loadavg: Path = LOADAVG,
        disk_path: str = "/config",
    ):
        """Paths are injectable so the parsing can be tested without cgroups."""
        self._cgroup = cgroup
        self._thermal = thermal
        self._meminfo = meminfo
        self._loadavg = loadavg
        self._disk_path = disk_path
        self._started = time.monotonic()
        self._last_cpu: tuple[int, float] | None = None
        self._cpu_count = os.cpu_count() or 1

    def sample(self) -> Sample:
        """Take a reading. CPU needs two calls to produce a figure."""
        return Sample(
            cpu_percent=self._cpu_percent(),
            **self._memory(),
            disk_used_percent=self._disk_percent(),
            cpu_temperature_c=self._temperature(),
            load_1m=self._load(),
            uptime_seconds=int(time.monotonic() - self._started),
        )

    # -- individual metrics --------------------------------------------------

    def _cpu_percent(self) -> float | None:
        """Percentage of the machine's total CPU capacity used since last call."""
        stat = _read(self._cgroup / "cpu.stat")
        if stat is None:
            return None
        usage: int | None = None
        for line in stat.splitlines():
            if line.startswith("usage_usec"):
                usage = int(line.split()[1])
                break
        if usage is None:
            return None

        now = time.monotonic()
        previous = self._last_cpu
        self._last_cpu = (usage, now)
        if previous is None:
            return None  # nothing to compare against yet

        elapsed = now - previous[1]
        if elapsed <= 0:
            return None
        busy_usec = usage - previous[0]
        percent = (busy_usec / (elapsed * 1_000_000) / self._cpu_count) * 100
        return round(max(0.0, min(100.0, percent)), 1)

    def _memory(self) -> dict[str, float | None]:
        used = _read_int(self._cgroup / "memory.current")
        limit_text = _read(self._cgroup / "memory.max")

        if used is not None and limit_text not in (None, "max"):
            total = int(limit_text)  # type: ignore[arg-type]
        else:
            # No limit set, so the container's own usage is not the useful
            # number -- report the machine's memory instead.
            used, total = self._host_memory(self._meminfo)

        if used is None or not total:
            return {"memory_used_mb": None, "memory_total_mb": None, "memory_percent": None}
        return {
            "memory_used_mb": round(used / 1024 / 1024, 1),
            "memory_total_mb": round(total / 1024 / 1024, 1),
            "memory_percent": round(used / total * 100, 1),
        }

    @staticmethod
    def _host_memory(meminfo: Path) -> tuple[int | None, int | None]:
        text = _read(meminfo)
        if text is None:
            return None, None
        fields: dict[str, int] = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].endswith(":"):
                try:
                    fields[parts[0][:-1]] = int(parts[1]) * 1024
                except ValueError:
                    continue
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if total is None or available is None:
            return None, total
        return total - available, total

    def _disk_percent(self) -> float | None:
        try:
            usage = shutil.disk_usage(self._disk_path)
        except OSError:
            return None
        if not usage.total:
            return None
        return round(usage.used / usage.total * 100, 1)

    def _temperature(self) -> float | None:
        """SoC temperature, which is what matters on a Pi in a warm cupboard."""
        try:
            zones = sorted(self._thermal.glob("thermal_zone*"))
        except OSError:
            return None
        for zone in zones:
            milli = _read_int(zone / "temp")
            if milli is not None and milli > 0:
                return round(milli / 1000, 1)
        return None

    def _load(self) -> float | None:
        text = _read(self._loadavg)
        if text is None:
            return None
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return None
