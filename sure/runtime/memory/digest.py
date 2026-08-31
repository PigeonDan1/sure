#!/usr/bin/env python3
"""Build artifacts/run_digest.json for the extract_lessons unit (design spec §4.3).

The digest is the only view of "what happened in this run" the extraction agent
gets, so it is rebuilt from events.jsonl (append-only history) and never from
state.json (which only keeps the latest patch). Everything here is best effort:
a missing file shrinks the digest, an exception turns it into
{"schema": ..., "error": ...}; the hook that calls us never blocks on it.

Rules that are easy to get wrong (all from the spec / skeleton §1.13):
- run_id is the run directory name, never a field read from a file;
- no absolute path and no output_dir string may end up in the digest: log_tail.path
  keeps the log_paths.json template text, run.args is stripped of output_dir,
  target.id is a model id, and resolved-input files are never copied wholesale;
- units are inferred from events <= cutoff only; --mark-passed marks the unit whose
  advance has not been written to events yet (the hook counts cutoff before the
  post_tool_result_state line lands);
- repairs prefer the raw gate text in tool_result_repair.state_patch.diagnostics[].repair
  and otherwise strip the injected Memory block by its config.json header.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from . import paths
except ImportError:  # executed as a script: python sure/runtime/memory/digest.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from memory import paths  # type: ignore[no-redef]

SCHEMA = "sure.memory.run_digest.v1"
TERMINAL_STATUSES = frozenset({"success", "failed", "incomplete", "cancelled"})
NON_SUCCESS_TERMINAL = frozenset({"failed", "incomplete", "cancelled"})
ARTIFACT_PREFIX = "artifact:"
SNAPSHOT_FIT_STEP = "index_snapshot_fit"
"""digest_trim_order step that drops memory_index_snapshot rows until the digest fits its budget."""
_SNAPSHOT_FIT_AFTER = "index_snapshot_min"
_SNAPSHOT_DROP_RANK = {"provisional": 1, "disputed": 2, "confirmed": 3}
_EXHAUSTED_RE = re.compile(r'^Gate "([^"]+)" exhausted')
_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")
_CANDIDATE_PREFIX_RE = re.compile(r"^\d+-")
_OMITTED_RE = re.compile(r"\n\.\.\.\[(\d+) chars omitted\]\.\.\.\n")


# --- small text helpers ---------------------------------------------------------

def strip_output_dir(args: str) -> str:
    """Port of splitOutputDir().rest in packages/coding-agent/src/core/sure/output-dir.ts:
    drop `output_dir=<v>` tokens and `--output_dir <v>` / `-output_dir <v>` / `output_dir <v>` pairs."""
    tokens = [token for token in (args or "").strip().split() if token]
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        eq = token.find("=")
        if eq >= 0:
            if token[:eq] != "output_dir":
                rest.append(token)
            i += 1
            continue
        if re.sub(r"^--?", "", token) != "output_dir":
            rest.append(token)
            i += 1
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        i += 2 if (nxt is not None and not nxt.startswith("-")) else 1
    return " ".join(rest)


def parse_args(args: str) -> dict[str, str]:
    """Minimal port of the hooks' parseArgs: `k=v` tokens and `--k v` pairs. Used only for the
    `model=` / `model_id=` target fallback, so model_input_path sniffing is left out on purpose."""
    out: dict[str, str] = {}
    tokens = [token for token in (args or "").strip().split() if token]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        eq = token.find("=")
        if eq >= 0:
            out[token[:eq]] = token[eq + 1:]
            i += 1
            continue
        key = re.sub(r"^--?", "", token)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and not nxt.startswith("-"):
            out[key] = nxt
            i += 2
        else:
            out[key] = "true"
            i += 1
    return out


def output_dir_forms(record: dict, args: str) -> list[str]:
    """Every spelling of a run's output_dir: what the invocation typed, what the harness resolved
    into run.json outputDir, and the realpath of each - a gate quoting the directory under its
    canonical mount alias names a path the agent never typed. Longest first so a spelling is
    replaced before a shorter one that is a prefix of it. Only absolute forms count: a relative
    spelling such as `out` is a substring of ordinary words, and its realpath is in the list anyway."""
    forms: list[str] = []
    for raw in (parse_args(args).get("output_dir"), record.get("outputDir")):
        if not isinstance(raw, str) or not raw.strip():
            continue
        for value in (raw, os.path.realpath(raw)):
            value = value.rstrip("/\\")  # a bare separator is dropped: masking it would mask everything
            if value and os.path.isabs(value) and value not in forms:
                forms.append(value)
    return sorted(forms, key=len, reverse=True)


def mask_output_dir(value: Any, forms: list[str]) -> Any:
    """Replace every output_dir spelling in a digest fragment with match.ts's `<path>` placeholder.
    The digest copies free text out of the run - gate repairs, log tails, tool commands - so the
    only way to keep the directory out of it is to walk everything that reached it."""
    if isinstance(value, str):
        for form in forms:
            value = value.replace(form, "<path>")
        return value
    if isinstance(value, dict):
        return {key: mask_output_dir(item, forms) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_output_dir(item, forms) for item in value]
    return value


def strip_memory_block(text: str, header: str) -> str:
    """Remove every injected Memory block: the line carrying `header` plus the non-blank lines
    that follow it (match.ts writes the block as one contiguous paragraph). Text before the header
    on the same line is kept. Returns the text unchanged when no header is present."""
    if not text or not header or header not in text:
        return text or ""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        at = line.find(header)
        if at < 0:
            out.append(line)
            i += 1
            continue
        before = line[:at].rstrip()
        if before:
            out.append(before)
        i += 1
        while i < len(lines) and lines[i].strip():  # the block's own lines
            i += 1
        while i < len(lines) and not lines[i].strip():  # blank lines after it
            i += 1
        while out and not out[-1].strip():  # blank lines before it
            out.pop()
        if out and i < len(lines):
            out.append("")  # one paragraph break between the surviving parts
    return "\n".join(out).strip("\n")


def clip_head_tail(text: str, head: int, tail: int) -> str:
    """Keep the first `head` and last `tail` characters with an omission marker in between."""
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n...[{omitted} chars omitted]...\n{text[-tail:]}"


def reclip_head_tail(text: str, head: int, tail: int) -> str:
    """clip_head_tail for text a previous clip already shortened: the new marker counts the
    characters that clip dropped as well, so the digest never understates the omission. Text
    carrying anything other than exactly one marker is clipped plainly."""
    markers = _OMITTED_RE.findall(text)
    match = _OMITTED_RE.search(text)
    if match is None or len(markers) != 1:
        return clip_head_tail(text, head, tail)
    kept_head, kept_tail = text[:match.start()], text[match.end():]
    new_head = kept_head[:head]
    new_tail = kept_tail[-tail:] if tail > 0 else ""
    omitted = len(kept_head) + int(match.group(1)) + len(kept_tail) - len(new_head) - len(new_tail)
    if omitted <= 0:
        return text
    return f"{new_head}\n...[{omitted} chars omitted]...\n{new_tail}"


def read_log_tail(path: Path, max_lines: int, max_line_chars: int, seek_bytes: int) -> list[str]:
    """Last `max_lines` lines of a log, reading at most `seek_bytes` from the end. Lines are split on
    \\r\\n, \\r and \\n (progress bars write bare \\r); a torn first line after the seek is dropped."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            partial = size > seek_bytes
            if partial:
                handle.seek(size - seek_bytes)
            raw = handle.read()
    except OSError:
        return []
    pieces = _LINE_SPLIT_RE.split(raw.decode("utf-8", errors="replace"))
    if partial and pieces:
        pieces = pieces[1:]
    while pieces and pieces[-1] == "":
        pieces.pop()
    if max_lines <= 0:
        return []
    return [line[:max_line_chars] for line in pieces[-max_lines:]]


