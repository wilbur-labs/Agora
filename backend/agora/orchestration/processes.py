"""Non-destructive cross-platform process liveness inspection."""
from __future__ import annotations

import ctypes
import errno
import os
from enum import Enum


class ProcessState(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


def inspect_process(pid: int) -> ProcessState:
    """Inspect a PID without sending a terminating signal.

    Unknown results deliberately remain distinct from dead processes so recovery
    can fail closed instead of dispatching a duplicate runtime.
    """
    if pid <= 0:
        return ProcessState.DEAD
    if os.name == "nt":
        return _inspect_windows_process(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ProcessState.DEAD
    except PermissionError:
        return ProcessState.UNKNOWN
    except OSError as exc:
        return ProcessState.DEAD if exc.errno == errno.ESRCH else ProcessState.UNKNOWN
    return ProcessState.ALIVE


def _inspect_windows_process(pid: int) -> ProcessState:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return (
            ProcessState.DEAD
            if ctypes.get_last_error() == error_invalid_parameter
            else ProcessState.UNKNOWN
        )
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return ProcessState.UNKNOWN
        return ProcessState.ALIVE if exit_code.value == still_active else ProcessState.DEAD
    finally:
        close_handle(handle)
