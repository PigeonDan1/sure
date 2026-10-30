#!/usr/bin/env python3
"""Call native SURE with correlated requests, durable receipts and bounded execution."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
TERMINAL = {"success", "failed", "incomplete", "cancelled"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def configuration_lock(path, exclusive=True):
    """Nonblocking locks serialize init against other calls and reserve output paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            else:
                import msvcrt
                handle.write(b"\0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValueError("Another invocation owns this configuration/output lock; retry after it finishes.") from error
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def cancellation_signals():
    stopped = [None]
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda received, _frame: stopped.__setitem__(0, received))
    try:
        yield stopped
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def stop_process_tree(process, force=False):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        else:
            process.send_signal(signal.CTRL_BREAK_EVENT)
    except (ProcessLookupError, PermissionError, OSError):
        if process.poll() is None:
            process.kill() if force else process.terminate()


def managed_run(argv, cwd, stdout_path, stderr_path, deadline, grace, stopped, progress=None):
    if stopped[0]:
        return -stopped[0], "cancelled"
    if time.monotonic() >= deadline:
        return 124, "timed_out"
    kwargs = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    reason = None
    stop_started = None
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **kwargs)
        try:
            while True:
                if progress:
                    progress(process.pid)
                if stopped[0] and reason is None:
                    reason = "cancelled"
                elif time.monotonic() >= deadline and reason is None:
                    reason = "timed_out"
                if reason and stop_started is None:
                    stop_started = time.monotonic()
                    stop_process_tree(process)
                if stop_started is not None and time.monotonic() - stop_started >= grace:
                    stop_process_tree(process, force=True)
                    process.wait(timeout=10)
                    break
                if process.poll() is not None:
                    if reason:
                        stop_process_tree(process, force=True)
                    break
                time.sleep(0.05)
        except BaseException:
            stop_process_tree(process, force=True)
            process.wait(timeout=10)
            raise
    return process.returncode, reason


def source_command(script):
    return ["node", "--import", (ROOT / "packages/coding-agent/test/source-resolver.ts").as_uri(), str(ROOT / script)]


def native_inspect(root, request, stopped, deadline, grace):
    with tempfile.TemporaryDirectory(prefix="sure-inspect-") as temporary:
        directory = Path(temporary)
        argv = source_command("scripts/sure-agent-bridge.ts")
        if request is not None:
            request_path = directory / "request.json"
            atomic_json(request_path, request)
            argv.append(str(request_path))
        code, reason = managed_run(argv, root, directory / "stdout", directory / "stderr",
                                   min(deadline, time.monotonic() + 30), grace, stopped)
        if reason:
            return None, reason
        try:
            response = read_object(directory / "stdout")
        except (ValueError, OSError) as error:
            detail = (directory / "stderr").read_text(encoding="utf-8", errors="replace")[-1500:]
            raise ValueError(f"Native discovery failed; install harness dependencies. {detail}") from error
        if code or response.get("status") != "success":
            raise ValueError(response.get("error", "Native discovery rejected the request."))
        return response, None


def dispatch_reply(log_path, request):
    """Only native extension notifications carry the transport response."""
    if not log_path.exists():
        return None
    found = None
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("type") != "extension_notify":
                    continue
                reply = json.loads(event.get("message", ""))
                if (isinstance(reply, dict) and reply.get("schema") == "sure.agent.dispatch.v1"
                        and reply.get("invocation_id") == request["invocation_id"]
                        and reply.get("command") == request["command"]):
                    found = reply
            except (ValueError, TypeError):
                continue
    return found