def _read_tail_text(path: Path, seek_bytes: int) -> str:
    """Whole lines from the end of a text file (used to find the last repair of a prior run)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            partial = size > seek_bytes
            if partial:
                handle.seek(size - seek_bytes)
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if partial:
        text = text.split("\n", 1)[1] if "\n" in text else ""
    return text


def _load_json_quiet(path: Path) -> Any:
    try:
        return paths.load_json(path)
    except (OSError, ValueError):
        return None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _string_at(obj: Any, *keys: str) -> str:
    """Nested string lookup that returns "" for anything missing or non-string."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current.strip() if isinstance(current, str) else ""


# --- target and log locations -----------------------------------------------------

def resolve_target(run_dir: Path, skill: str | None, args: str) -> dict:
    """{"kind": "model"|"eval", "id": str}. Read the resolved-input artifact first, then fall back to
    `model=` / `model_id=` in args. Only the id is copied: eval_input_resolved.json also carries the
    output_dir path inside runtime.run_dir, which must never enter the digest."""
    art = Path(run_dir) / "artifacts"
    kind = "eval" if skill == "sure_eval" else "model"
    target_id = ""
    if skill == "sure_onboard":
        target_id = _string_at(_load_json_quiet(art / "model_input_resolved.json"), "model_id")
    elif skill == "sure_eval":
        target_id = _string_at(_load_json_quiet(art / "eval_input_resolved.json"), "user_input", "model")
    if not target_id:
        parsed = parse_args(args)
        target_id = parsed.get("model") or parsed.get("model_id") or ""
    return {"kind": kind, "id": target_id}


def _product_dir(run_dir: Path, skill: str | None) -> Path | None:
    """{product_dir} placeholder: onboard model_dir, eval runtime.run_dir. Used only to open logs;
    the resolved value is never written into the digest."""
    art = Path(run_dir) / "artifacts"
    if skill == "sure_onboard":
        raw = _string_at(_load_json_quiet(art / "model_input_resolved.json"), "model_dir")
    elif skill == "sure_eval":
        raw = _string_at(_load_json_quiet(art / "eval_input_resolved.json"), "runtime", "run_dir")
    else:
        raw = ""
    return Path(raw).expanduser() if raw else None


