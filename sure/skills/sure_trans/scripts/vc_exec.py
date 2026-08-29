#!/usr/bin/env python3
"""Shared Volcano (vc) execution helpers for SURE-TRANS GPU validation.

The GPU-touching trans gates (run_execution_compat.py, run_trans_validate.py)
route container work through ``vc submit`` on the site's dedicated GPU
partition instead of ``docker run --gpus all`` on the login node.

Mechanics:

- The submit host writes an inner bash script under a work dir on shared
  storage, identity-mounts that dir into the job, and passes
  ``bash <container path>`` as ``--cmd``.
- The inner script runs the requested command and writes stdout, stderr, and
  the exit code into the mounted dir. The submit host polls those files rather
  than parsing volatile vc status strings; ``vc info --job`` and ``vc logs``
  are diagnostic evidence only.
- A standalone CLI exposes the same submit+wait for agent-driven steps such as
  the unit-17 post-pull MCP smoke.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

DEFAULT_PROJECT = "hpc"
DEFAULT_GPUS = 1
DEFAULT_MEMORY_GB = 32
DEFAULT_CPUS = 8
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 1200.0
DEFAULT_POLL_INTERVAL_SECONDS = 15.0
DONE_MARKER = "SURE_TRANS_JOB_DONE"

REGISTRY_PROJECT = "hpc"
IMAGE_PREFIX = "ai_asr-"
SEMVER_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
REGISTRY_TAG_LIST_LIMIT = 1000
REGISTRY_TAG_TIMEOUT_SECONDS = 30


def default_partition() -> str:
    """Return the site's default VC partition for trans GPU validation."""
    resolved = load_site_policy(required=True) or {}
    value = resolved.get("policy", {}).get("execution", {}).get("vc_default_partition")
    if not value:
        raise ValueError(
            "site policy is missing execution.vc_default_partition; set it in "
            "config/site.bundled.yaml or config/site.local.yaml"
        )
    return str(value)


def registry_host() -> str:
    """Return the site's container registry host for trans image delivery."""
    resolved = load_site_policy(required=True) or {}
    value = resolved.get("policy", {}).get("network", {}).get("container_registry")
    if not value:
        raise ValueError(
            "site policy is missing network.container_registry; set it in "
            "config/site.bundled.yaml or config/site.local.yaml"
        )
    return str(value)

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}")
# push reports "digest: sha256:..." and pull reports "Digest: sha256:...".
_DIGEST_RE = re.compile(r"digest:\s+(sha256:[0-9a-f]{64})", re.IGNORECASE)

RAM_OOM_MARKERS = ("oomkilled", "std::bad_alloc", "cannot allocate memory", "out of memory: killed process")
GPU_OOM_MARKER = "cuda out of memory"