def validate_result(root, request, reply):
    if (not isinstance(reply, dict) or reply.get("schema") != "sure.agent.dispatch.v1"
            or reply.get("invocation_id") != request["invocation_id"] or reply.get("command") != request["command"]):
        raise ValueError("No correlated native dispatch response; inspect the harness logs.")
    if reply.get("status") == "blocked":
        return {"status": "blocked", "error": reply.get("error") or "Native prerequisites were not met.",
                "next_action": reply.get("next_action")}
    if request["command"] == "sure_init":
        manifest = reply.get("init_manifest")
        if (reply.get("status") != "success" or not isinstance(manifest, dict)
                or manifest.get("trusted") is not True or manifest.get("uvOk") is not True
                or not isinstance(manifest.get("defaultModel"), str) or not manifest["defaultModel"]
                or not isinstance(reply.get("expected_model"), str) or manifest["defaultModel"] != reply["expected_model"]
                or not isinstance(manifest.get("defaultProvider"), str) or not manifest["defaultProvider"]):
            raise ValueError("Native initialization response is not a valid success.")
        return {"status": "success", "init_manifest": manifest, "next_action": reply.get("next_action")}
    run_id = reply.get("run_id")
    if reply.get("status") != "accepted":
        raise ValueError("Native dispatch did not accept this workflow.")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Native dispatch response has no valid run ID.")
    run_dir = (root / ".sure/runs" / run_id).resolve()
    if not isinstance(reply.get("run_dir"), str) or Path(reply["run_dir"]).resolve() != run_dir:
        raise ValueError("Native run directory does not match the run ID.")
    record_path = run_dir / "run.json"
    record = read_object(record_path)
    if (record.get("runId") != run_id or record.get("invocationId") != request["invocation_id"]
            or not isinstance(record.get("command"), str) or not record["command"]
            or not isinstance(record.get("runDir"), str) or Path(record["runDir"]).resolve() != run_dir
            or record.get("command") != reply.get("run_command")):
        raise ValueError("Native run record does not belong to this invocation.")
    if request["command"] != "sure_resume" and record["command"] != request["command"]:
        raise ValueError("Native command does not match the request.")
    status = record.get("status")
    if not isinstance(status, str) or status not in TERMINAL or not record.get("finishedAt"):
        raise ValueError(f"Native run has no terminal status: {status!r}.")
    result = {"status": status, "run_id": run_id, "run_dir": str(run_dir),
              "record_path": str(record_path), "record_sha256": digest(record_path),
              "events_path": str(run_dir / "events.jsonl"), "state_path": str(run_dir / "state.json"),
              "error": record.get("errorSummary") or record.get("lastRepair")}
    result["resumed"] = request["command"] == "sure_resume"
    if request["command"] != "sure_resume":
        output = Path(request["output_dir"]).resolve()
        if not isinstance(record.get("outputDir"), str) or Path(record["outputDir"]).resolve() != output:
            raise ValueError("Native output directory does not match the structured request.")
        native_path = output / "result.json"
        native = read_object(native_path)
        products = native.get("products")
        if (native.get("schema") != "sure.run_result.v1" or native.get("invocation_id") != request["invocation_id"]
                or native.get("command") != "/" + request["command"] or native.get("run_id") != run_id
                or native.get("status") != status or not isinstance(native.get("run_dir"), str)
                or Path(native["run_dir"]).resolve() != run_dir or not isinstance(products, list)
                or not all(isinstance(item, str) for item in products)):
            raise ValueError("Native result is incomplete or does not match its run record.")
        for product in products:
            path = (output / product).resolve()
            if not path.is_relative_to(output) or not path.exists():
                raise ValueError("Native product is missing or escapes the output directory.")
        result.update(evidence_path=str(native_path), products=products)
    if status == "success":
        if not isinstance(record.get("manifestPath"), str):
            raise ValueError("Successful run has no accepted manifest path.")
        manifest_path = Path(record["manifestPath"]).resolve()
        if not manifest_path.is_relative_to(root.resolve()):
            raise ValueError("Accepted manifest is outside the SURE workspace.")
        manifest = read_object(manifest_path)
        if (manifest.get("run_id") != run_id or manifest.get("skill_name") != record.get("skillName")
                or manifest.get("status") != status or not isinstance(manifest.get("schema_version"), str)
                or not all(isinstance(manifest.get(key), dict) for key in ("inputs", "outputs", "validation"))):
            raise ValueError("Accepted manifest does not match the successful run.")
        result.update(manifest_path=str(manifest_path), manifest_sha256=digest(manifest_path))
    return result


def trace_summary(result, log_path):
    counts = {"tool_calls": 0, "tool_errors": 0, "tool_blocks": 0, "finish_repairs": 0, "continuations": 0, "provider_retries": 0}
    event_path = result.get("events_path")
    if event_path and Path(event_path).exists():
        with Path(event_path).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "resumed" and result.get("resumed"):
                        counts = {key: 0 for key in counts}
                    for name, expected in {"tool_calls": "tool_call", "tool_blocks": "tool_call_repair",
                                           "finish_repairs": "finish_repair", "continuations": "finish_missing"}.items():
                        counts[name] += kind == expected
                    counts["tool_errors"] += kind == "tool_result" and event.get("data", {}).get("isError") is True
                except (ValueError, AttributeError):
                    continue
    if log_path.exists():
        with log_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                    counts["provider_retries"] += isinstance(event, dict) and event.get("type") == "auto_retry_start"
                except ValueError:
                    continue
    return counts