def _artifact_log_path(run_dir: Path, produces: str, product_dir: Path | None) -> Path | None:
    """`artifact:<produces>` entries: read the unit's produces JSON and resolve its string field
    `log_path` the way sure_onboard/scripts/run_validate.py log_path_for does (absolute as is,
    `artifacts/...` relative to the validation cwd = product dir, anything else under run artifacts)."""
    data = _load_json_quiet(Path(run_dir) / "artifacts" / produces)
    raw = _string_at(data, "log_path")
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    if raw.startswith("artifacts/"):
        return (product_dir / candidate) if product_dir is not None else None
    return Path(run_dir) / "artifacts" / candidate


def _find_log(run_dir: Path, skill: str | None, unit_id: str, log_paths: dict, product_dir: Path | None) -> tuple[str, Path] | None:
    """First existing log for (skill, unit): returns (label written into the digest, real path)."""
    table = log_paths.get(skill or "")
    if not isinstance(table, dict):
        return None
    candidates = table.get(unit_id)
    if not isinstance(candidates, list):
        return None
    for entry in candidates:
        if not isinstance(entry, str) or not entry:
            continue
        if entry.startswith(ARTIFACT_PREFIX):
            resolved = _artifact_log_path(run_dir, entry[len(ARTIFACT_PREFIX):], product_dir)
        else:
            if "{product_dir}" in entry and product_dir is None:
                continue
            filled = entry.replace("{run_dir}", str(run_dir))
            if product_dir is not None:
                filled = filled.replace("{product_dir}", str(product_dir))
            resolved = Path(filled)
        if resolved is not None and resolved.is_file():
            return entry, resolved
    return None


# --- events -----------------------------------------------------------------------

def _read_events(run_dir: Path, cutoff: int | None) -> tuple[list[dict], int]:
    """Parsed events within the first `cutoff` lines (all lines when None) and the effective cutoff.
    A line is one `\\n`-terminated record, the same count as hooks' readEventCount (skeleton §1.13):
    the text after the last `\\n` is a record still being written and is neither counted nor parsed;
    broken lines that do end in `\\n` count toward the cutoff but are skipped.

    The caller passes the line count it took itself, and the file only ever grows, so fewer lines
    than that means the file was truncated or could not be read. Raising there is the point: an
    empty event list is a complete run history as far as the rest of this module is concerned, and
    build_run_digest turns the exception into the {schema, error} digest the spec has for it.

    Read line by line and stopped at the cutoff rather than in one buffer: a production events.jsonl
    runs to hundreds of MB and the digest is rebuilt on every unit pass, and 0x0A never occurs inside
    a multibyte UTF-8 sequence, so per-line decoding gives what decoding the whole file would."""
    events: list[dict] = []
    complete = 0
    try:
        with (Path(run_dir) / "events.jsonl").open("rb") as handle:
            for raw in handle:
                if cutoff is not None and complete >= cutoff:
                    break
                if not raw.endswith(b"\n"):
                    break  # the torn tail; neither counted nor parsed
                complete += 1
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except OSError:
        pass  # unreadable is an empty history, and with a cutoff the check below rejects it
    if cutoff is not None and complete < cutoff:
        raise ValueError(f"events.jsonl holds {complete} complete lines, fewer than the {cutoff} counted when "
                         "the unit started; it was truncated or could not be read")
    return events, complete


def _checkpoint_of(event: dict) -> tuple[dict, dict] | None:
    """(checkpoint.data, patch) from a state event ({patch, state}) or a gate result ({state_patch})."""
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("patch", "state_patch"):
        patch = data.get(key)
        if isinstance(patch, dict):
            checkpoint = patch.get("checkpoint")
            if isinstance(checkpoint, dict) and isinstance(checkpoint.get("data"), dict):
                return checkpoint["data"], patch
    return None


def _repair_of(event: dict) -> str | None:
    """Repair text of a tool_result_repair / finish_repair event. state_patch.diagnostics[].repair is
    the gate script's raw text; the top-level repair is what the agent saw, Memory block included."""
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    patch = data.get("state_patch")
    if isinstance(patch, dict):
        for diag in patch.get("diagnostics") or []:
            if isinstance(diag, dict) and isinstance(diag.get("repair"), str) and diag["repair"].strip():
                return diag["repair"]
    if isinstance(data.get("repair"), str):
        return data["repair"]
    return None


def _tool_call_row(data: Any) -> dict | None:
    """{tool, command}: bash keeps the command, every other tool only its path."""
    if not isinstance(data, dict) or not isinstance(data.get("toolName"), str):
        return None
    tool = data["toolName"]
    payload = data.get("input") if isinstance(data.get("input"), dict) else {}
    if tool == "bash":
        command = payload.get("command")
    else:
        command = payload.get("path")
    if not isinstance(command, str) or not command.strip():
        return None
    return {"tool": tool, "command": command}


