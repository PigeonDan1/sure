#!/usr/bin/env python3
"""Host environment variables a sanitized child interpreter may keep.

Used by the local Python launcher and the model-runtime import probe.
The POSIX names preserve the historical launch allowlist. On Windows,
also retain host identity and directory variables used by Winsock,
getpass, tempfile and user-directory discovery.
"""

from __future__ import annotations

import sys

POSIX_HOST_ENV = frozenset(
    {
        "CUDA_VISIBLE_DEVICES",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "NVIDIA_DRIVER_CAPABILITIES",
        "NVIDIA_VISIBLE_DEVICES",
        "PATH",
        "TERM",
        "TZ",
    }
)

WINDOWS_HOST_ENV = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMPUTERNAME",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        # Git Bash parents can export HOME.
        "HOME",
        "LOCALAPPDATA",
        "OS",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)

HOST_ENV_ALLOW = POSIX_HOST_ENV | WINDOWS_HOST_ENV if sys.platform == "win32" else POSIX_HOST_ENV