def invoke(options, root=ROOT):
    timeout = getattr(options, "timeout_seconds", 3600.0)
    grace = getattr(options, "kill_grace_seconds", 5.0)
    if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(grace) or grace < 0:
        return {"status": "blocked", "error": "Timeout must be positive and grace nonnegative; both must be finite."}, 1
    invocation_id = uuid4().hex
    name = options.command.lstrip("/")
    name = name if name.startswith("sure_") else "sure_" + name
    receipt_dir = root / ".sure/agent-invocations" / invocation_id
    executing = options.command != "list" and not options.dry_run
    result = {"schema": "sure.agent.receipt.v1", "invocation_id": invocation_id, "command": "/" + name,
              "status": "running", "started_at": now(), "attempt": 1, "automatic_retries": 0}
    log_path = receipt_dir / "harness.jsonl"
    deadline = time.monotonic() + timeout

    def publish(event):
        if executing:
            result["updated_at"] = now()
            atomic_json(receipt_dir / "receipt.json", result)
            with (receipt_dir / "invocation.events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": event, "timestamp": now(), "status": result["status"]}) + "\n")

    if executing:
        receipt_dir.mkdir(parents=True, mode=0o700)
        result.update(receipt_dir=str(receipt_dir), receipt_path=str(receipt_dir / "receipt.json"))
        publish("created")
        print(json.dumps({"event": "invocation_started", "invocation_id": invocation_id,
                          "receipt_path": result["receipt_path"]}), file=sys.stderr, flush=True)
    with cancellation_signals() as stopped, ExitStack() as locks:
        try:
            if options.command == "list":
                response, reason = native_inspect(root, None, stopped, deadline, grace)
                if reason:
                    return {**result, "status": reason}, 128 + stopped[0] if stopped[0] else 124
                return {"status": "success", "root": str(root), **response["discovery"]}, 0
            arguments = options.args_file.read_text(encoding="utf-8") if options.args_file else options.arguments
            request = {"schema": "sure.agent.request.v1", "invocation_id": invocation_id,
                       "command": name, "arguments": arguments}
            if name not in ("sure_init", "sure_resume") or options.output_dir is not None:
                output = options.output_dir.expanduser().resolve() if options.output_dir else receipt_dir / "output"
                request["output_dir"] = str(output)
            response, reason = native_inspect(root, request, stopped, deadline, grace)
            if reason:
                result["status"] = reason
            else:
                request = response["request"]
                argv = source_command("packages/coding-agent/src/cli.ts") + [
                    "--offline", "--mode", "json", "--no-session",
                    "--approve" if options.trust else "--no-approve", "-p",
                    "/sure_invoke " + json.dumps(request, ensure_ascii=False),
                ]
                if options.dry_run:
                    return {"status": "planned", "command": "/" + name, "cwd": str(root), "argv": argv,
                            "discovery": response["discovery"]}, 0
                locks.enter_context(configuration_lock(root / ".sure/agent-invocations/configuration.lock", name == "sure_init"))
                if name == "sure_resume":
                    locks.enter_context(configuration_lock(root / ".sure/agent-invocations/locks" / ("run-" + arguments.strip())))
                if "output_dir" in request:
                    output = Path(request["output_dir"])
                    key = hashlib.sha256(str(output.resolve()).encode()).hexdigest()
                    locks.enter_context(configuration_lock(root / ".sure/agent-invocations/locks" / key))
                    if output.exists() and (not output.is_dir() or any(output.iterdir())):
                        raise ValueError("Use a new or empty output directory; old results are not reused.")
                atomic_json(receipt_dir / "request.json", request)
                result["request_sha256"] = digest(receipt_dir / "request.json")
                result["transport_sha256"] = digest(ROOT / "packages/coding-agent/src/core/sure/agent-invocation.ts")
                result["wrapper_sha256"] = digest(Path(__file__))
                result["discovery"] = response["discovery"]
                publish("validated")
                seen_reply = [None]

                def progress(pid):
                    if seen_reply[0] is not None:
                        return
                    reply = dispatch_reply(log_path, request)
                    if reply and reply != seen_reply[0]:
                        seen_reply[0] = reply
                        result.update({key: reply[key] for key in ("run_id", "run_dir") if key in reply})
                        result["pid"] = pid
                        publish("native_dispatch")

                code, reason = managed_run(argv, root, log_path, receipt_dir / "harness.stderr.log",
                                           deadline, grace, stopped, progress)
                result["exit_code"] = code
                reply = dispatch_reply(log_path, request)
                if reason:
                    result.update(status=reason, error="Native process tree was stopped before returning.")
                    if reply:
                        result.update({key: reply[key] for key in ("run_id", "run_dir") if key in reply})
                        if result.get("run_dir"):
                            result["events_path"] = str(Path(result["run_dir"]) / "events.jsonl")
                else:
                    result.update(validate_result(root, request, reply))
                    if code:
                        result.update(status="failed", error=result.get("error") or f"Native CLI exited {code}.")
                result["trace_summary"] = trace_summary(result, log_path)
                for field, filename in (("record_path", "run.snapshot.json"), ("manifest_path", "manifest.snapshot.json"),
                                        ("events_path", "native.events.jsonl"), ("state_path", "state.snapshot.json")):
                    if isinstance(result.get(field), str) and Path(result[field]).is_file():
                        snapshot = receipt_dir / filename
                        shutil.copyfile(result[field], snapshot)
                        result[field + "_snapshot"] = str(snapshot)
                        result[field + "_snapshot_sha256"] = digest(snapshot)
        except (ValueError, OSError, TypeError) as error:
            result.update(status="blocked", error=str(error))
        finally:
            result["finished_at"] = now()
            publish("finished")
        if result["status"] == "cancelled":
            return result, 128 + (stopped[0] or signal.SIGINT)
        if result["status"] == "timed_out":
            return result, 124
        return result, 0 if result["status"] == "success" else 1


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main():
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("command", help="list, init, feed, onboard, trans, approve, infer, eval, agent_eval, resume")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--arguments", default="", help="Native arguments, preserved without shell re-parsing")
    group.add_argument("--args-file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--trust", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--kill-grace-seconds", type=float, default=5)
    try:
        result, code = invoke(parser.parse_args())
    except (ValueError, OSError) as error:
        result, code = {"status": "blocked", "error": str(error)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