class _UnitTrack:
    __slots__ = ("blocks", "repairs", "passed", "exhausted", "first_block_pos", "pass_pos")

    def __init__(self) -> None:
        self.blocks = 0
        self.repairs: list[dict] = []
        self.passed = False
        self.exhausted = False
        self.first_block_pos: int | None = None
        self.pass_pos: int | None = None


class _EventScan:
    """Replay events in order and keep per-unit gate history plus run-level signals."""

    def __init__(self, header: str) -> None:
        self.header = header
        self.units: dict[str, _UnitTrack] = {}
        self.order: list[str] = []          # units in first-seen order (for skill-less digests)
        self.completed: list[str] = []
        self.current: str | None = None
        self.known_retries: dict[str, int] = {}
        self.tool_calls: list[tuple[int, dict]] = []
        self.tool_errors = 0
        self.terminal = False
        self.status: str | None = None
        self.count = 0

    def unit(self, unit_id: str) -> _UnitTrack:
        track = self.units.get(unit_id)
        if track is None:
            track = self.units[unit_id] = _UnitTrack()
            self.order.append(unit_id)
        return track

    def run(self, events: list[dict]) -> None:
        for pos, event in enumerate(events):
            self.count = pos + 1
            kind = event.get("type")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if kind == "tool_call":
                row = _tool_call_row(data)
                if row is not None:
                    self.tool_calls.append((pos, row))
                continue
            if kind == "tool_result":
                if data.get("isError") is True:
                    self.tool_errors += 1
                continue
            if kind in ("status", "started", "session_shutdown"):
                self._status(data.get("status"))
            elif kind == "finished":
                finish = data.get("finish") if isinstance(data.get("finish"), dict) else {}
                self._status(finish.get("status"))
            elif kind in ("agent_error", "pre_start_repair"):
                self._status("failed")
            found = _checkpoint_of(event)
            if found is not None:
                self._checkpoint(pos, *found)
            if kind == "tool_result_repair":
                self._repair(event, found[0] if found else None)

    def _status(self, status: Any) -> None:
        if isinstance(status, str) and status:
            self.status = status
            if status in TERMINAL_STATUSES:
                self.terminal = True

    def _checkpoint(self, pos: int, data: dict, patch: dict) -> None:
        current = data.get("currentUnit")
        if isinstance(current, str) and current:
            self.current = current
        for unit_id in data.get("completedUnits") or []:
            if isinstance(unit_id, str) and unit_id not in self.completed:
                self.completed.append(unit_id)
                track = self.unit(unit_id)
                track.passed = True
                track.pass_pos = pos
        retries = data.get("retries") if isinstance(data.get("retries"), dict) else {}
        for unit_id, count in retries.items():
            if not isinstance(unit_id, str) or not isinstance(count, int):
                continue
            if count > self.known_retries.get(unit_id, 0):
                self.known_retries[unit_id] = count
                track = self.unit(unit_id)
                track.blocks = max(track.blocks, count)
                if track.first_block_pos is None:
                    track.first_block_pos = pos
        phase = patch.get("phase") if isinstance(patch.get("phase"), dict) else {}
        message = patch.get("message")
        if phase.get("id") == "gate" and isinstance(message, str):
            match = _EXHAUSTED_RE.match(message)
            if match:
                self.unit(match.group(1)).exhausted = True

    def _repair(self, event: dict, checkpoint: dict | None) -> None:
        text = _repair_of(event)
        if text is None:
            return
        unit_id = None
        if checkpoint is not None and isinstance(checkpoint.get("currentUnit"), str):
            unit_id = checkpoint["currentUnit"]
        unit_id = unit_id or self.current
        if not unit_id:
            return
        attempt = self.known_retries.get(unit_id, 0)
        cleaned = strip_memory_block(text, self.header)  # a no-op for raw gate text
        self.unit(unit_id).repairs.append({"attempt": attempt, "text": cleaned})


# --- digest assembly ------------------------------------------------------------