def run_command(
    args: list[str],
    *,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Thin subprocess wrapper; kept separate so tests can monkeypatch it."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def agent_bin_dir() -> Path:
    """Directory the coding agent puts first on PATH for its bundled fd and rg."""
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"
    return base / "bin"


def agent_bin_cleared_env() -> dict[str, str]:
    """The environment with the agent's own bin dir taken off PATH.

    That leading bin dir shadows any system binary of the same name. A docker
    left there answered every push with "denied" while the system one pushed
    fine, so drop the directory rather than trust whatever now sits in it.
    """
    env = os.environ.copy()
    path = env.get("PATH")
    if path:
        shadowed = agent_bin_dir()
        env["PATH"] = os.pathsep.join(
            entry for entry in path.split(os.pathsep) if entry and Path(entry) != shadowed
        )
    return env


def proxy_cleared_env() -> dict[str, str]:
    env = agent_bin_cleared_env()
    for key in _PROXY_KEYS:
        env.pop(key, None)
    return env


def vc_available() -> bool:
    if not shutil.which("vc"):
        return False
    try:
        result = run_command(["vc", "info"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def user_partitions() -> set[str]:
    if not shutil.which("vc"):
        return set()
    try:
        result = run_command(["vc", "info", "-u"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    # Only the [Partition] block lists partitions. Scanning the whole output
    # collected the separator rule and answers from other blocks ("NO" from
    # the overcommit block), which made the caller's precheck accept them.
    partitions: set[str] = set()
    in_block = False
    for line in result.stdout.splitlines():
        name = line.strip()
        if name == "[Partition]":
            in_block = True
            continue
        if not in_block:
            continue
        if name.startswith("["):
            break
        if name.strip("-") and re.fullmatch(r"[A-Za-z0-9._-]+", name):
            partitions.add(name)
    return partitions


def partition_allowed(partition: str) -> bool:
    return partition in user_partitions()


def safe_image_component(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("._-")
    if not name:
        raise ValueError(f"invalid image name component: {value!r}")
    return name.lower()


def registry_image(model_name: str, version: str, stage: str | None = None) -> str:
    """Return the registry-enforced delivery name for a trans image.

    Naming spec (server-enforced; other names are rejected with exit 4)::

        <network.container_registry>/hpc/ai_asr-<name>:<version>
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", version or ""):
        raise ValueError(f"invalid image version: {version!r}")
    safe = safe_image_component(model_name)
    suffix = f"-{stage}" if stage else ""
    return f"{registry_host()}/{REGISTRY_PROJECT}/{IMAGE_PREFIX}{safe}{suffix}:{version}"


def registry_repository(model_name: str, stage: str | None = None) -> str:
    """Return the repository portion of a registry-backed trans image."""
    return registry_image(model_name, "0.0.0", stage).rsplit(":", 1)[0]


def _docker_registry_credentials(registry: str) -> tuple[str, str] | None:
    """Read Basic credentials from Docker's config without exposing them."""
    configured = os.environ.get("DOCKER_CONFIG", "").strip()
    config_path = Path(configured).expanduser() / "config.json" if configured else Path.home() / ".docker" / "config.json"
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    auths = document.get("auths")
    if not isinstance(auths, dict):
        return None
    normalized = registry.removeprefix("http://").removeprefix("https://").rstrip("/")
    candidates = (registry, normalized, f"http://{normalized}", f"https://{normalized}")
    for key in candidates:
        entry = auths.get(key)
        if not isinstance(entry, dict):
            continue
        encoded = entry.get("auth")
        if isinstance(encoded, str) and encoded:
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except (ValueError, UnicodeError):
                continue
            username, separator, password = decoded.partition(":")
            if separator:
                return username, password
        identity_token = entry.get("identitytoken")
        if isinstance(identity_token, str) and identity_token:
            return "", identity_token
    return None


def _registry_base_url(repository: str) -> tuple[str, str, str]:
    """Split a repository ref into API URL, registry host, and repo path."""
    raw = repository
    if "://" not in raw:
        raw = f"{os.environ.get('SURE_REGISTRY_SCHEME', 'http')}://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"invalid registry repository: {repository!r}")
    repo_path = parsed.path.strip("/")
    endpoint = urlunparse((parsed.scheme, parsed.netloc, f"/v2/{repo_path}/tags/list", "", urlencode({"n": REGISTRY_TAG_LIST_LIMIT}), ""))
    return endpoint, parsed.netloc, repo_path


def _basic_authorization(credentials: tuple[str, str] | None) -> str | None:
    if credentials is None:
        return None
    username, password = credentials
    if username == "" and password:
        return f"Bearer {password}"
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _bearer_challenge(header: str) -> dict[str, str]:
    scheme, _, parameters = header.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    return {key: value for key, value in re.findall(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"', parameters)}


def _registry_json_request(url: str, headers: dict[str, str]) -> tuple[dict, dict[str, str]]:
    request = Request(url, headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=REGISTRY_TAG_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("registry response is not a JSON object")
            return payload, {str(key): str(value) for key, value in response.headers.items()}
    except HTTPError:
        raise
    except (OSError, URLError, ValueError, UnicodeError) as error:
        raise ValueError(f"registry tag query failed: {error}") from error


def _next_registry_page(headers: dict[str, str], current_url: str) -> str | None:
    link = next((value for key, value in headers.items() if key.lower() == "link"), "")
    for entry in link.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', entry)
        if match:
            return urljoin(current_url, match.group(1))
    return None


def _collect_registry_tags(
    endpoint: str,
    headers: dict[str, str],
    first_page: tuple[dict, dict[str, str]] | None = None,
) -> list[str]:
    tags: set[str] = set()
    page_url = endpoint
    page = first_page
    while page_url:
        payload, response_headers = page if page is not None else _registry_json_request(page_url, headers)
        page = None
        page_tags = payload.get("tags")
        if page_tags is not None:
            if not isinstance(page_tags, list) or not all(isinstance(tag, str) for tag in page_tags):
                raise ValueError("registry tag query returned an invalid tags list")
            tags.update(page_tags)
        page_url = _next_registry_page(response_headers, page_url)
    return sorted(tags)


def registry_tags(repository: str) -> list[str]:
    """List tags for a repository through the Docker Registry V2 API."""
    endpoint, registry, _ = _registry_base_url(repository)
    credentials = _docker_registry_credentials(registry)
    authorization = _basic_authorization(credentials)
    headers = {"Accept": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    try:
        first_page = _registry_json_request(endpoint, headers)
    except HTTPError as error:
        if error.code == 404:
            return []
        if error.code != 401:
            raise ValueError(f"registry tag query returned HTTP {error.code} for {repository}") from error
        challenge = _bearer_challenge(str(error.headers.get("WWW-Authenticate") or ""))
        realm = challenge.get("realm")
        if not realm:
            raise ValueError(f"registry tag query requires authentication for {repository}") from error
        query = dict(parse_qsl(urlparse(realm).query, keep_blank_values=True))
        for key in ("service", "scope"):
            if challenge.get(key):
                query[key] = challenge[key]
        token_url = urlunparse(urlparse(realm)._replace(query=urlencode(query)))
        token_headers = {"Accept": "application/json"}
        if authorization and authorization.startswith("Basic "):
            token_headers["Authorization"] = authorization
        try:
            token_payload, _ = _registry_json_request(token_url, token_headers)
        except HTTPError as token_error:
            raise ValueError(f"registry authentication failed for {repository} (HTTP {token_error.code})") from token_error
        token = token_payload.get("token") or token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError(f"registry authentication returned no bearer token for {repository}")
        authenticated_headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        try:
            return _collect_registry_tags(endpoint, authenticated_headers)
        except HTTPError as authenticated_error:
            raise ValueError(
                f"registry tag query remained unauthorized for {repository} (HTTP {authenticated_error.code})"
            ) from authenticated_error
    return _collect_registry_tags(endpoint, headers, first_page)


def next_image_version(tags: list[str] | tuple[str, ...] | set[str]) -> str:
    """Choose the next unused patch version after the highest SemVer tag."""
    tag_set = {str(tag) for tag in tags}
    parsed = []
    for tag in tag_set:
        match = SEMVER_TAG_RE.fullmatch(tag)
        if match:
            parsed.append(tuple(int(part) for part in match.groups()))
    major, minor, patch = max(parsed, default=(0, 1, -1))
    candidate = (major, minor, patch + 1)
    while ".".join(str(part) for part in candidate) in tag_set:
        candidate = (candidate[0], candidate[1], candidate[2] + 1)
    return ".".join(str(part) for part in candidate)


def resolve_image_version(model_name: str, requested: str | None = None) -> tuple[str, dict[str, object]]:
    """Resolve an explicit version or the next free version across both repos."""
    if requested is not None:
        return requested, {"mode": "explicit", "repositories": [], "existing_tags": []}
    repositories = [registry_repository(model_name, "source"), registry_repository(model_name)]
    tags_by_repository = {repository: registry_tags(repository) for repository in repositories}
    all_tags = sorted({tag for tags in tags_by_repository.values() for tag in tags})
    version = next_image_version(all_tags)
    return version, {
        "mode": "registry_auto",
        "repositories": repositories,
        "existing_tags": all_tags,
        "tags_by_repository": tags_by_repository,
    }


def inner_script_command(log_dir: Path) -> str:
    """The --cmd string vc hands to a remote shell.

    log_dir comes from --log-dir/--run-dir and is only resolved, so a path with
    a space split into two remote arguments and a path with a semicolon started
    a second remote command there. Quote it for that shell.
    """
    return "bash " + shlex.quote(str(log_dir / "inner.sh"))


def bounded_seconds(value: str) -> float:
    """A wait budget must be a finite positive number of seconds.

    argparse's plain float type accepts inf and nan. With inf the poll loop's
    deadline never passes, so a job that never writes its exit code left the
    caller waiting forever.
    """
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of seconds") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be a finite positive number of seconds")
    return seconds


def cancel_vc_job(job_id: str) -> str:
    """Best-effort cancel of a job we have stopped waiting for.

    A local timeout only ends the poll loop. Without this the job keeps its
    place in the queue and its GPUs long after the run has been reported, and
    writes its output into a log directory the next attempt is already using.
    """
    try:
        result = run_command(["vc", "delete", "--job", job_id], timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return f"job {job_id} could not be cancelled: {error!r}"
    body = f"{result.stdout}{result.stderr}".strip()
    rendered = f"$ vc delete --job {job_id}\nexit_code={result.returncode}\n{body}".rstrip()
    if result.returncode != 0:
        return f"{rendered}\njob {job_id} could not be cancelled"
    return rendered


def clear_previous_result(log_dir: Path) -> None:
    """Drop the completion marker an earlier job left in this directory.

    Log directories are deterministic per run and per gate, so rerunning a
    gate reuses one. The poll loop finishes as soon as exit_code holds a
    value, so a leftover marker ended the wait immediately and reported the
    previous attempt's result while the new job was still queued.

    stdout.log and stderr.log are left alone: they are audit evidence, and
    the inner script truncates them when the job starts.
    """
    (log_dir / "exit_code").unlink(missing_ok=True)


def normalize_job_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return (name or "sure-trans-job")[:60].rstrip("-")


def recorded_push_digest(artifact: dict, registry_ref: str) -> str:
    """Digest an earlier push of this exact reference already earned.

    Gate scripts run more than once per unit, and this registry refuses the
    second push of a tag it already holds. Without the digest from the first
    push, the rerun either discards a good result or fails a unit that
    actually succeeded.
    """
    if str(artifact.get("registry_ref") or "") != registry_ref:
        return ""
    push = artifact.get("registry_push")
    if not isinstance(push, dict):
        return ""
    return str(push.get("digest") or "")


def registry_tag_digest(image_ref: str, log_path: Path) -> str:
    """Manifest digest the registry currently serves for this reference.

    ``vc submit`` takes ``repo:tag`` only and answers 镜像不存在 to any
    ``repo@sha256:...`` reference, so a job cannot carry the pin in the
    reference it runs. Pull the tag and read the digest back instead, which
    makes the pin something the submission proves rather than states.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["docker", "pull", image_ref]
    result = run_command(command, env=proxy_cleared_env(), timeout=3600)
    output = f"{result.stdout}\n{result.stderr}".strip()
    with log_path.open("a", encoding="utf-8", buffering=1) as handle:
        handle.write(f"=== {datetime.now(timezone.utc).isoformat()} resolve {image_ref} ===\n")
        handle.write(f"$ {' '.join(command)}\n{output}\nexit_code={result.returncode}\n")
    if result.returncode != 0:
        raise ValueError(f"docker pull {image_ref} failed ({result.returncode}); see {log_path}.\n{output}")
    match = _DIGEST_RE.search(output)
    if not match:
        raise ValueError(
            f"docker pull {image_ref} reported no manifest digest; see {log_path}. "
            f"Without it the submission cannot prove which image it runs.\n{output}"
        )
    return match.group(1)


def ensure_registry_image(
    local_image: str, registry_ref: str, log_path: Path, known_digest: str = ""
) -> str:
    """Tag a local image with its registry name and push it (idempotent).

    Returns the manifest digest reported by ``docker push`` (empty string when
    the output did not carry one).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ["docker", "tag", local_image, registry_ref],
        ["docker", "push", registry_ref],
    ]
    # A rerun of the same gate re-pushes a tag the registry already holds and is
    # refused with no digest; seeding from the earlier push keeps that result.
    digest = known_digest
    # Line buffered: a push can take the better part of an hour, and a block
    # buffered handle shows a reader nothing until the function returns.
    with log_path.open("a", encoding="utf-8", buffering=1) as handle:
        handle.write(
            f"=== {datetime.now(timezone.utc).isoformat()} pid={os.getpid()} "
            f"tag+push {registry_ref} ===\n"
        )
        for command in commands:
            handle.write(f"$ {' '.join(command)}\n")
            result = run_command(command, env=proxy_cleared_env(), timeout=3600)
            handle.write(result.stdout)
            handle.write(result.stderr)
            handle.write(f"exit_code={result.returncode}\n")
            output = f"{result.stdout}\n{result.stderr}".strip()
            if command[1] == "push":
                match = _DIGEST_RE.search(output)
                if match:
                    digest = match.group(1)
            if result.returncode != 0 or (command[1] == "push" and not digest):
                reason = (
                    f"exit code {result.returncode}"
                    if result.returncode != 0
                    else "exit code 0 but no manifest digest in the output"
                )
                raise ValueError(
                    f"{' '.join(command)} failed ({reason}); see {log_path}. "
                    f"The registry enforces the hpc/ai_asr-* naming spec and rejects tag reuse; "
                    f"bump image_version when the content changed.\n{output}"
                )
    return digest


@dataclass
class VcSpec:
    image: str
    mounts: list[str]
    command: list[str]
    env: dict[str, str]
    workdir: str = ""


_IGNORED_DOCKER_FLAGS: dict[str, int] = {
    "--rm": 0,
    "-i": 0,
    "-t": 0,
    "-it": 0,
    "-d": 0,
    "--gpus": 1,
    "--name": 1,
    "--user": 1,
    "-u": 1,
    "--runtime": 1,
    "--shm-size": 1,
    "--network": 1,
    "--net": 1,
    "--ipc": 1,
    "--ulimit": 1,
    "--cap-add": 1,
    "--cap-drop": 1,
    "--platform": 1,
    "--cpus": 1,
    "--memory": 1,
    "--memory-swap": 1,
    "--pull": 1,
    "--hostname": 1,
    "--restart": 1,
    "--detach-keys": 1,
    "--stop-signal": 1,
    "--log-driver": 1,
    "--log-opt": 1,
    "--pid": 1,
    "--security-opt": 1,
}


def _require_value(tokens: list[str], index: int, flag: str) -> str:
    if index >= len(tokens):
        raise ValueError(f"docker run_command flag {flag} is missing its value")
    return tokens[index]


def _merge_env(env: dict[str, str], assignment: str) -> None:
    if "=" not in assignment:
        env[assignment] = os.environ.get(assignment, "")
        return
    key, _, value = assignment.partition("=")
    env[key] = value


def _validate_mount(mount: str) -> None:
    parts = mount.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid docker volume in run_command: {mount!r}")
    host = Path(parts[0]).expanduser()
    target = Path(parts[1])
    if not host.is_absolute() or not target.is_absolute():
        raise ValueError(f"vc volume host and target must be absolute: {mount!r}")
    if len(parts) == 3 and parts[2] not in {"ro", "rw"}:
        raise ValueError(f"vc volume mode must be ro or rw: {mount!r}")


def ensure_mount_host_paths(mounts: list[str]) -> None:
    """Prepare vc bind-mount host sources before submit.

    The vc platform auto-creates missing host directories as its own service
    uid, which leaves them unwritable for the job (the job runs as the
    submitting user). Create missing sources here so the submitter owns them,
    and fail fast when an existing writable source is not usable.
    """
    for mount in mounts:
        parts = mount.split(":")
        host = parts[0]
        if not host:
            raise ValueError(f"invalid vc volume: {mount!r}")
        source = Path(host).expanduser()
        if not source.is_absolute():
            raise ValueError(f"vc volume host must be absolute: {mount!r}")
        source = source.resolve()
        mode = parts[2] if len(parts) == 3 else ""
        if source.exists():
            if mode == "ro":
                continue
            if source.is_dir() and not os.access(source, os.W_OK):
                raise ValueError(
                    f"vc mount host path {source} is not writable by the submitting user "
                    f"(owner uid {source.stat().st_uid}); recreate it as your user (it should "
                    "be an empty scratch dir) or point the mount at a user-owned directory, "
                    "then rerun the gate."
                )
            continue
        if mode == "ro":
            raise ValueError(f"vc read-only mount source does not exist: {source}")
        try:
            source.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"cannot create vc mount host path {source}: {error}") from error


def _docker_image_entrypoint(image: str) -> tuple[list[str] | None, list[str] | None]:
    """Resolve the image ENTRYPOINT/CMD from the local Docker daemon.

    ``docker run`` applies the image entrypoint when the command omits
    ``--entrypoint``; the vc translation must reproduce that behavior instead
    of executing the first positional argument directly.
    """
    try:
        inspected = run_command(
            ["docker", "image", "inspect", image, "--format", "{{json .Config}}"],
            timeout=60,
            env=agent_bin_cleared_env(),
        )
    except OSError as error:
        raise ValueError(
            f"cannot resolve image entrypoint for vc translation: {error}; declare an "
            "explicit `--entrypoint` in run_command or make sure docker and the local "
            "image are available on this host"
        ) from error
    if inspected.returncode != 0:
        raise ValueError(
            f"docker image inspect failed for {image!r}: "
            f"{(inspected.stderr or inspected.stdout).strip() or 'unknown error'}; "
            "declare an explicit `--entrypoint` in run_command or load the image locally"
        )
    try:
        config = json.loads(inspected.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"docker image inspect returned unparsable output for {image!r}") from error
    if isinstance(config, list):
        if not config or not isinstance(config[0], dict):
            raise ValueError(f"docker image inspect returned unexpected output for {image!r}")
        config = config[0]
    if not isinstance(config, dict):
        raise ValueError(f"docker image inspect returned unexpected output for {image!r}")
    entrypoint = config.get("Entrypoint")
    cmd = config.get("Cmd")
    if entrypoint is not None and not isinstance(entrypoint, list):
        raise ValueError(f"unexpected Entrypoint shape for {image!r}")
    if cmd is not None and not isinstance(cmd, list):
        raise ValueError(f"unexpected Cmd shape for {image!r}")
    return (None if entrypoint is None else list(entrypoint), None if cmd is None else list(cmd))


def docker_run_to_vc(run_command: object, resolve_entrypoint=None) -> VcSpec:
    """Translate a docker-shaped run_command into the vc job ingredients."""
    tokens = list(run_command) if isinstance(run_command, list) else shlex.split(str(run_command))
    if len(tokens) < 2 or tokens[0] != "docker" or tokens[1] != "run":
        raise ValueError("vc run_command must be `docker run ...` in list form")
    mounts: list[str] = []
    env: dict[str, str] = {}
    entrypoint: str | None = None
    entrypoint_given = False
    workdir = ""
    image: str | None = None
    rest: list[str] = []
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token in ("-v", "--volume"):
            mount = _require_value(tokens, index + 1, token)
            mounts.append(mount)
            index += 2
            continue
        if token.startswith("--volume="):
            mounts.append(token.split("=", 1)[1])
            index += 1
            continue
        if token == "--mount":
            raise ValueError("--mount is not supported in vc run_command; use -v host:container[:ro]")
        if token in ("-e", "--env"):
            _merge_env(env, _require_value(tokens, index + 1, token))
            index += 2
            continue
        if token.startswith("--env="):
            _merge_env(env, token.split("=", 1)[1])
            index += 1
            continue
        if token == "--entrypoint":
            entrypoint = _require_value(tokens, index + 1, token)
            entrypoint_given = True
            index += 2
            continue
        if token.startswith("--entrypoint="):
            entrypoint = token.split("=", 1)[1]
            entrypoint_given = True
            index += 1
            continue
        if token in ("-w", "--workdir"):
            workdir = _require_value(tokens, index + 1, token)
            index += 2
            continue
        if token.startswith("--workdir="):
            workdir = token.split("=", 1)[1]
            index += 1
            continue
        if token in _IGNORED_DOCKER_FLAGS:
            index += 1 + _IGNORED_DOCKER_FLAGS[token]
            continue
        if token.startswith("-"):
            raise ValueError(f"unsupported docker flag in vc run_command: {token}")
        image = token
        rest = tokens[index + 1 :]
        break
    if not image:
        raise ValueError("docker run_command must declare an image")
    for mount in mounts:
        _validate_mount(mount)
    if entrypoint_given:
        command = ([entrypoint] if entrypoint else []) + rest
    else:
        if resolve_entrypoint is None:
            resolve_entrypoint = _docker_image_entrypoint
        image_entrypoint, image_cmd = resolve_entrypoint(image)
        command = list(image_entrypoint or []) + (list(rest) if rest else list(image_cmd or []))
    if not command:
        raise ValueError("docker run_command must declare a container command")
    return VcSpec(image=image, mounts=mounts, command=command, env=env, workdir=workdir)


def parse_job_id(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if lines:
        last = lines[-1]
        if _JOB_ID_RE.fullmatch(last):
            return last
    match = _JOB_ID_RE.search(stdout or "")
    if not match:
        raise ValueError(f"vc submit returned no recognizable job id: {stdout!r}")
    return match.group(0)


def diagnose_oom(exit_code: int | None, evidence: str) -> str | None:
    """Map job failure evidence to a targeted repair hint for OOM causes.

    ``evidence`` is any combined text (container stdout/stderr plus vc
    diagnostics). Returns None when no known OOM signature matches.
    """
    text = (evidence or "").lower()
    if exit_code == 137 or "oomkilled" in text:
        return (
            "job was OOM-killed (exit 137): RAM request too small. Raise vc_memory_gb "
            "(the partition caps 32 GiB per GPU; request more GPUs to raise it, e.g. "
            "vc_gpus=2 vc_memory_gb=64), then rerun the gate."
        )
    if GPU_OOM_MARKER in text:
        return (
            "GPU VRAM exhausted (RTX 4090 has 24 GiB): reduce batch/beam size, enable bf16, "
            "or shard the model, then rerun the gate."
        )
    if any(marker in text for marker in RAM_OOM_MARKERS) or "killed" in text:
        return (
            "job RAM exhausted: raise vc_memory_gb (the partition caps 32 GiB per GPU; "
            "request more GPUs to raise it, e.g. vc_gpus=2 vc_memory_gb=64), then rerun the gate."
        )
    return None


def render_inner_script(
    log_dir: Path,
    command: str,
    env: dict[str, str],
    command_timeout_seconds: float | None = None,
    workdir: str = "",
) -> None:
    exports = [f"export {shlex.quote(key)}={shlex.quote(str(value))}" for key, value in sorted(env.items())]
    lines = [
        "#!/usr/bin/env bash",
        "set -u",
        f"OUT={shlex.quote(str(log_dir))}",
    ]
    lines.extend(exports)
    if workdir:
        # docker_run_to_vc parses -w/--workdir but nothing used to act on it, so
        # the job ran in the image default and relative paths did not resolve.
        lines.append(f"cd {shlex.quote(workdir)}")
    if command_timeout_seconds and command_timeout_seconds > 0:
        hard_timeout = f"timeout --kill-after=15 {int(command_timeout_seconds)} {command}"
        body = [
            "if command -v timeout >/dev/null 2>&1; then",
            f"  {hard_timeout}",
            "else",
            f"  {command}",
            "fi",
        ]
    else:
        body = [command]
    lines.extend(["{", *body, '} >"$OUT/stdout.log" 2>"$OUT/stderr.log"'])
    lines.extend(
        [
            'code=$?',
            "printf '%s\\n' \"$code\" > \"$OUT/exit_code\"",
            f'echo "{DONE_MARKER} exit=$code"',
            "exit 0",
        ]
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "inner.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class VcJobResult:
    exit_code: int | None
    stdout: str
    stderr: str
    job_id: str
    partition: str
    submit_command: list[str]
    duration_ms: float
    timed_out: bool
    log_dir: Path
    vc_diagnostics: str


def collect_diagnostics(job_id: str, log_dir: Path) -> str:
    """Best-effort vc evidence for the job.

    These are supporting notes, not the result. A vc info or vc logs call that
    timed out used to propagate out of run_vc_job before the authoritative exit
    code was ever read, turning a finished job into a failed one.
    """
    chunks: list[str] = []
    commands = [
        ["vc", "info", "--job", job_id],
        ["vc", "logs", "-t", f"{job_id}-master-0", "-l", "200"],
    ]
    timeouts = [60, 120]
    for command, timeout in zip(commands, timeouts):
        try:
            result = run_command(command, timeout=timeout)
            body = f"{result.stdout}{result.stderr}"
        except (OSError, subprocess.SubprocessError) as error:
            body = f"diagnostics unavailable: {error!r}"
        chunks.append(f"$ {chr(32).join(command)}\n{body}".rstrip())
    rendered = "\n\n".join(chunks) + "\n"
    try:
        (log_dir / "vc_job.log").write_text(rendered, encoding="utf-8")
    except OSError:
        pass
    return rendered


def run_vc_job(
    *,
    image: str,
    command: str,
    log_dir: Path,
    mounts: list[str] | None = None,
    env: dict[str, str] | None = None,
    partition: str | None = None,
    project: str = DEFAULT_PROJECT,
    gpus: int = DEFAULT_GPUS,
    memory_gb: int = DEFAULT_MEMORY_GB,
    cpus: int = DEFAULT_CPUS,
    job_name: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    workdir: str = "",
) -> VcJobResult:
    partition = partition or default_partition()
    if not vc_available():
        raise ValueError(
            "vc is required for GPU validation: `which vc && vc info` did not pass on this host"
        )
    allowed = user_partitions()
    if partition not in allowed:
        raise ValueError(
            f"vc partition {partition!r} is not available to this user "
            f"(visible partitions: {sorted(allowed)}); ask the cluster admin for access"
        )
    log_dir = log_dir.resolve()
    mount_list = list(mounts or [])
    covered = [Path(mount.split(":", 1)[0]).expanduser().resolve() for mount in mount_list]
    if not any(log_dir == host or log_dir.is_relative_to(host) for host in covered):
        mount_list.append(f"{log_dir}:{log_dir}")
    render_inner_script(log_dir, command, env or {}, command_timeout_seconds, workdir=workdir)
    clear_previous_result(log_dir)
    ensure_mount_host_paths(mount_list)
    name = normalize_job_name(job_name or f"sure-trans-{log_dir.name}-{int(time.time())}")
    submit_command = [
        "vc", "submit",
        "-i", image,
        "-p", partition,
        "-g", str(gpus),
        "-m", f"{memory_gb}G",
        "-c", str(cpus),
        "-n", "1",
        "-j", name,
        "--project", project,
        "-v", ",".join(mount_list),
        "--cmd", inner_script_command(log_dir),
    ]
    started = time.monotonic()
    submitted = run_command(submit_command, timeout=300)
    if submitted.returncode != 0:
        raise ValueError(
            f"vc submit failed ({submitted.returncode}): "
            f"{(submitted.stderr or submitted.stdout).strip()}"
        )
    job_id = parse_job_id(submitted.stdout)
    exit_code_path = log_dir / "exit_code"
    timed_out = True
    while True:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        if exit_code_path.is_file() and exit_code_path.read_text(encoding="utf-8").strip():
            timed_out = False
            break
        # Truncate the last sleep so timeout_seconds is the wait, not the wait
        # rounded up to the next poll.
        time.sleep(min(poll_interval, remaining))
    diagnostics = collect_diagnostics(job_id, log_dir)
    if timed_out:
        diagnostics = f"{diagnostics}\n{cancel_vc_job(job_id)}\n"
    exit_code: int | None = None
    if not timed_out:
        try:
            exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            exit_code = None
    stdout = (log_dir / "stdout.log").read_text(encoding="utf-8", errors="replace") if (log_dir / "stdout.log").is_file() else ""
    stderr = (log_dir / "stderr.log").read_text(encoding="utf-8", errors="replace") if (log_dir / "stderr.log").is_file() else ""
    return VcJobResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        job_id=job_id,
        partition=partition,
        submit_command=submit_command,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
        timed_out=timed_out,
        log_dir=log_dir,
        vc_diagnostics=diagnostics,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a bounded GPU command to the vc queue and wait for completion.")
    parser.add_argument("--image", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--mount", action="append", default=[])
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--partition", default=None, help="defaults to the site policy partition")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--gpus", type=int, default=DEFAULT_GPUS)
    parser.add_argument("--memory-gb", type=int, default=DEFAULT_MEMORY_GB)
    parser.add_argument("--cpus", type=int, default=DEFAULT_CPUS)
    parser.add_argument("--job-name")
    parser.add_argument("--timeout-seconds", type=bounded_seconds, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--command-timeout-seconds", type=bounded_seconds, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--produces", required=True)
    parser.add_argument(
        "--expect-digest",
        default="",
        help="refuse to submit unless --image resolves to this manifest digest",
    )
    args = parser.parse_args()

    env: dict[str, str] = {}
    for assignment in args.env:
        _merge_env(env, assignment)
    for mount in args.mount:
        _validate_mount(mount)
    resolved_digest = ""
    if args.expect_digest:
        try:
            resolved_digest = registry_tag_digest(args.image, Path(args.log_dir).resolve() / "resolve.log")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(str(exc))
            return 1
        if resolved_digest != args.expect_digest:
            print(
                f"{args.image} now serves {resolved_digest}, not the pinned {args.expect_digest}; "
                f"refusing to submit. The tag moved after it was validated: rebuild and repush "
                f"under a new image_version, or pin the digest that is actually deployed."
            )
            return 1
    try:
        result = run_vc_job(
            image=args.image,
            command=args.command,
            log_dir=Path(args.log_dir).resolve(),
            mounts=args.mount,
            env=env,
            partition=args.partition,
            project=args.project,
            gpus=args.gpus,
            memory_gb=args.memory_gb,
            cpus=args.cpus,
            job_name=args.job_name,
            timeout_seconds=args.timeout_seconds,
            command_timeout_seconds=args.command_timeout_seconds,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc))
        return 1
    status = "failed"
    if result.timed_out:
        status = "timeout"
    elif result.exit_code == 0:
        status = "passed"
    payload = {
        "status": status,
        "exit_code": result.exit_code,
        "image_ref": args.image,
        "resolved_digest": resolved_digest,
        "vc_job_id": result.job_id,
        "vc_partition": result.partition,
        "vc_submit_command": result.submit_command,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "stdout_log": str(result.log_dir / "stdout.log"),
        "stderr_log": str(result.log_dir / "stderr.log"),
        "vc_job_log": str(result.log_dir / "vc_job.log"),
        "diagnostics": result.vc_diagnostics[:8000],
    }
    output = Path(args.produces).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
