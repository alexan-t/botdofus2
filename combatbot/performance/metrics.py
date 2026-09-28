"""Mesures de ressources du processus courant, sans dépendance supplémentaire.

Windows : API natives via ctypes (Working Set, Private Bytes, temps CPU, handles, objets GDI/USER,
threads via Toolhelp). Linux : ``/proc/self``. Toute valeur non disponible vaut ``None`` : elle n'est
jamais estimée. Le tas Python (tracemalloc) n'est mesuré que si le traçage a été démarré explicitement
(mode diagnostic : il ralentit les allocations).
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass

MB = 1024 * 1024


@dataclass(frozen=True)
class ProcessSnapshot:
    wall: float
    cpu_seconds: float | None
    rss_mb: float | None
    private_mb: float | None
    threads: int | None
    python_threads: int
    handles: int | None
    gdi_objects: int | None
    user_objects: int | None
    python_heap_mb: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _python_heap_mb() -> float | None:
    if not tracemalloc.is_tracing():
        return None
    current, _peak = tracemalloc.get_traced_memory()
    return round(current / MB, 2)


def _windows_snapshot() -> dict[str, object]:
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    class ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                    ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # ctypes suppose des arguments ``int`` tant que les signatures ne sont pas
    # déclarées. Le pseudo-handle 64 bits renvoyé par GetCurrentProcess vaut
    # 0xffffffffffffffff et déborde alors avant même l'appel Win32.
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user32.GetGuiResources.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    result: dict[str, object] = {}
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        result["rss_mb"] = round(counters.WorkingSetSize / MB, 2)
        result["private_mb"] = round(counters.PrivateUsage / MB, 2)
    times = [wintypes.FILETIME() for _ in range(4)]
    if kernel32.GetProcessTimes(process, *(ctypes.byref(item) for item in times)):
        kernel, user = times[2], times[3]
        result["cpu_seconds"] = ((kernel.dwHighDateTime << 32 | kernel.dwLowDateTime)
                                 + (user.dwHighDateTime << 32 | user.dwLowDateTime)) / 1e7
    count = wintypes.DWORD()
    if kernel32.GetProcessHandleCount(process, ctypes.byref(count)):
        result["handles"] = int(count.value)
    result["gdi_objects"] = int(user32.GetGuiResources(process, 0))
    result["user_objects"] = int(user32.GetGuiResources(process, 1))
    snapshot = kernel32.CreateToolhelp32Snapshot(0x4, 0)            # TH32CS_SNAPTHREAD
    if snapshot and snapshot != wintypes.HANDLE(-1).value:
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(ThreadEntry)
            pid, threads = os.getpid(), 0
            ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while ok:
                threads += entry.th32OwnerProcessID == pid
                ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            result["threads"] = threads
        finally:
            kernel32.CloseHandle(snapshot)
    return result


def _linux_snapshot() -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        status = open("/proc/self/status", encoding="utf-8").read().splitlines()
    except OSError:
        return result
    fields = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in status if ":" in line}
    if "VmRSS" in fields:
        result["rss_mb"] = round(int(fields["VmRSS"].split()[0]) / 1024, 2)
    if "RssAnon" in fields:                     # mémoire anonyme privée : équivalent le plus proche
        result["private_mb"] = round(int(fields["RssAnon"].split()[0]) / 1024, 2)
    if "Threads" in fields:
        result["threads"] = int(fields["Threads"])
    try:
        result["handles"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    times = os.times()
    result["cpu_seconds"] = times.user + times.system
    return result


def process_snapshot() -> ProcessSnapshot:
    values: dict[str, object] = {}
    try:
        values = _windows_snapshot() if sys.platform == "win32" else _linux_snapshot()
    except (OSError, AttributeError, ValueError):
        values = {}
    if "cpu_seconds" not in values:
        times = os.times()
        values["cpu_seconds"] = times.user + times.system
    return ProcessSnapshot(
        wall=time.monotonic(), cpu_seconds=values.get("cpu_seconds"), rss_mb=values.get("rss_mb"),
        private_mb=values.get("private_mb"), threads=values.get("threads"),
        python_threads=threading.active_count(), handles=values.get("handles"),
        gdi_objects=values.get("gdi_objects"), user_objects=values.get("user_objects"),
        python_heap_mb=_python_heap_mb())


def cpu_percent(previous: ProcessSnapshot, current: ProcessSnapshot) -> float | None:
    """% d'UN cœur (100 = un cœur plein, comme « ps ») ; None si non mesurable."""
    if previous.cpu_seconds is None or current.cpu_seconds is None or current.wall <= previous.wall:
        return None
    return round(100 * (current.cpu_seconds - previous.cpu_seconds) / (current.wall - previous.wall), 1)