def _outcome_rows(scan: _EventScan, registry: list[str], mark_passed: str | None, terminal: bool) -> list[tuple[str, str]]:
    """[(unit_id, outcome)] in registry order up to and including the current unit."""
    current = scan.current
    if mark_passed:
        track = scan.unit(mark_passed)
        track.passed = True
        track.pass_pos = scan.count
        if mark_passed not in scan.completed:
            scan.completed.append(mark_passed)
        if mark_passed in registry:
            idx = registry.index(mark_passed)
            current = registry[idx + 1] if idx + 1 < len(registry) else None
        else:
            current = None
    elif current is None and registry:
        current = registry[0]  # no checkpoint yet: the run is at its first unit
    if current is not None and current in scan.completed:
        current = None  # LAST_UNIT passed: advance() keeps currentUnit on it

    def outcome_for(unit_id: str) -> str:
        track = scan.units.get(unit_id)
        if unit_id in scan.completed:
            return "passed"
        if unit_id == current:
            if track is not None and (track.exhausted or (terminal and track.blocks > 0)):
                return "failed"
            return "current"
        return "skipped"

    rows: list[tuple[str, str]] = []
    listed: set[str] = set()
    for unit_id in registry:
        if current is None and unit_id not in scan.completed:
            break
        rows.append((unit_id, outcome_for(unit_id)))
        listed.add(unit_id)
        if unit_id == current:
            break
    for unit_id in scan.order:  # units seen in events but unknown to the registry
        if unit_id not in listed and (unit_id in scan.completed or unit_id == current or scan.units[unit_id].blocks > 0):
            rows.append((unit_id, outcome_for(unit_id)))
            listed.add(unit_id)
    return rows


def _unit_row(unit_id: str, outcome: str, scan: _EventScan, limits: dict, log: tuple[str, Path] | None) -> dict:
    track = scan.units.get(unit_id) or _UnitTrack()
    row: dict[str, Any] = {
        "id": unit_id,
        "outcome": outcome,
        "attempts": track.blocks + (1 if outcome == "passed" else 0),
        "repairs": [
            {"attempt": rep["attempt"], "text": clip_head_tail(rep["text"], limits["repair_head_chars"], limits["repair_tail_chars"])}
            for rep in track.repairs
        ],
        "fix_window": [],
        "last_commands": [],
    }
    command_chars = limits["command_chars"]
    if outcome == "passed" and track.blocks > 0 and track.first_block_pos is not None:
        end = track.pass_pos if track.pass_pos is not None else scan.count
        window = [call for pos, call in scan.tool_calls if track.first_block_pos < pos < end]
        row["fix_window"] = [{"tool": c["tool"], "command": c["command"][:command_chars]} for c in window[-limits["fix_window_commands"]:]]
    if outcome == "failed":
        recent = [call for _pos, call in scan.tool_calls][-limits["last_commands"]:]
        row["last_commands"] = [{"tool": c["tool"], "command": c["command"][:command_chars]} for c in recent]
        if log is not None:
            label, real = log
            row["log_tail"] = {
                "path": label,
                "lines": read_log_tail(real, limits["log_tail_lines"], limits["log_line_chars"], limits["log_seek_bytes"]),
            }
    return row


def _memory_usage(memory_root: Path, run_id: str) -> list[dict]:
    """This run's inject rows, with their settle outcome folded in (usage/<run_id>.jsonl)."""
    rows, _bad = paths.read_jsonl(memory_root / "usage" / f"{run_id}.jsonl")
    entries: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        kind = row.get("kind")
        if kind == "inject":
            unit = row.get("unit")
            attempt = row.get("attempt")
            if not isinstance(unit, str):
                continue
            for entry in row.get("entries") or []:
                entry_id = entry.get("entry_id") if isinstance(entry, dict) else None
                if not isinstance(entry_id, str):
                    continue
                key = (entry_id, unit)
                if key not in entries:
                    entries[key] = {"entry_id": entry_id, "unit": unit, "attempt": attempt if isinstance(attempt, int) else None, "outcome": "open"}
                    order.append(key)
        elif kind == "settle":
            key = (row.get("entry_id"), row.get("unit"))
            outcome = row.get("outcome")
            if key in entries and isinstance(outcome, str):
                if outcome == "disputed":
                    entries[key]["outcome"] = "disputed"
                elif outcome.startswith("useful"):
                    entries[key]["outcome"] = "useful"
    return [entries[key] for key in order]


def _index_snapshot(memory_root: Path) -> list[dict]:
    index = _load_json_quiet(memory_root / "index.json")
    if not isinstance(index, dict) or index.get("schema") != "sure.memory.index.v1":
        return []
    out: list[dict] = []
    for entry in index.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") in ("superseded", "rejected") or entry.get("superseded_by"):
            continue
        out.append({
            "id": entry.get("entry_id"),
            "type": entry.get("type"),
            "status": entry.get("status"),
            "target_skill": entry.get("target_skill"),
            "component": entry.get("component"),
            "cause": entry.get("cause"),
            "trigger": [t for t in (entry.get("trigger") or []) if isinstance(t, str)],
            "useful": _int(entry.get("useful_activated")),
            "disputed": _int(entry.get("disputed")),
        })
    return out


def _declared_candidates(other_run_dir: Path) -> list[str]:
    """Candidate slugs a prior run declared (artifacts/extraction_declaration.json), `nn-` prefix dropped;
    falls back to the artifacts/candidates/ directory names."""
    declaration = _load_json_quiet(other_run_dir / "artifacts" / "extraction_declaration.json")
    ids: list[str] = []
    if isinstance(declaration, dict) and isinstance(declaration.get("candidates"), list):
        ids = [c for c in declaration["candidates"] if isinstance(c, str)]
    else:
        try:
            ids = sorted(p.name for p in (other_run_dir / "artifacts" / "candidates").iterdir() if p.is_dir())
        except OSError:
            ids = []
    return [_CANDIDATE_PREFIX_RE.sub("", c) for c in ids]


