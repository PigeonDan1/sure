#!/usr/bin/env python3
"""Host environment variables a sanitized child interpreter may keep.

Single source of truth for the two consumers that must never drift apart:
`python_execution._safe_environment` (the local_python launch env for
infer_entrypoint.py and, by inheritance through model_child_env, the Model
Python) and the model-runtime import probe in
check_execution_surface_compliance.

The POSIX names are the historical launch allowlist. The Windows names are
system identity values, never credentials: Windows Python needs them during
import, and stripping them fails the child before any model code runs
(observed on Win11 26200 with the harness venv: without SystemRoot,
`import asyncio` dies with WinError 10106 because WSAStartup cannot resolve
the Winsock service providers; without USERNAME, `getpass.getuser()` falls
back to the POSIX `pwd` module and raises ModuleNotFoundError; TEMP/USERPROFILE
back tempfile and the cache homes that HF/torch resolve during import).
"""

from __future__ import annotations

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
        # Not Windows-native, but Git-Bash parents carry it and path.home()
        # prefers it over USERPROFILE.
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

HOST_ENV_ALLOW = POSIX_HOST_ENV | WINDOWS_HOST_ENV
