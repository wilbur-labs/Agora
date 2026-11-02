"""Is the shell a previous launch started still running? (CR-75 §2, CR-76.)

A process id alone is not an identity — after a reboot the number is soon reused — so
the creation time recorded at launch must match too. No new dependency: Win32 through
ctypes, ``/proc`` on Linux. The answer is ``alive``, ``gone`` or ``unknown``, and the
caller treats ``unknown`` like ``alive``: it would rather not start a second agent.
"""
from __future__ import annotations

import os
import sys

#: Two readings of one process's creation time agree to within this many seconds.
TOLERANCE = 2.0


def creation_time(pid: int) -> float | None:
    """The process's creation time (seconds since the epoch), or None if it cannot be read."""
    try:
        if os.name == "nt":
            return _windows_creation(pid)
        if sys.platform.startswith("linux"):
            return _linux_creation(pid)
    except Exception:  # noqa: BLE001 - any failure to read is "could not read"
        return None
    return None


def liveness(pid: int | None, created: float | None) -> str:
    if not pid or pid <= 0:
        return "gone"
    if os.name == "nt":
        state = _windows_state(pid)
    elif sys.platform.startswith("linux"):
        try:
            os.stat(f"/proc/{pid}")
            state = "alive"
        except FileNotFoundError:
            state = "gone"
        except OSError:  # denied, or a passing I/O error: not evidence that it ended
            state = "unknown"
    else:
        return "unknown"
    if state != "alive":
        return state
    if created is None:
        return "unknown"
    now = creation_time(pid)
    if now is None:
        return "unknown"
    return "alive" if abs(now - created) <= TOLERANCE else "gone"


# --- Windows -----------------------------------------------------------------------------
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
#: 100 ns intervals between 1601-01-01 and 1970-01-01.
_EPOCH_DELTA = 116444736000000000


def _open(pid: int):
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return kernel32, handle, ctypes.get_last_error()


def _windows_state(pid: int) -> str:
    import ctypes

    try:
        kernel32, handle, error = _open(pid)
    except Exception:  # noqa: BLE001
        return "unknown"
    if not handle:
        return "gone" if error == _ERROR_INVALID_PARAMETER else "unknown"
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return "unknown"
        return "alive" if code.value == _STILL_ACTIVE else "gone"
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _windows_creation(pid: int) -> float | None:
    import ctypes
    from ctypes import wintypes

    kernel32, handle, _ = _open(pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return (ticks - _EPOCH_DELTA) / 10_000_000
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


# --- Linux -------------------------------------------------------------------------------
def _linux_creation(pid: int) -> float | None:
    with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
        stat = handle.read()
    # The command name is in parentheses and may contain spaces: split after the last ')'.
    fields = stat[stat.rindex(")") + 2:].split()
    start_ticks = int(fields[19])
    with open("/proc/stat", encoding="utf-8") as handle:
        boot = next(int(line.split()[1]) for line in handle if line.startswith("btime "))
    return boot + start_ticks / os.sysconf("SC_CLK_TCK")