def _last_repair_from_events(other_run_dir: Path, header: str, seek_bytes: int) -> str | None:
    """Last tool_result_repair / finish_repair text of a finished prior run (run.json clears lastRepair
    on finish). Only the tail of events.jsonl is read."""
    last: str | None = None
    for line in _read_tail_text(other_run_dir / "events.jsonl", seek_bytes).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") not in ("tool_result_repair", "finish_repair"):
            continue
        text = _repair_of(event)
        if text is not None:
            last = text
    return strip_memory_block(last, header) if last else None


def _prior_run_summary(other: Path, record: dict, header: str, limits: dict) -> dict:
    status = record.get("status") if isinstance(record.get("status"), str) else "unknown"
    failed_unit = None
    if status != "success":
        failed_unit = _string_at(_load_json_quiet(other / "state.json"), "checkpoint", "data", "currentUnit") or None
    last_repair = record.get("lastRepair") or record.get("errorSummary")
    if not isinstance(last_repair, str) or not last_repair.strip():
        last_repair = _last_repair_from_events(other, header, limits["log_seek_bytes"])
    else:
        last_repair = strip_memory_block(last_repair, header)
    if last_repair:  # each prior run has its own output_dir; the caller's forms do not cover it
        last_repair = mask_output_dir(last_repair, output_dir_forms(record, record.get("args") if isinstance(record.get("args"), str) else ""))
    finished_at = record.get("finishedAt") or record.get("updatedAt")
    return {
        "run_id": other.name,
        "status": status,
        "failed_unit": failed_unit,
        "finished_at": finished_at if isinstance(finished_at, str) else None,
        "last_repair": last_repair[: limits["prior_run_repair_chars"]] if last_repair else None,
        "candidates": _declared_candidates(other),
    }


def _prior_runs(run_dir: Path, skill: str | None, target_id: str, header: str, limits: dict) -> list[dict]:
    """Same skill, same target, newest first (run ids sort chronologically), stop once we have enough."""
    if not skill or not target_id:
        return []
    root = Path(run_dir).parent
    try:
        names = sorted((p.name for p in root.iterdir() if p.is_dir() and p.name != Path(run_dir).name), reverse=True)
    except OSError:
        return []
    out: list[dict] = []
    for name in names:
        other = root / name
        record = _load_json_quiet(other / "run.json")
        if not isinstance(record, dict) or record.get("skillName") != skill:
            continue
        other_args = record.get("args") if isinstance(record.get("args"), str) else ""
        if resolve_target(other, skill, other_args)["id"] != target_id:
            continue
        out.append(_prior_run_summary(other, record, header, limits))
        if len(out) >= limits["prior_runs"]:
            break
    return out


def _build(run_dir: Path, repo_root: Path, *, cutoff: int | None, mark_passed: str | None, config: dict,
           units: dict, log_paths: dict, skill: str | None, finish_status: str | None) -> dict:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run dir is not a directory: {run_dir.name}")
    limits = config["digest_limits"]
    header = config["inject_header"]
    run_id = run_dir.name
    memory_root = paths.memory_root(Path(repo_root))

    record = _load_json_quiet(run_dir / "run.json")
    record = record if isinstance(record, dict) else {}
    events, effective_cutoff = _read_events(run_dir, cutoff)
    created = next((e["data"] for e in events if e.get("type") == "created" and isinstance(e.get("data"), dict)), {})
    skill = skill or _string_at(record, "skillName") or _string_at(created, "skillName") or None
    raw_args = record.get("args") if isinstance(record.get("args"), str) else created.get("args")
    raw_args = raw_args if isinstance(raw_args, str) else ""
    registry_all = {name: list(ids) for name, ids in (units.get("skills") or {}).items() if isinstance(ids, list)}
    registry = registry_all.get(skill or "", [])

    scan = _EventScan(header)
    scan.run(events)
    record_status = record.get("status") if isinstance(record.get("status"), str) else None
    status = finish_status or record_status or scan.status or "unknown"
    terminal = scan.terminal or bool(finish_status) or (record_status in NON_SUCCESS_TERMINAL)

    product_dir = _product_dir(run_dir, skill)
    target = resolve_target(run_dir, skill, raw_args)
    unit_rows = []
    for unit_id, outcome in _outcome_rows(scan, registry, mark_passed, terminal):
        log = _find_log(run_dir, skill, unit_id, log_paths, product_dir) if outcome == "failed" else None
        unit_rows.append(_unit_row(unit_id, outcome, scan, limits, log))

    # last: repairs, log tails and tool commands are all free text out of the run, and trim_to_budget
    # then measures the shortened strings rather than the paths they replaced.
    return mask_output_dir({
        "schema": SCHEMA,
        "run": {
            "run_id": run_id,
            "skill": skill,
            "args": strip_output_dir(raw_args),
            "target": target,
            "status_so_far": status,
            "cutoff": effective_cutoff,
            "memory_usage": _memory_usage(memory_root, run_id),
        },
        "units": unit_rows,
        "tool_errors": scan.tool_errors,
        "prior_runs": _prior_runs(run_dir, skill, target["id"], header, limits),
        "memory_index_snapshot": _index_snapshot(memory_root),
        "units_registry": registry_all,
    }, output_dir_forms(record, raw_args))


