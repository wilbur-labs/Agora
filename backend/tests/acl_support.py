"""Build a directory the current account may not traverse, and undo it.

Used by the tests that pin *unreadable is not absent* (``test_fsprobe``,
``test_progress_snapshot``, ``test_project_provisioning``): those care that the OS
itself refuses the stat, not only that a patched ``lstat`` raised.

**Windows is deliberately not attempted.** A deny ACE does produce the case — measured:
``icacls <dir> /deny <user>:(OI)(CI)(RX)`` makes ``os.lstat`` of a child raise
``PermissionError``, and non-inheriting variants do not, because
``SeChangeNotifyPrivilege`` ("bypass traverse checking") is held by default and skips
traverse checks on intermediate directories. The problem is undoing it: ``(RX)`` denies
``READ_CONTROL``, so ``icacls /remove:d``, ``icacls /reset``, ``Get-Acl`` and
``Directory.SetAccessControl`` all fail with access denied afterwards for a
non-elevated owner, and the directory can then be neither read nor deleted. A test that
can create state it cannot remove has no business in an automated suite, so
:func:`deny_self_traverse` returns False on Windows and the caller skips with a reason.
The distinction itself is pinned there by tests that raise the ``OSError`` directly.

POSIX has no such problem: a mode of 0 is enforced for the owner and restored by one
``chmod``. Root ignores it, so that case reports False as well.

Always restore in a ``finally``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def deny_self_traverse(directory: Path) -> bool:
    """Deny this account traversal of ``directory``. False when it cannot be arranged.

    False means "this platform or token cannot build the case", never "it was built".
    """
    if sys.platform == "win32":
        # Creatable, but not reversibly — see the module docstring.
        return False
    if os.geteuid() == 0:
        return False
    os.chmod(directory, 0o000)
    return True


def restore_traverse(directory: Path) -> None:
    """Undo :func:`deny_self_traverse` so the temporary tree can be removed."""
    if sys.platform == "win32":
        return
    os.chmod(directory, 0o700)