def build_run_digest(run_dir: Path, repo_root: Path, *, cutoff: int | None, mark_passed: str | None,
                     config: dict, units: dict, log_paths: dict, skill: str | None = None,
                     finish_status: str | None = None) -> dict:
    """The digest, trimmed to config.digest_max_bytes; {"schema", "error"} when it cannot be built.
    The trim runs inside the same guard: a config missing digest_max_bytes would otherwise raise
    past main() as a traceback, and the hook copies whatever reaches stderr into the error field."""
    try:
        digest = _build(run_dir, repo_root, cutoff=cutoff, mark_passed=mark_passed, config=config, units=units,
                        log_paths=log_paths, skill=skill, finish_status=finish_status)
        return trim_to_budget(digest, config)
    except Exception as exc:  # noqa: BLE001 - the digest must never take the hook down with it
        return {"schema": SCHEMA, "error": f"{type(exc).__name__}: {exc}"}


# --- budget ---------------------------------------------------------------------

def _digest_bytes(digest: dict) -> int:
    return len(json.dumps(digest, indent=2, ensure_ascii=False).encode("utf-8")) + 1  # + trailing newline


def _snapshot_drop_order(rows: list[dict]) -> list[int]:
    """Snapshot row indexes in the order they may be dropped. index.render_index_md answers the same
    question for the same rows when index.md runs out of budget - drop the oldest provisional lines,
    never drop confirmed - so the snapshot answers it the same way: an unrecognised status first,
    then provisional, then disputed, then confirmed last. index.json holds each status run in
    index.ordered_entries order and provisional runs newest first, so the tail of a run is its
    oldest row and dropping from the tail drops the oldest first."""
    return sorted(range(len(rows)), key=lambda i: (_SNAPSHOT_DROP_RANK.get(str(rows[i].get("status")), 0), -i))


def _omitted_row(count: int) -> dict:
    """The marker that goes in place of the dropped rows, shaped like index.md's notice line."""
    return {
        "omitted": count,
        "note": (f"omitted {count} memory index entries to fit digest_max_bytes; run "
                 "`python3 -s sure/runtime/memory/cli.py list` to see them"),
    }


def _fit_index_snapshot(digest: dict, budget: int) -> None:
    """Drop snapshot rows until the digest fits, and record in the list itself how many went.

    The snapshot is the only part of the digest sized by the library instead of by the run, so it
    is the only part that can carry a quiet run past the budget: index_snapshot_min shrinks a row
    to about 184 bytes but the row count is whatever the index holds. This is also the only step
    that removes rather than shortens, which is why the count is not optional - a list that is
    silently short reads like a small library, the same silent lie as the overrun.

    Binary search on how many rows go: the size falls monotonically as rows leave (the marker grows
    by at most a digit), and the snapshot is as long as the library, so one probe per row does not
    scale. Dropping every row may still not fit; that is the same accepted overflow as the rest of
    the ladder, and the marker then says the whole snapshot went."""
    rows = list(digest.get("memory_index_snapshot") or [])
    if not rows or _digest_bytes(digest) <= budget:
        return
    order = _snapshot_drop_order(rows)

    def drop(count: int) -> None:
        gone = set(order[:count])
        digest["memory_index_snapshot"] = [r for i, r in enumerate(rows) if i not in gone] + [_omitted_row(count)]

    low, high = 1, len(rows)
    while low < high:
        mid = (low + high) // 2
        drop(mid)
        if _digest_bytes(digest) <= budget:
            high = mid
        else:
            low = mid + 1
    drop(low)


def _trim_step(digest: dict, step: str, config: dict) -> None:
    limits = config["digest_limits"]
    if step == "index_snapshot_min":
        digest["memory_index_snapshot"] = [
            # a marker row is not an entry: minimising it would turn the omission count into a row
            # of five nulls that reads like an entry the index lost.
            e if "omitted" in e else
            {"id": e.get("id"), "status": e.get("status"), "target_skill": e.get("target_skill"), "component": e.get("component"), "cause": e.get("cause")}
            for e in digest.get("memory_index_snapshot") or []
        ]
    elif step == SNAPSHOT_FIT_STEP:
        _fit_index_snapshot(digest, int(config["digest_max_bytes"]))
    elif step == "registry_current_only":
        skill = (digest.get("run") or {}).get("skill")
        registry = digest.get("units_registry") or {}
        digest["units_registry"] = {skill: registry[skill]} if skill in registry else {}
    elif step == "prior_runs_2":
        kept = []
        for row in (digest.get("prior_runs") or [])[:2]:
            row = dict(row)
            row.pop("candidates", None)
            kept.append(row)
        digest["prior_runs"] = kept
    elif step == "log_tail_10":
        for unit in digest.get("units") or []:
            tail = unit.get("log_tail")
            if isinstance(tail, dict) and isinstance(tail.get("lines"), list):
                tail["lines"] = tail["lines"][-10:]
    elif step == "repairs_300":
        head, tail = limits["repair_head_chars"] // 2, limits["repair_tail_chars"] // 2
        for unit in digest.get("units") or []:
            unit["repairs"] = [{"attempt": r.get("attempt"), "text": reclip_head_tail(str(r.get("text") or ""), head, tail)} for r in unit.get("repairs") or []]
    elif step == "fix_window_5":
        for unit in digest.get("units") or []:
            unit["fix_window"] = (unit.get("fix_window") or [])[-5:]


def _trim_order(config: dict) -> list[str]:
    """config.digest_trim_order with the snapshot fit step in it. config.json is hand-tuned and no
    upgrade rewrites it (spec 8.2), so a deployed file lists the ladder as it stood before this step
    existed; reading that list literally would leave the overrun exactly where it was found. The
    step finishes what index_snapshot_min starts - the ladder already spends the snapshot before it
    spends anything of this run's - so it goes directly after it."""
    order = [s for s in (config.get("digest_trim_order") or []) if isinstance(s, str)]
    if SNAPSHOT_FIT_STEP in order:
        return order
    at = order.index(_SNAPSHOT_FIT_AFTER) + 1 if _SNAPSHOT_FIT_AFTER in order else len(order)
    return order[:at] + [SNAPSHOT_FIT_STEP] + order[at:]


def trim_to_budget(digest: dict, config: dict) -> dict:
    """Apply config.digest_trim_order step by step until the serialized digest fits digest_max_bytes.
    Repairs and fix_window are shrunk but never removed; index snapshot rows are the one thing that
    is dropped, and always against a count; overflow after the last step is accepted."""
    if "error" in digest and "run" not in digest:
        return digest
    budget = int(config["digest_max_bytes"])
    if _digest_bytes(digest) <= budget:
        return digest
    trimmed = json.loads(json.dumps(digest))  # deep copy; the caller's dict is left alone
    for step in _trim_order(config):
        _trim_step(trimmed, step, config)
        if _digest_bytes(trimmed) <= budget:
            break
    return trimmed


# --- cli ------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="build_run_digest.py",
        description="Write artifacts/run_digest.json for the current run (hook use; agents only preview with --out).",
    )
    parser.add_argument("--run-dir", required=True, help=".sure/runs/<run_id>")
    parser.add_argument("--repo-root", required=True, help="checkout root; sure/memory/ lives under it")
    parser.add_argument("--cutoff", type=int, default=None, help="events.jsonl line count to read up to (newline-terminated lines)")
    parser.add_argument("--mark-passed", default=None, help="unit id whose advance is not in events yet")
    parser.add_argument("--out", default=None, help="write here instead (relative paths resolve against --run-dir)")
    parser.add_argument("--skill", default=None, help="skill name (hooks always pass it; wins over run.json / events)")
    parser.add_argument("--finish-status", default=None, help="pre_finish only: the status the agent is finishing with")
    # stdout: exactly one JSON line {"path", "sha256", "bytes", "error"}; exit 0 = digest written,
    # 1 = an error digest {schema, error} was written, 2 = nothing written (reason on stderr). Skeleton §1.13.
    ns = parser.parse_args(argv)
    run_dir = Path(ns.run_dir)
    try:
        config, units, log_paths = paths.load_config(), paths.load_units(), paths.load_log_paths()
    except (OSError, ValueError) as exc:
        print(f"build_run_digest: cannot load memory config: {exc}", file=sys.stderr)
        return 2
    digest = build_run_digest(run_dir, Path(ns.repo_root), cutoff=ns.cutoff, mark_passed=ns.mark_passed, config=config,
                              units=units, log_paths=log_paths, skill=ns.skill, finish_status=ns.finish_status)
    out = Path(ns.out) if ns.out else Path("artifacts") / "run_digest.json"
    if not out.is_absolute():
        out = run_dir / out
    try:
        paths.atomic_write_json(out, digest)
    except OSError as exc:
        print(f"build_run_digest: cannot write {out}: {exc}", file=sys.stderr)
        return 2
    try:  # the file is written; failing to read it back is not worth a traceback the hook would store
        sha256, size = paths.sha256_file(out), out.stat().st_size
    except OSError:
        sha256, size = None, None
    print(json.dumps({"path": str(out), "sha256": sha256, "bytes": size, "error": digest.get("error")}))
    return 1 if "error" in digest else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
