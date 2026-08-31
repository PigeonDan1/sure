#!/usr/bin/env python3
"""Mechanical gate for the extract_lessons unit (memory design spec 5.3), part A.

This module holds:
- the one trigger predicate shared with match.ts (trigger_hits);
- body parsers for the two entry types (parse_bad_case, parse_fact) plus the
  field hygiene helpers they need (interpolation_problem, count_body_words,
  provenance_line_in_body);
- evidence path helpers with a single resolution entry point (evidence_bases /
  resolve_evidence / evidence_problem);
- trigger discipline (rule 4), schema / enum / body checks (rule 1), infra
  isolation (rule 5), causal evidence (rule 6) and the "digest could not be
  built" guard from spec 4.2 (reported as rule 10);
- the RULES registry, build_context and check_extraction.

Part B (a later commit) appends evidence resolution against the target dir
(rule 2), claims (3), dedup (7), target_entry (8), source binding (9), the
declaration consistency checks (10) and main().

Rules are plain functions GateContext -> list[GateFailure]. check_extraction
runs every registered rule and returns every failure at once, so the agent can
fix everything in one attempt instead of discovering problems one by one.

interpolation_problem, count_body_words, provenance_line_in_body,
parse_evidence_ref, is_unsafe_evidence_path and is_single_name are adapted from
the old branch's scripts/check_memory_proposals.py (same repository, no
third-party license). Only the standard library is used so the gate also runs
under the cluster's system `python3 -s`.
"""
from __future__ import annotations

import argparse
import codecs
import difflib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path, PurePath
from typing import Callable, Iterable

try:
    from . import digest as run_digest, paths
except ImportError:  # executed as a script: python sure/runtime/memory/proposals.py (same shape as digest.py)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from memory import digest as run_digest, paths  # type: ignore[no-redef]

# --- constants (spec 4.4 / 5.1 / 5.2 / 5.3) ------------------------------------------

PROPOSAL_SCHEMA = "sure.memory.proposal.v2"
DECLARATION_SCHEMA = "sure.memory.extraction.v2"
DIGEST_SCHEMA = "sure.memory.run_digest.v1"

DECLARATION_REQUIRED = (
    "schema", "no_new_lessons", "no_lessons_reason", "covered_by", "candidates", "infra_noise", "infra_evidence",
)
PROPOSAL_REQUIRED = (
    "schema", "type", "op", "target_skill", "target_entry", "applies_to", "cell", "trigger",
    "causal", "evidence", "claims", "source", "similar", "scope", "checked_at",
)
ENTRY_TYPES = ("bad_case", "fact")
OPS = ("add", "modify", "supersede")
CLAIM_KINDS = ("unit_result", "gate_repair")
FACT_COMPONENT = "_"
FACT_CAUSE = "n.a."

# Lines the publish step generates as the entry header; a body line spelled this
# way would be read back as routing, so the gate refuses it (spec 5.1).
PROVENANCE_PREFIXES = ("Trigger:", "Cell:", "Source:", "Added:", "Status:", "Superseded-by:")
BAD_CASE_REQUIRED_SECTIONS = ("Trigger", "Affected Step", "Minimum Evidence", "Known Mitigation", "Verification")
BAD_CASE_OPTIONAL_SECTIONS = ("Example Artifacts",)
FACT_HEADER_KEYS = ("Scope", "Checked-at", "Evidence")
GATE_NAME = "check_memory_extraction gate"
# One read() per evidence chunk. Not a config key: it is a buffer size, not a limit anyone tunes,
# and it only decides how often the streaming readers loop (paths.sha256_file picks its own the
# same way). config.json's evidence_max_bytes is the limit that matters.
EVIDENCE_CHUNK_BYTES = 1 << 18

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A date, optionally followed by a time: run-specific text, never a reusable trigger.
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
_HEX_RE = re.compile(r"[0-9a-f]+")


# --- data types ------------------------------------------------------------------------

@dataclass
class GateFailure:
    rule: int
    message: str


@dataclass
class ParsedBody:
    """Result of parse_bad_case / parse_fact. Never raises: problems land in .errors."""
    title: str
    sections: dict[str, str]
    word_count: int
    errors: list[str]


@dataclass
class Candidate:
    cid: str
    dir: Path
    proposal: dict
    md: str
    body: ParsedBody | None  # None when proposal.type is not a known entry type


@dataclass
class GateContext:
    """Everything the rules read. Built once per gate run by build_context."""
    run_dir: Path
    repo_root: Path
    config: dict
    skills: dict                     # units.json["skills"]: skill -> [unit ids]
    index: dict | None               # sure/memory/index.json (rules 7 / 8, part B)
    checkpoint_digest_sha: str | None  # checkpoint.memory.digestSha256 (rule 9, part B)
    declaration: dict
    digest: dict | None
    trigger_texts: list[str]         # this run's gate repair texts (unclipped when events.jsonl still has them) + log tails + prior runs' gate repairs, for rule 4
    candidates: list[Candidate]
    load_failures: list[GateFailure]

    @property
    def run_id(self) -> str:
        # Skeleton 1.13: the run id is the run directory name, nothing else.
        return self.run_dir.name

    @property
    def target_id(self) -> str:
        run = self.digest.get("run") if isinstance(self.digest, dict) else None
        target = run.get("target") if isinstance(run, dict) else None
        value = target.get("id") if isinstance(target, dict) else None
        return value if isinstance(value, str) else ""

    @property
    def run_skill(self) -> str | None:
        """The skill the digest says this run is; None when there is no usable digest."""
        run = self.digest.get("run") if isinstance(self.digest, dict) else None
        value = run.get("skill") if isinstance(run, dict) else None
        return value if isinstance(value, str) and value else None

    @property
    def digest_error(self) -> str | None:
        """Non-None when there is no usable digest: the hook wrote {schema, error} (spec 4.2) or nothing."""
        if not isinstance(self.digest, dict):
            return "artifacts/run_digest.json is missing or unreadable"
        error = self.digest.get("error")
        return error if isinstance(error, str) else None


RuleFn = Callable[[GateContext], list[GateFailure]]


# --- the trigger predicate (spec 7.2) --------------------------------------------------

def trigger_hits(trigger: str, text: str) -> bool:
    """The only trigger predicate in the whole system; match.ts triggerHits is the same line.
    Case-insensitive literal substring. No whitespace folding, no normalisation, no regex.
    fixtures/match_vectors.json pins the behaviour for both languages."""
    needle = trigger.strip().lower()
    return bool(needle) and needle in text.lower()


def observed_in(trigger: str, texts: list[str]) -> bool:
    return any(trigger_hits(trigger, text) for text in texts)


# --- field hygiene ---------------------------------------------------------------------

def interpolation_problem(value: str) -> str | None:
    """Why value cannot go into a header line or README route-table cell, or None.
    Adapted from the old branch (allow-list on isprintable so NEL / U+2028 / U+2029,
    which str.splitlines() also breaks on, are refused too); ';' added because the
    Trigger: header joins triggers with '; '."""
    for ch in value:
        if ch == "|":
            return "a '|', which would re-split the README route-table row"
        if ch == ";":
            return "a ';', which is the separator of the Trigger: header line"
        if not ch.isprintable():
            return (
                f"the non-printable character {ch!r} (U+{ord(ch):04X}), which could inject "
                "route-table rows or forge Trigger:/Cell:/Source: header lines"
            )
    return None


def count_body_words(md_text: str) -> int:
    """Body word count: fenced code (toggled by ``` lines), heading lines and provenance
    header lines are excluded, then len(text.split()). Adapted from the old branch."""
    in_fence = False
    kept: list[str] = []
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(PROVENANCE_PREFIXES):
            continue
        kept.append(line)
    return len("\n".join(kept).split())


def provenance_line_in_body(md_text: str) -> str | None:
    """First body line that would be read back as a provenance header, or None.
    Fenced code is exempt. Adapted from the old branch."""
    in_fence = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith(PROVENANCE_PREFIXES):
            return stripped
    return None


# --- body parsers (spec 5.1 / 5.2) -----------------------------------------------------

def _is_fence(stripped: str) -> bool:
    return stripped.startswith("```")


def _h1_errors(seen_h1: bool, title: str) -> list[str]:
    if not seen_h1:
        return ["missing H1 title ('# <title>')"]
    if not title:
        return ["H1 title is empty"]
    if not title.isprintable():
        return ["H1 title contains non-printable characters"]
    return []


def parse_bad_case(md: str) -> ParsedBody:
    """Six-section bad_case body: H1, then '## <name>' sections from the fixed list.
    Content inside fences is neither a heading nor a provenance line."""
    body = ParsedBody(title="", sections={}, word_count=0, errors=[])
    allowed = BAD_CASE_REQUIRED_SECTIONS + BAD_CASE_OPTIONAL_SECTIONS
    # One list per section joined once at the end, like parse_fact's notes: `sections[k] += line`
    # inside the loop is quadratic (a dict value never gets CPython's in-place concat), which a
    # candidate carrying a pasted log turns into a gate timeout.
    lines: dict[str, list[str]] = {}
    in_fence = False
    seen_h1 = False
    preamble_reported = False
    orphan_reported = False
    current: str | None = None

    def orphan(stripped: str) -> str | None:
        # A line between the H1 and the first '## ' section belongs to no section, so no section
        # check ever sees it -- yet it ships verbatim in the published entry, and index.py reads
        # a body carrying an orphan 'Scope:' line back as a fact. Report it once.
        if stripped and seen_h1 and current is None and not orphan_reported:
            return "text between the H1 title and the first section; every body line must live inside a '## <name>' section"
        return None

    for raw in md.splitlines():
        stripped = raw.strip()
        if _is_fence(stripped):
            in_fence = not in_fence
        if in_fence or _is_fence(stripped):
            if current is not None:
                lines[current].append(raw)
            elif (problem := orphan(stripped)) is not None:
                body.errors.append(problem)
                orphan_reported = True
            continue
        if stripped.startswith("# "):
            if seen_h1:
                body.errors.append("more than one H1 title")
            else:
                seen_h1 = True
                body.title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            name = stripped[3:].strip()
            if name not in allowed:
                body.errors.append(f"unknown section '## {name}' (allowed: {', '.join(allowed)})")
            elif name in lines:
                body.errors.append(f"duplicate section '## {name}'")
            lines.setdefault(name, [])
            current = name
            continue
        if not seen_h1:
            if stripped and not preamble_reported:
                body.errors.append("the file must start with an H1 title ('# <title>')")
                preamble_reported = True
            continue
        if current is not None:
            lines[current].append(raw)
        elif (problem := orphan(stripped)) is not None:
            body.errors.append(problem)
            orphan_reported = True
    body.sections = {name: "\n".join(text).strip("\n") for name, text in lines.items()}
    body.errors.extend(_h1_errors(seen_h1, body.title))
    for name in BAD_CASE_REQUIRED_SECTIONS:
        if name not in body.sections:
            body.errors.append(f"missing section '## {name}'")
        elif not body.sections[name].strip():
            body.errors.append(f"section '## {name}' is empty")
    forged = provenance_line_in_body(md)
    if forged is not None:
        body.errors.append(
            f"line {forged!r} starts with a provenance prefix {PROVENANCE_PREFIXES}; "
            "the publish step writes those header lines itself"
        )
    body.word_count = count_body_words(md)
    return body


def parse_fact(md: str) -> ParsedBody:
    """Fact body: H1 (one sentence), then 'Scope:' / 'Checked-at:' / 'Evidence:' lines,
    then optional notes (spec 5.2). sections holds the three values plus 'Notes'."""
    body = ParsedBody(title="", sections={}, word_count=0, errors=[])
    in_fence = False
    seen_h1 = False
    preamble_reported = False
    notes: list[str] = []
    for raw in md.splitlines():
        stripped = raw.strip()
        if _is_fence(stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("# "):
            if seen_h1:
                body.errors.append("more than one H1 title")
            else:
                seen_h1 = True
                body.title = stripped[2:].strip()
            continue
        if not seen_h1:
            if stripped and not preamble_reported:
                body.errors.append("the file must start with an H1 title ('# <one sentence>')")
                preamble_reported = True
            continue
        key = next((k for k in FACT_HEADER_KEYS if stripped.startswith(k + ":")), None)
        if key is not None:
            value = stripped[len(key) + 1:].strip()
            if key in body.sections:
                body.errors.append(f"duplicate '{key}:' line")
            else:
                body.sections[key] = value
            continue
        notes.append(raw)
    body.errors.extend(_h1_errors(seen_h1, body.title))
    for key in FACT_HEADER_KEYS:
        if key not in body.sections:
            body.errors.append(f"missing '{key}:' line")
        elif not body.sections[key]:
            body.errors.append(f"'{key}:' line is empty")
    body.sections["Notes"] = "\n".join(notes).strip()
    body.word_count = count_body_words(body.sections["Notes"])
    forged = provenance_line_in_body(md)
    if forged is not None:
        body.errors.append(
            f"line {forged!r} starts with a provenance prefix {PROVENANCE_PREFIXES}; "
            "the publish step writes those header lines itself"
        )
    return body


# --- evidence helpers --------------------------------------------------------------------

def parse_evidence_ref(entry: str) -> tuple[str, int | None]:
    """Split 'path:line' into (path, line). Only a trailing ':<positive int>' is a line
    reference; 'C:\\x' keeps its drive. Adapted from the old branch."""
    idx = entry.rfind(":")
    if idx == -1:
        return entry, None
    suffix = entry[idx + 1:]
    if suffix.isdigit() and int(suffix) > 0:
        return entry[:idx], int(suffix)
    return entry, None


def is_unsafe_evidence_path(rel: str) -> bool:
    """Absolute, drive-relative, separator-led or '..'-carrying: any of these would let
    pathlib's '/' drop or escape the base it is joined onto. Adapted from the old branch.
    PurePath follows the host platform, so on Linux 'C:\\x' has no drive and 'a\\..\\b' has
    no '..' part; the windows-shaped forms are therefore also matched textually, and the
    gate judges a candidate the same way on the dev box and on the cluster."""
    p = PurePath(rel)
    return (
        bool(p.drive)
        or p.is_absolute()
        or rel.startswith(("/", "\\"))
        or ".." in p.parts
        or re.match(r"^[A-Za-z]:", rel) is not None
        or ".." in rel.replace("\\", "/").split("/")
    )


def is_single_name(name) -> bool:
    """True when name is usable as exactly one directory name (candidate ids are joined
    onto artifacts/candidates/). Adapted from the old branch. Both separators and a drive
    prefix are checked textually for the same reason as above: PurePosixPath reads 'a\\b'
    and 'C:' as one plain name."""
    return (
        isinstance(name, str)
        and bool(name)
        and name not in (".", "..")
        and "/" not in name
        and "\\" not in name
        and re.match(r"^[A-Za-z]:", name) is None
        and not PurePath(name).drive
        and len(PurePath(name).parts) == 1
        and name == name.rstrip(" .")
    )


def evidence_bases(ctx: GateContext) -> list[tuple[Path, str]]:
    """Ordered (base, containment) pairs an evidence path is tried against (spec 5.3 rule 2).
    (a) the run root with resolve-based containment: artifacts/, vc_logs/, local_logs/ all live under
        it, and a link planted inside the run dir must not turn a file elsewhere into evidence;
    (b) the target directories with lexical containment only: cluster model dirs link weights/ and
        checkpoints/ out to NFS, and resolving would reject real files there."""
    bases: list[tuple[Path, str]] = [(ctx.run_dir, "resolve")]
    bases.extend((base, "lexical") for base in product_dirs_for(ctx.run_dir, ctx.repo_root))
    return bases


def resolve_evidence(ctx: GateContext, rel: str) -> Path | None:
    """First base under which rel names an existing regular file, or None."""
    if not isinstance(rel, str) or not rel.strip() or is_unsafe_evidence_path(rel):
        return None
    for base, mode in evidence_bases(ctx):
        try:
            candidate = base / rel
            if mode == "resolve":
                root = base.resolve()
                candidate = candidate.resolve()
                if not candidate.is_relative_to(root):
                    continue
            if candidate.is_file():
                return candidate
        except (OSError, ValueError, RuntimeError):
            continue
    return None


def evidence_line_count(path: Path, stop_at: int, max_bytes: int, chunk_bytes: int = EVIDENCE_CHUNK_BYTES) -> int | None:
    """Lines in `path`, counted one chunk at a time so the file is never held in memory (the eval
    log the spec tells the agent to cite is a multi-GB job log). Stops as soon as `stop_at` lines
    have been seen, so citing line 212 costs one read(). None when `max_bytes` was reached first:
    the count is then unknown and the caller must not call the reference out of range.
    Raises OSError when the file cannot be read.

    A line ends at \\r\\n, \\r or \\n -- the same three the digest splits on
    (digest._LINE_SPLIT_RE, used by read_log_tail). Keep the two in step: a job log is mostly
    progress bars redrawing with a bare \\r, and counting only \\n reads a 302-line shard log as 3,
    which rejects path:line citations the digest is happy to display. The one deliberate
    difference: read_log_tail drops every trailing blank line because it is showing a tail, while
    numbering a citation keeps them and only the final terminator ends the last line."""
    lines = 0
    read = 0
    tail_break = True   # nothing read yet: an empty file is 0 lines, not 1
    pending_cr = False  # the block ended on \r; only the next byte says whether that was \r\n
    with Path(path).open("rb") as handle:
        while read < max_bytes:
            block = handle.read(min(chunk_bytes, max_bytes - read))
            if not block:
                lines += 1 if pending_cr else 0  # a trailing \r is a break of its own
                return lines if tail_break else lines + 1
            read += len(block)
            tail_break = block[-1:] in (b"\n", b"\r")
            if pending_cr:
                block = b"\r" + block  # re-join, so a split \r\n is counted once
                pending_cr = False
            if block.endswith(b"\r"):
                pending_cr = True
                block = block[:-1]
            lines += block.count(b"\n") + block.count(b"\r") - block.count(b"\r\n")
            if lines >= stop_at:
                return lines
    return None


def evidence_triggers_found(path: Path, triggers: list[str], max_bytes: int,
                            chunk_bytes: int = EVIDENCE_CHUNK_BYTES) -> tuple[set[str], bool]:
    """Which of `triggers` appear in the first `max_bytes` of `path`, using the same case-insensitive
    literal test as trigger_hits. The file is streamed in overlapping windows -- each window carries
    the last (longest trigger - 1) characters of the previous one -- so a trigger lying across a
    chunk boundary is still found. Raises OSError when the file cannot be read.

    Returns (found, truncated). truncated is True when the scan ran out of budget with the file
    unfinished: a trigger missing from `found` was then not searched for to the end and its absence
    is unknown, not established. Same contract as evidence_line_count returning None -- neither
    reader answers for bytes it did not read."""
    needles = {t: t.strip().lower() for t in triggers}
    needles = {t: n for t, n in needles.items() if n}
    if not needles:
        return set(), False
    overlap = max(len(n) for n in needles.values()) - 1
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    found: set[str] = set()
    carry = ""
    read = 0
    truncated = True  # cleared on end of file, or once every trigger has been found
    with Path(path).open("rb") as handle:
        while read < max_bytes:
            block = handle.read(min(chunk_bytes, max_bytes - read))
            if not block:
                truncated = False
                break
            read += len(block)
            window = carry + decoder.decode(block)
            haystack = window.lower()
            for trigger, needle in needles.items():
                if trigger not in found and needle in haystack:
                    found.add(trigger)
            if len(found) == len(needles):
                truncated = False
                break
            carry = window[-overlap:] if overlap > 0 else ""
    return found, truncated


@dataclass
class EvidenceTriggerScan:
    """What reading a candidate's evidence files settled about its triggers.

    Three outcomes, not two. `missing` is an answer: every readable evidence file was read to the
    end and the trigger was not in any of them. `unverified` is the absence of an answer: a file
    hit evidence_max_bytes (32 MiB) first, so the trigger may well be in the bytes nobody looked
    at -- this run's job log is 158 MB against that budget. The gate stays silent about those, and
    it is right to: refusing a real lesson over bytes it declined to read would be worse. But that
    silence is what makes them worth naming. A trigger cited against a file too big to read is
    UNVERIFIED, not verified, and a human confirming the entry cannot tell the two apart unless
    something says so."""
    found: list[str]
    missing: list[str]
    unverified: list[str]
    readable: int  # evidence files that resolved and opened


def scan_evidence_triggers(files: list[Path], triggers: list[str], max_bytes: int) -> EvidenceTriggerScan:
    """Read `files` (streamed, never materialised) until every trigger is found or the files run
    out, and sort the triggers into found / missing / unverified."""
    found: set[str] = set()
    readable = 0
    truncated = False
    for path in files:
        try:
            hits, hit_cap = evidence_triggers_found(path, [t for t in triggers if t not in found], max_bytes)
        except OSError:
            continue
        readable += 1
        found |= hits
        truncated = truncated or hit_cap
    unseen = [t for t in triggers if t not in found]
    return EvidenceTriggerScan(
        found=[t for t in triggers if t in found],
        missing=[] if truncated else unseen,
        unverified=unseen if truncated else [],
        readable=readable,
    )


def _resolved_evidence_files(ctx: GateContext, evidence) -> list[Path]:
    """The evidence refs of one candidate that name an existing file, in declaration order."""
    out: list[Path] = []
    for ref in evidence if isinstance(evidence, list) else []:
        if not isinstance(ref, str):
            continue
        resolved = resolve_evidence(ctx, parse_evidence_ref(ref)[0])
        if resolved is not None:
            out.append(resolved)
    return out


def evidence_problem(ctx: GateContext, ref) -> str | None:
    """Why an evidence reference (path or path:line) is unusable, or None."""
    if not isinstance(ref, str) or not ref.strip():
        return "evidence entry must be a non-empty string"
    path_part, line = parse_evidence_ref(ref)
    if is_unsafe_evidence_path(path_part):
        return f"{ref!r} is an absolute path, a drive path or contains '..'; use a path relative to the run directory"
    resolved = resolve_evidence(ctx, path_part)
    if resolved is None:
        return f"{ref!r} does not resolve to an existing file"
    if line is not None:
        try:
            total = evidence_line_count(resolved, line, int(ctx.config["evidence_max_bytes"]))
        except OSError:
            return f"{ref!r} could not be read"
        if total is not None and total < line:
            return f"{ref!r} line {line} is beyond the end of the file ({total} lines)"
    return None


# --- trigger discipline (rule 4) -----------------------------------------------------------

def strip_template_phrases(trigger: str, phrases: list[str]) -> str:
    """Remove every occurrence of every harness template phrase (case-insensitive)."""
    rest = trigger
    for phrase in sorted((p for p in phrases if p), key=len, reverse=True):
        rest = re.sub(re.escape(phrase), "", rest, flags=re.IGNORECASE)
    return rest


def trigger_problem(trigger: str, config: dict) -> str | None:
    """Per-trigger discipline that does not need the digest: length, stop word, template
    text, regex prefix. Returns a message fragment starting with 'trigger ...' or None."""
    minimum = int(config["trigger_min_chars"])
    core = trigger.strip()
    if core.lower().startswith("re:"):
        return f"trigger {trigger!r} starts with 're:'; regular expressions are not supported, triggers are literal substrings"
    if len(core) < minimum:
        return f"trigger {trigger!r} is shorter than {minimum} characters"
    if core.lower() in {w.strip().lower() for w in config["trigger_stopwords"]}:
        return f"trigger {trigger!r} is a stop word; use the specific error string instead"
    remaining = re.sub(r"\s+", "", strip_template_phrases(core, list(config["trigger_template_phrases"])))
    if len(remaining) < minimum:
        return (
            f"trigger {trigger!r} is only harness template text (fewer than {minimum} characters remain "
            "after removing the fixed gate phrases); quote the underlying error instead"
        )
    return None


def is_generic_trigger(trigger: str, *, run_id: str, target_id: str, exclude: Iterable[str] = ()) -> bool:
    """A trigger that could appear again in another run: no run id, no target id, none of the
    `exclude` strings (the callers pass the prior run ids a trigger may have been copied out of),
    no .sure/runs/ path, not a bare number or hash once punctuation is removed, no ISO date."""
    t = trigger.strip().lower()
    if not t or ".sure/runs/" in t:
        return False
    if run_id and run_id.lower() in t:
        return False
    if target_id and target_id.lower() in t:
        return False
    if any(other and other.lower() in t for other in exclude):
        return False
    if _ISO_TS_RE.search(t):
        return False
    core = re.sub(r"[\W_]+", "", t)
    if not core or core.isdigit() or _HEX_RE.fullmatch(core):
        return False
    return True


def _digest_unit_texts(digest: dict | None, *, repairs: bool, log_tail: bool) -> list[str]:
    """The requested kinds of unit text in the digest, in document order."""
    texts: list[str] = []
    units = digest.get("units") if isinstance(digest, dict) else None
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        if repairs:
            for repair in unit.get("repairs") or []:
                if isinstance(repair, dict) and isinstance(repair.get("text"), str):
                    texts.append(repair["text"])
        if log_tail:
            tail = unit.get("log_tail")
            lines = tail.get("lines") if isinstance(tail, dict) else None
            for line in lines if isinstance(lines, list) else []:
                if isinstance(line, str):
                    texts.append(line)
    return texts


def digest_texts(digest: dict | None) -> list[str]:
    """Every gate repair text and log tail line in the digest, in document order."""
    return _digest_unit_texts(digest, repairs=True, log_tail=True)


def _prior_run_rows(digest: dict | None) -> list[dict]:
    rows = digest.get("prior_runs") if isinstance(digest, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def prior_run_ids(digest: dict | None) -> list[str]:
    """The run ids of the digest's prior_runs, for is_generic_trigger's exclude list."""
    return [row["run_id"] for row in _prior_run_rows(digest) if isinstance(row.get("run_id"), str)]


def prior_run_texts(digest: dict | None) -> list[str]:
    """The last_repair of each prior run a GATE wrote (last_repair_source "gate"), in digest order.

    A unit that runs after extract_lessons -- sure_eval's run_report, sure_onboard's
    finalize_model_bundle -- can only be blocked after this run's digest already exists, so its gate
    text never reaches units[] and no second extraction window opens. The next run of the same skill
    on the same target carries it in prior_runs[].last_repair, and that is the only place it is.

    Source "agent" is skipped: that text is run.json errorSummary, the previous agent's own sentence
    from sure_finish, and rule 4 asks for something a gate said, not something an agent wrote."""
    return [row["last_repair"] for row in _prior_run_rows(digest)
            if row.get("last_repair_source") == "gate"
            and isinstance(row.get("last_repair"), str) and row["last_repair"].strip()]


def repair_texts_from_events(run_dir: Path, digest: dict | None, config: dict) -> list[str] | None:
    """This run's gate repair texts at full length, re-read from events.jsonl, or None.

    digest.py clips every units[].repairs[].text to repair_head_chars + repair_tail_chars before
    run_digest.json is written, and the repairs_300 trim step halves that again, so what a reader
    gets back from the digest has an elided middle. That clip is there to bound the prompt the
    digest becomes. A trigger check is not a prompt: an agent that quoted a repair verbatim must
    not be told it "never mentioned it" because the quote landed in the part the clip dropped.

    The unclipped string is not in this process -- the clip runs inside build_run_digest, before
    the JSON is written -- but its source is: events.jsonl is append-only and still on disk, and
    the digest records the line count it was built from (run.cutoff). Reading events.jsonl up to
    that same cutoff yields the same repairs at full length and not one event the digest did not
    see, so the extract_lessons unit's own gate repairs (this gate's output, which quotes the
    candidate back at the agent) stay out of it.

    None when there is no usable cutoff, or events.jsonl no longer reaches it: the caller then
    keeps the clipped digest texts, which is what the check has always run on."""
    run = digest.get("run") if isinstance(digest, dict) else None
    cutoff = run.get("cutoff") if isinstance(run, dict) else None
    if not isinstance(cutoff, int) or isinstance(cutoff, bool) or cutoff < 0:
        return None
    try:
        events, _limit = run_digest._read_events(Path(run_dir), cutoff)
    except (OSError, ValueError):  # truncated or unreadable: fall back rather than guess
        return None
    header = config.get("inject_header")
    header = header if isinstance(header, str) else ""
    texts: list[str] = []
    for event in events:
        text = run_digest._repair_of(event)
        if text is None:
            continue
        # The same cleaning digest.py does before it stores a repair. An injected Memory block is
        # an entry's own text quoted back at the agent, so a trigger found only there was never
        # observed in this run and must not count as observation.
        cleaned = run_digest.strip_memory_block(text, header)
        if cleaned:
            texts.append(cleaned)
    return texts


def trigger_texts(run_dir: Path, digest: dict | None, config: dict) -> list[str]:
    """The texts a trigger counts as observed in: this run's gate repair texts, unclipped when
    events.jsonl can still supply them, plus the digest's log tail lines (already whole lines) and
    the gate repair of every prior run in the digest (the only channel a unit that runs after
    extract_lessons has; see prior_run_texts)."""
    prior = prior_run_texts(digest)
    full = repair_texts_from_events(run_dir, digest, config)
    if full is None or (not full and _digest_unit_texts(digest, repairs=True, log_tail=False)):
        # events.jsonl is unusable, or it disagrees with the digest about whether this run produced
        # a repair at all -- which is what a digest built by another checkout's digest.py looks like
        # when the event shape has moved on. Believe the digest then: replacing its repairs with an
        # empty list would fail triggers the check accepted before this ever read events.
        return digest_texts(digest) + prior
    return full + _digest_unit_texts(digest, repairs=False, log_tail=True) + prior


# --- small value checks used by rule 1 ---------------------------------------------------

def scope_problem(scope, config: dict) -> str | None:
    kinds = [k for k in config["fact_scopes"] if k != "cluster"]
    shape = "scope must be 'cluster', 'model_family:<name>' or 'dataset:<name>'"
    if not isinstance(scope, str) or not scope.strip():
        return f"{shape}, got {scope!r}"
    if scope == "cluster":
        return None
    kind, sep, name = scope.partition(":")
    if not sep or kind not in kinds or not name.strip():
        return f"{shape}, got {scope!r}"
    problem = interpolation_problem(scope)
    return f"scope contains {problem}" if problem else None


def date_problem(value, field: str) -> str | None:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return f"{field} must be a YYYY-MM-DD date, got {value!r}"
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return f"{field} is not a real calendar date: {value!r}"
    return None


def entry_id_problem(value, field: str) -> str | None:
    if not isinstance(value, str) or paths.split_entry_id(value) is None:
        return f"{field} must be an entry id '<target_skill>/<slug>', got {value!r}"
    return None


# --- rule 1: schema, enums, required fields, body ---------------------------------------

def _declaration_shape(d: dict) -> list[GateFailure]:
    out: list[GateFailure] = []

    def fail(msg: str) -> None:
        out.append(GateFailure(1, f"extraction_declaration.json {msg}"))

    missing = [k for k in DECLARATION_REQUIRED if k not in d]
    if missing:
        fail(f"is missing required field(s) {missing}")
    if d.get("schema") != DECLARATION_SCHEMA:
        fail(f"schema must be {DECLARATION_SCHEMA!r}, got {d.get('schema')!r}")
    if "no_new_lessons" in d and not isinstance(d["no_new_lessons"], bool):
        fail(f"no_new_lessons must be a boolean, got {d['no_new_lessons']!r}")
    if "no_lessons_reason" in d and not (d["no_lessons_reason"] is None or isinstance(d["no_lessons_reason"], str)):
        fail(f"no_lessons_reason must be a string or null, got {d['no_lessons_reason']!r}")
    for key in ("covered_by", "candidates", "infra_evidence"):
        value = d.get(key)
        if key in d and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            fail(f"{key} must be a list of strings, got {value!r}")
    if "infra_noise" in d and not isinstance(d["infra_noise"], bool):
        fail(f"infra_noise must be a boolean, got {d['infra_noise']!r}")
    return out


def _proposal_shape(cand: Candidate, ctx: GateContext) -> list[GateFailure]:
    p = cand.proposal
    cfg = ctx.config
    out: list[GateFailure] = []

    def fail(msg: str) -> None:
        out.append(GateFailure(1, f"candidate {cand.cid}: {msg}"))

    missing = [k for k in PROPOSAL_REQUIRED if k not in p]
    if missing:
        fail(f"proposal.json is missing required field(s) {missing}")
    if p.get("schema") != PROPOSAL_SCHEMA:
        fail(f"schema must be {PROPOSAL_SCHEMA!r}, got {p.get('schema')!r}")

    etype = p.get("type") if p.get("type") in ENTRY_TYPES else None
    if etype is None:
        fail(f"type must be one of {ENTRY_TYPES}, got {p.get('type')!r}")
    op = p.get("op") if p.get("op") in OPS else None
    if op is None:
        fail(f"op must be one of {OPS}, got {p.get('op')!r}")

    target_skill = p.get("target_skill")
    if target_skill not in cfg["target_skills"]:
        fail(f"target_skill must be one of {cfg['target_skills']}, got {target_skill!r}")
        target_skill = None
    elif etype == "fact" and target_skill != "_shared":
        # The layout is fixed (skeleton 1.4): a fact is published to
        # sure/skills/_shared/memory/facts/<slug>.md while entry_id keeps target_skill, so
        # any other value would be read back from disk under a different entry id.
        fail("a fact must use target_skill '_shared' (facts live in sure/skills/_shared/memory/facts/)")
    elif etype == "bad_case" and target_skill == "_shared":
        # _shared holds facts only; there is no _shared/references/memory/bad_cases/.
        fail("target_skill '_shared' is for facts; a bad_case must name the skill whose unit hit it")

    target_entry = p.get("target_entry")
    if op == "add" and target_entry is not None:
        fail(f"target_entry must be null for op=add, got {target_entry!r}")
    elif op in ("modify", "supersede") and (not isinstance(target_entry, str) or paths.split_entry_id(target_entry) is None):
        fail(f"target_entry must be an entry id for op={op}, got {target_entry!r}")

    applies_to = p.get("applies_to")
    if not isinstance(applies_to, list) or not applies_to or not all(isinstance(s, str) for s in applies_to):
        fail(f"applies_to must be a non-empty list of skills, got {applies_to!r}")
    else:
        for skill in applies_to:
            if skill not in cfg["target_skills"]:
                fail(f"applies_to contains unknown skill {skill!r}")
        if len(set(applies_to)) != len(applies_to):
            fail("applies_to has duplicates")

    cell = p.get("cell")
    if not isinstance(cell, dict) or not isinstance(cell.get("component"), str) or not isinstance(cell.get("cause"), str):
        fail(f"cell must be an object with component and cause strings, got {cell!r}")
    else:
        component, cause = cell["component"], cell["cause"]
        if etype == "fact":
            if component != FACT_COMPONENT:
                fail(f"cell.component must be '_' for a fact, got {component!r}")
        elif etype == "bad_case" and target_skill is not None:
            unit_ids = ctx.skills.get(target_skill) or []
            if unit_ids and component not in unit_ids:
                fail(f"cell.component {component!r} must be a {target_skill} unit id from units.json")
            elif not unit_ids and component != FACT_COMPONENT:
                fail(f"cell.component {component!r} must be '_' ({target_skill} has no state machine)")
            elif (target_skill == ctx.run_skill and component != FACT_COMPONENT
                  and component in (_digest_units(ctx.digest) or {}) and component not in claim_units(p)):
                # match.ts filters bad_cases on component === unit with no fallback, so a component
                # no claim names files the entry on a cell it was never learned on: it is offered
                # where the lesson does not apply and missing where it does. Only units this run
                # walked are bound: rule 3 forbids claiming a unit the digest does not list, and a
                # bad_case filed against another skill has nothing in this digest to claim either.
                # Ceiling: a claim on any listed unit is copyable straight out of the digest, so
                # this catches a cell that disagrees with the candidate's own claims, not one that
                # is wrong about both. Binding a trigger to the unit that emitted it needs the
                # per-unit attribution rule 4 does not have.
                fail(f"cell.component {component!r} is not named by any claim; a bad_case is filed on a unit its "
                     f"own claims describe (claims name {sorted(claim_units(p))})")
        if cause not in cfg["cause_enum"]:
            fail(f"cell.cause {cause!r} must be one of {cfg['cause_enum']}")
        elif etype == "fact" and cause != FACT_CAUSE:
            fail(f"cell.cause must be {FACT_CAUSE!r} for a fact, got {cause!r}")
        elif etype == "bad_case" and cause == FACT_CAUSE:
            fail(f"cell.cause {FACT_CAUSE!r} is reserved for facts; pick the cause from config.json cause_enum")

    trigger = p.get("trigger")
    if not isinstance(trigger, list):
        fail(f"trigger must be a list of strings, got {trigger!r}")
    else:
        limit = int(cfg["max_triggers_per_candidate"])
        if len(trigger) > limit:
            fail(f"trigger has {len(trigger)} entries (max {limit})")
        for i, item in enumerate(trigger):
            if not isinstance(item, str):
                fail(f"trigger[{i}] must be a string, got {item!r}")
                continue
            problem = interpolation_problem(item)
            if problem is not None:
                fail(f"trigger[{i}] contains {problem}: {item!r}")

    if not isinstance(p.get("causal"), bool):
        fail(f"causal must be a boolean, got {p.get('causal')!r}")

    evidence = p.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        fail(f"evidence must be a non-empty list of strings, got {evidence!r}")
    else:
        for i, item in enumerate(evidence):
            if not isinstance(item, str) or not item.strip():
                fail(f"evidence[{i}] must be a non-empty string, got {item!r}")

    claims = p.get("claims")
    if not isinstance(claims, list):
        fail(f"claims must be a list, got {claims!r}")
    else:
        for i, claim in enumerate(claims):
            if not isinstance(claim, dict):
                fail(f"claims[{i}] must be an object, got {claim!r}")
                continue
            if claim.get("kind") not in CLAIM_KINDS:
                fail(f"claims[{i}].kind must be one of {CLAIM_KINDS}, got {claim.get('kind')!r}")
            if not isinstance(claim.get("unit"), str) or not claim["unit"].strip():
                fail(f"claims[{i}].unit must be a non-empty string, got {claim.get('unit')!r}")
            attempt = claim.get("attempt")
            if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
                fail(f"claims[{i}].attempt must be an integer >= 1, got {attempt!r}")
            if not isinstance(claim.get("status"), str) or not claim["status"].strip():
                fail(f"claims[{i}].status must be a non-empty string, got {claim.get('status')!r}")

    source = p.get("source")
    if not isinstance(source, dict):
        fail(f"source must be an object, got {source!r}")
    else:
        for key in ("run_id", "target"):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                fail(f"source.{key} must be a non-empty string, got {value!r}")
                continue
            problem = interpolation_problem(value)
            if problem is not None:
                fail(f"source.{key} contains {problem}: {value!r}")
        if source.get("skill") not in ctx.skills:
            fail(f"source.skill must be one of {sorted(ctx.skills)}, got {source.get('skill')!r}")
        sha = source.get("digest_sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            fail(f"source.digest_sha256 must be a 64-character lowercase hex sha256, got {sha!r}")

    similar = p.get("similar")
    if similar is not None:
        if not isinstance(similar, dict):
            fail(f"similar must be null or an object {{entry, difference}}, got {similar!r}")
        else:
            problem = entry_id_problem(similar.get("entry"), "similar.entry")
            if problem is not None:
                fail(problem)
            if not isinstance(similar.get("difference"), str) or not similar["difference"].strip():
                fail(f"similar.difference must be a non-empty string, got {similar.get('difference')!r}")

    if etype == "fact":
        problem = scope_problem(p.get("scope"), cfg)
        if problem is not None:
            fail(problem)
        problem = date_problem(p.get("checked_at"), "checked_at")
        if problem is not None:
            fail(problem)
    elif etype == "bad_case":
        if p.get("scope") is not None:
            fail(f"scope must be null for a bad_case, got {p.get('scope')!r}")
        if p.get("checked_at") is not None:
            fail(f"checked_at must be null for a bad_case, got {p.get('checked_at')!r}")
    return out


def _body_checks(cand: Candidate, ctx: GateContext) -> list[GateFailure]:
    body = cand.body
    if body is None:  # unknown type: already reported by _proposal_shape
        return []
    p = cand.proposal
    cfg = ctx.config
    out = [GateFailure(1, f"candidate {cand.cid}: proposal.md: {err}") for err in body.errors]

    def fail(msg: str) -> None:
        out.append(GateFailure(1, f"candidate {cand.cid}: proposal.md {msg}"))

    if p.get("type") == "bad_case":
        limit = int(cfg["bad_case_max_words"])
        if body.word_count > limit:
            fail(f"body is {body.word_count} words (max {limit}; headings and code blocks are not counted)")
        return out
    limit = int(cfg["fact_max_words"])
    if body.word_count > limit:
        fail(f"notes are {body.word_count} words (max {limit}; the H1 and the Scope/Checked-at/Evidence lines are not counted)")
    # The three header lines must agree with proposal.json: index / meta take the JSON values.
    scope = body.sections.get("Scope")
    if scope and isinstance(p.get("scope"), str) and scope != p["scope"]:
        fail(f"Scope: {scope!r} does not equal proposal.json scope {p['scope']!r}")
    checked = body.sections.get("Checked-at")
    if checked and isinstance(p.get("checked_at"), str) and checked != p["checked_at"]:
        fail(f"Checked-at: {checked!r} does not equal proposal.json checked_at {p['checked_at']!r}")
    evidence_line = body.sections.get("Evidence")
    if evidence_line and isinstance(p.get("evidence"), list) and evidence_line not in p["evidence"]:
        fail(f"Evidence: {evidence_line!r} is not listed in proposal.json evidence")
    return out


def rule_1_schema(ctx: GateContext) -> list[GateFailure]:
    out = _declaration_shape(ctx.declaration)
    for cand in ctx.candidates:
        out.extend(_proposal_shape(cand, ctx))
        out.extend(_body_checks(cand, ctx))
    return out


# --- rule 4: trigger discipline ------------------------------------------------------------

def rule_4_triggers(ctx: GateContext) -> list[GateFailure]:
    out: list[GateFailure] = []
    for cand in ctx.candidates:
        cid = cand.cid
        p = cand.proposal
        raw = p.get("trigger")
        triggers = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []
        for t in triggers:
            problem = trigger_problem(t, ctx.config)
            if problem is not None:
                out.append(GateFailure(4, f"candidate {cid}: {problem}"))
        etype = p.get("type")
        if etype == "bad_case":
            out.extend(_bad_case_trigger_failures(ctx, cid, triggers))
        elif etype == "fact":
            out.extend(_fact_trigger_failures(ctx, cid, triggers, p.get("evidence")))
    return out


def _no_trigger_failure(cid: str, etype: str) -> list[GateFailure]:
    """The one "this candidate has no trigger" failure, shared by both entry types.

    Neither type is usable without one, though they fail differently in match.ts: matchBadCases
    drops an entry whose hook triggers hit nothing, so a triggerless bad_case is stored and never
    selected, while matchFacts keeps an entry on scope alone, so a triggerless 'cluster' fact is
    selected on every run at hitLength 0 -- injected unconditionally, ranked last, and never once
    because anything in the run said it was relevant. Requiring a trigger of both keeps selection
    tied to something the run actually said."""
    return [GateFailure(4, f"candidate {cid}: a {etype} needs at least one trigger; without one it cannot be matched "
                           "on anything this run said, so it would be stored and never usefully selected")]


def _bad_case_trigger_failures(ctx: GateContext, cid: str, triggers: list[str]) -> list[GateFailure]:
    if not any(t.strip() for t in triggers):
        return _no_trigger_failure(cid, "bad_case")
    # A prior run's id is excluded like this run's: a trigger copied out of prior_runs[].last_repair
    # with the old id still in it passes hook_trigger (same texts) and then never matches again,
    # because match.ts weighs it against a future run's texts, where that id does not appear.
    generic = [t for t in triggers if is_generic_trigger(t, run_id=ctx.run_id, target_id=ctx.target_id,
                                                         exclude=prior_run_ids(ctx.digest))]
    observed = [t for t in triggers if observed_in(t, ctx.trigger_texts)]
    # One trigger has to satisfy both: reusable text AND seen in this run. Judging the two
    # separately would accept "generic but never seen" next to "seen but carries the run id".
    reusable = [t for t in generic if t in observed]
    if reusable:
        return []
    if not generic:
        why = ("every trigger contains run-specific text (the run id, a prior run's id, the target id, a "
               ".sure/runs/ path, a bare number or hash, or a timestamp)")
    elif not observed:
        why = ("no trigger appears verbatim (case-insensitive) in this run's gate repair texts or log tails, "
               "or a prior run's gate repair (prior_runs[].last_repair, source \"gate\")")
        if ctx.digest_error is not None:
            why += f" ({ctx.digest_error})"
    else:
        why = ("the triggers seen in this run are run-specific and the generic ones were never seen; the same "
               "trigger must be both generic and observed")
    return [GateFailure(4, f"candidate {cid}: no reusable trigger: {why}")]


def _fact_trigger_failures(ctx: GateContext, cid: str, triggers: list[str], evidence) -> list[GateFailure]:
    if not any(t.strip() for t in triggers):
        # Scope is not enough on its own: matchFacts selects on scope OR a trigger hit, so a
        # scope-only fact is injected into every matching run with nothing in the run pointing
        # at it. A trigger is cheap here -- rule 4 already requires it to be in the cited evidence.
        return _no_trigger_failure(cid, "fact")
    scan = scan_evidence_triggers(_resolved_evidence_files(ctx, evidence), triggers,
                                  int(ctx.config["evidence_max_bytes"]))
    suffix = " (no evidence file could be read)" if not scan.readable else ""
    # scan.unverified is deliberately not reported: those triggers were not searched for to the end,
    # so the gate does not know they are absent and must not say so (see EvidenceTriggerScan). Rule 2
    # still proves the file exists, and the entry lands provisional for a human to confirm -- which
    # is why scan.unverified has to reach that human; the meta is the only place they would see it.
    return [GateFailure(4, f"candidate {cid}: fact trigger {t!r} does not appear verbatim in any evidence file{suffix}")
            for t in scan.missing]


# --- rule 5: infra isolation -----------------------------------------------------------------

def rule_5_infra(ctx: GateContext) -> list[GateFailure]:
    if ctx.declaration.get("infra_noise") is not True:
        return []
    out: list[GateFailure] = []
    for cand in ctx.candidates:
        p = cand.proposal
        if p.get("type") != "bad_case":
            continue  # facts carry cause 'n.a.' by rule 1; the infra rule is about bad_case attribution
        cell = p.get("cell")
        cause = cell.get("cause") if isinstance(cell, dict) else None
        if cause != "infra":
            out.append(GateFailure(5, f"candidate {cand.cid}: cell.cause must be 'infra' when infra_noise is true, got {cause!r}"))
    refs = ctx.declaration.get("infra_evidence")
    if not isinstance(refs, list) or not refs:
        out.append(GateFailure(5, "infra_noise is true but infra_evidence is empty; point at the log line that shows the disturbance"))
        return out
    for i, ref in enumerate(refs):
        problem = evidence_problem(ctx, ref)
        if problem is not None:
            out.append(GateFailure(5, f"infra_evidence[{i}] {problem}"))
    return out


# --- rule 6: causal needs path:line ------------------------------------------------------------

def rule_6_causal(ctx: GateContext) -> list[GateFailure]:
    out: list[GateFailure] = []
    for cand in ctx.candidates:
        p = cand.proposal
        if p.get("causal") is not True:
            continue
        evidence = p.get("evidence")
        has_line_ref = False
        for ref in evidence if isinstance(evidence, list) else []:
            if not isinstance(ref, str):
                continue
            path_part, line = parse_evidence_ref(ref)
            # A syntactic path:line only counts when it could resolve; part B (rule 2)
            # checks that every evidence entry actually exists.
            if line is not None and not is_unsafe_evidence_path(path_part):
                has_line_ref = True
                break
        if not has_line_ref:
            out.append(GateFailure(6, f"candidate {cand.cid}: causal is true but no evidence entry is in path:line form"))
    return out


# --- rule 10 (part A share): no digest, no candidates ----------------------------------------

def rule_10_digest_error(ctx: GateContext) -> list[GateFailure]:
    """Spec 4.2: when the hook could only write {schema, error} the agent may only declare
    no_new_lessons: true. Reported as rule 10 (declaration consistency)."""
    error = ctx.digest_error
    if error is None or not ctx.candidates:
        return []
    return [GateFailure(10, f"run_digest.json could not be built ({error}); only no_new_lessons: true (quoting that error in no_lessons_reason) is accepted, remove the candidates")]


# --- registry, context, entry points -----------------------------------------------------------

# Part B appends rule_2_evidence, rule_3_claims, rule_7_dedup, rule_8_target, rule_9_source,
# rule_10_declaration. Order does not matter: check_extraction sorts failures by rule number.
RULES: list[RuleFn] = [rule_1_schema, rule_4_triggers, rule_5_infra, rule_6_causal, rule_10_digest_error]


def _skills_table(units: dict) -> dict:
    table = units.get("skills") if isinstance(units, dict) else None
    if isinstance(table, dict):
        return table
    return units if isinstance(units, dict) else {}


def _load_digest(run_dir: Path) -> dict | None:
    try:
        digest = paths.load_json(run_dir / "artifacts" / "run_digest.json")
    except (OSError, ValueError):
        return None
    return digest if isinstance(digest, dict) else None


def _load_candidates(run_dir: Path, declaration: dict, config: dict) -> tuple[list[Candidate], list[GateFailure]]:
    """Read every declared candidate dir. Ids are joined onto artifacts/candidates/, so a
    non-single-segment id is refused before touching the filesystem, and proposal.md is measured
    before it is read: bad_case_max_words counts nothing inside a fence, so the word limit alone
    lets a pasted job log through as a handful of words."""
    md_limit = int(config["proposal_md_max_bytes"])
    candidates_dir = run_dir / "artifacts" / "candidates"
    loaded: list[Candidate] = []
    failures: list[GateFailure] = []
    ids = declaration.get("candidates")
    for cid in ids if isinstance(ids, list) else []:
        if not isinstance(cid, str):
            continue  # rule 1 reports the list shape
        if not is_single_name(cid):
            failures.append(GateFailure(10, f"candidate id {cid!r} must be a single directory name under artifacts/candidates/"))
            continue
        cdir = candidates_dir / cid
        if not cdir.is_dir():
            failures.append(GateFailure(10, f"candidate {cid}: directory artifacts/candidates/{cid} does not exist"))
            continue
        try:
            proposal = paths.load_json(cdir / "proposal.json")
        except (OSError, ValueError) as exc:
            failures.append(GateFailure(10, f"candidate {cid}: proposal.json unreadable: {exc}"))
            continue
        if not isinstance(proposal, dict):
            failures.append(GateFailure(10, f"candidate {cid}: proposal.json is not a JSON object"))
            continue
        md_path = cdir / "proposal.md"
        if not md_path.is_file():
            failures.append(GateFailure(10, f"candidate {cid}: missing proposal.md"))
            continue
        try:
            md_bytes = md_path.stat().st_size
        except OSError:
            md_bytes = 0
        if md_bytes > md_limit:
            failures.append(GateFailure(10, f"candidate {cid}: proposal.md is {md_bytes} bytes (max {md_limit}); an entry "
                                            "is a short lesson, quote the few lines that matter instead of pasting a log"))
            continue
        try:
            md = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(GateFailure(10, f"candidate {cid}: proposal.md unreadable: {exc}"))
            continue
        etype = proposal.get("type")
        body = parse_bad_case(md) if etype == "bad_case" else parse_fact(md) if etype == "fact" else None
        loaded.append(Candidate(cid=cid, dir=cdir, proposal=proposal, md=md, body=body))
    return loaded, failures


def build_context(run_dir: Path, repo_root: Path, *, config: dict, units: dict, index: dict | None,
                  checkpoint_digest_sha: str | None, declaration: dict) -> GateContext:
    run_dir = Path(run_dir)
    digest = _load_digest(run_dir)
    candidates, load_failures = _load_candidates(run_dir, declaration, config)
    return GateContext(
        run_dir=run_dir,
        repo_root=Path(repo_root),
        config=config,
        skills=_skills_table(units),
        index=index,
        checkpoint_digest_sha=checkpoint_digest_sha,
        declaration=declaration,
        digest=digest,
        trigger_texts=trigger_texts(run_dir, digest, config),
        candidates=candidates,
        load_failures=load_failures,
    )


def check_extraction(run_dir: Path, repo_root: Path, *, config: dict, units: dict, index: dict | None,
                     checkpoint_digest_sha: str | None) -> list[GateFailure]:
    """Run every registered rule against <run_dir>/artifacts/extraction_declaration.json and its
    candidates. Returns all failures sorted by rule number (stable); empty means pass."""
    run_dir = Path(run_dir)
    decl_path = run_dir / "artifacts" / "extraction_declaration.json"
    try:
        declaration = paths.load_json(decl_path)
    except (OSError, ValueError) as exc:
        return [GateFailure(1, f"cannot read extraction_declaration.json: {exc}")]
    if not isinstance(declaration, dict):
        return [GateFailure(1, "extraction_declaration.json must be a JSON object")]
    ctx = build_context(run_dir, repo_root, config=config, units=units, index=index,
                        checkpoint_digest_sha=checkpoint_digest_sha, declaration=declaration)
    failures = list(ctx.load_failures)
    for rule in RULES:
        failures.extend(rule(ctx))
    failures.sort(key=lambda f: f.rule)
    return failures


def format_repair(failures: list[GateFailure]) -> str:
    """Repair text the hook hands back to the agent (main() prints it to stderr). Empty when clean."""
    if not failures:
        return ""
    lines = [f"{GATE_NAME}: {len(failures)} problem(s) in artifacts/extraction_declaration.json and its candidates."]
    lines.extend(f"- [rule {f.rule}] {f.message}" for f in failures)
    if any(f.rule == 4 for f in failures):
        lines.append(
            "At least one trigger must be a string that would appear verbatim if the same failure happened again: "
            "found in this run's gate repair text or log tail, or in a prior run's gate repair, without the run id, "
            "a prior run's id, target id, timestamps or bare numbers."
        )
    lines.append(
        "Rule numbers follow sure/runtime/memory/EXTRACTION.md. A candidate that cannot pass may be removed; "
        "then set no_new_lessons: true and give the reason. Do not rebuild run_digest.json."
    )
    return "\n".join(lines)


# sure/runtime/memory/proposals.py  (Part B, appended after format_repair)

# ---------------------------------------------------------------------------
# Part B: target directories for evidence (rule 2), claims (3), dedup and cell
# occupancy (7), target_entry / applies_to (8), source binding (9), declaration
# consistency (10) and main(). Rules are GateContext -> list[GateFailure] like
# part A and are appended to RULES below.
# ---------------------------------------------------------------------------

INDEX_SCHEMA = "sure.memory.index.v1"
LIVE_STATUSES = ("confirmed", "provisional", "disputed")
DECLARATION_NAME = "extraction_declaration.json"
DIGEST_NAME = "run_digest.json"


# --- small pure helpers ---------------------------------------------------------

def h1_title(md: str) -> str:
    """Text of the first '# ' heading; '' when there is none. Rule 7 compares titles with it."""
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def trigger_set(proposal: dict) -> set[str]:
    """Normalised trigger set for dedup: strip + lower, empty and non-string items dropped (the same
    normalisation trigger_hits applies before its substring test)."""
    triggers = proposal.get("trigger")
    if not isinstance(triggers, list):
        return set()
    return {t.strip().lower() for t in triggers if isinstance(t, str) and t.strip()}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def near_duplicate(a: str, b: str, ratio_min: float) -> bool:
    """Two titles that are nearly the same sentence (difflib ratio >= ratio_min after strip + lower).
    Titles only: comparing triggers pairwise would flag any shared trigger and make the Jaccard
    threshold meaningless."""
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio_min


def _read_json_object(path: Path) -> dict | None:
    try:
        obj = paths.load_json(path)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def product_dirs_for(run_dir: Path, repo_root: Path) -> list[Path]:
    """Target directories evidence may cite after the run root (spec 5.3 rule 2 (b)).
    onboard: model_input_resolved.json model_dir (falls back to sure/models/<model_name>);
    eval: eval_input_resolved.json runtime.run_dir. The gate reads these itself so the agent never
    needs to know output_dir; the paths are never printed into a repair."""
    art = Path(run_dir) / "artifacts"
    out: list[Path] = []
    onboard = _read_json_object(art / "model_input_resolved.json")
    if onboard:
        model_dir = onboard.get("model_dir")
        model_name = onboard.get("model_name")
        if isinstance(model_dir, str) and model_dir.strip():
            out.append(Path(model_dir))
        elif isinstance(model_name, str) and model_name.strip():
            out.append(Path(repo_root) / "sure" / "models" / model_name)
    eval_input = _read_json_object(art / "eval_input_resolved.json")
    if eval_input:
        runtime = eval_input.get("runtime")
        run_dir_field = runtime.get("run_dir") if isinstance(runtime, dict) else None
        if isinstance(run_dir_field, str) and run_dir_field.strip():
            out.append(Path(run_dir_field))
    return out


def _digest_units(digest: dict | None) -> dict[str, dict] | None:
    """id -> unit row from the digest; None when the digest is missing or is the {schema, error} form."""
    if not isinstance(digest, dict) or not isinstance(digest.get("units"), list):
        return None
    return {u["id"]: u for u in digest["units"] if isinstance(u, dict) and isinstance(u.get("id"), str)}


def _index_entries(index: dict | None) -> list[dict]:
    if not isinstance(index, dict) or not isinstance(index.get("entries"), list):
        return []
    return [e for e in index["entries"] if isinstance(e, dict) and isinstance(e.get("entry_id"), str)]


def _entry_triggers(entry: dict) -> set[str]:
    return trigger_set({"trigger": entry.get("trigger")})


def _never_injected(entry: dict) -> bool:
    """index.never_injected for one index.json entry: the single definition of "the hooks can never
    select this", so the gate and the indexer cannot drift apart on it. Imported inside the call and
    not at module level because publish.py imports proposals.py eagerly and index.py only where it
    needs it (publish.py:799), so a broken indexer must not stop the gate from loading."""
    try:
        from . import index
    except ImportError:  # executed as a script; the import at the top of this module fixed sys.path
        from memory import index  # type: ignore[no-redef]
    return index.never_injected(entry)


def _similar_entry(proposal: dict) -> str | None:
    similar = proposal.get("similar")
    if isinstance(similar, dict) and isinstance(similar.get("entry"), str) and similar["entry"].strip():
        return similar["entry"].strip()
    return None


def _similar_has_difference(proposal: dict) -> bool:
    similar = proposal.get("similar")
    return isinstance(similar, dict) and isinstance(similar.get("difference"), str) and bool(similar["difference"].strip())


def _cell_of(proposal: dict) -> tuple[str | None, str | None]:
    cell = proposal.get("cell")
    if not isinstance(cell, dict):
        return None, None
    component, cause = cell.get("component"), cell.get("cause")
    return (component if isinstance(component, str) else None, cause if isinstance(cause, str) else None)


def claim_units(proposal: dict) -> set[str]:
    """The units claims[] names. Rule 3 checks every one of them against the digest, so unlike
    cell.component these are not free text. publish.py re-checks the cell binding with this too."""
    claims = proposal.get("claims")
    return {c["unit"] for c in claims if isinstance(c, dict) and isinstance(c.get("unit"), str)} if isinstance(claims, list) else set()


# --- rule 2: evidence ---------------------------------------------------------------

def rule_2_evidence(ctx: GateContext) -> list[GateFailure]:
    """Every evidence entry resolves (run root first, then the target dirs) and path:line is in range.
    evidence_problem is part A's single entry point, so rules 4 / 5 / 6 and this one agree."""
    out: list[GateFailure] = []
    for cand in ctx.candidates:
        evidence = cand.proposal.get("evidence")
        if not isinstance(evidence, list):
            continue  # rule 1 reports the shape
        for ref in evidence:
            problem = evidence_problem(ctx, ref)
            if problem is not None:
                out.append(GateFailure(2, f"candidate {cand.cid}: evidence {problem}"))
    return out


# --- rule 3: claims -----------------------------------------------------------------

def rule_3_claims(ctx: GateContext) -> list[GateFailure]:
    """unit_result: attempt == units[].attempts and status == units[].outcome.
    gate_repair: attempt appears in units[].repairs[].attempt and status == "failed"."""
    out: list[GateFailure] = []
    units = _digest_units(ctx.digest)
    for cand in ctx.candidates:
        claims = cand.proposal.get("claims")
        if not isinstance(claims, list):
            continue  # rule 1 reports the shape
        if claims and units is None:
            out.append(GateFailure(3, f"candidate {cand.cid}: run_digest.json has no units (digest error), so no claim can be "
                                      "verified; declare no_new_lessons: true and cite the digest error"))
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                out.append(GateFailure(3, f"candidate {cand.cid}: claim is not an object: {claim!r}"))
                continue
            kind, unit = claim.get("kind"), claim.get("unit")
            attempt, status = claim.get("attempt"), claim.get("status")
            if kind not in CLAIM_KINDS:
                out.append(GateFailure(3, f"candidate {cand.cid}: claim kind must be one of {list(CLAIM_KINDS)}, got {kind!r}"))
                continue
            if not isinstance(unit, str) or unit not in units:
                out.append(GateFailure(3, f"candidate {cand.cid}: claim names unit {unit!r} which is not in the digest units"))
                continue
            if not isinstance(attempt, int) or isinstance(attempt, bool):
                out.append(GateFailure(3, f"candidate {cand.cid}: claim attempt must be an integer, got {attempt!r}"))
                continue
            row = units[unit]
            if kind == "unit_result":
                if status != row.get("outcome"):
                    out.append(GateFailure(3, f"candidate {cand.cid}: unit_result for {unit} says status {status!r} but the "
                                              f"digest outcome is {row.get('outcome')!r}"))
                if attempt != row.get("attempts"):
                    out.append(GateFailure(3, f"candidate {cand.cid}: unit_result for {unit} says attempt {attempt} but the "
                                              f"digest attempts is {row.get('attempts')!r}"))
            else:
                repairs = row.get("repairs") if isinstance(row.get("repairs"), list) else []
                seen = sorted({r.get("attempt") for r in repairs if isinstance(r, dict) and isinstance(r.get("attempt"), int)})
                if attempt not in seen:
                    out.append(GateFailure(3, f"candidate {cand.cid}: gate_repair for {unit} attempt {attempt} not found; the "
                                              f"digest has repairs for attempts {seen}"))
                if status != "failed":
                    out.append(GateFailure(3, f"candidate {cand.cid}: a gate_repair claim describes a blocked attempt, so its "
                                              f"status must be \"failed\" (got {status!r})"))
    return out


# --- rule 7: duplicates and cell occupancy ---------------------------------------------

def rule_7_dedup(ctx: GateContext) -> list[GateFailure]:
    """Spec 5.3 rule 7 against index.json (part-B data source) and between candidates of this batch.
    Both thresholds live in config.json (dedup_jaccard_min / dedup_ratio_min); no defaults here."""
    out: list[GateFailure] = []
    jaccard_min = float(ctx.config["dedup_jaccard_min"])
    ratio_min = float(ctx.config["dedup_ratio_min"])
    entries = _index_entries(ctx.index)
    known_ids = {e["entry_id"] for e in entries}
    live = [e for e in entries if e.get("status") in LIVE_STATUSES]
    unavailable = " (index unavailable)" if ctx.index is None else ""
    covered_raw = ctx.declaration.get("covered_by")
    covered_by = {c for c in covered_raw if isinstance(c, str)} if isinstance(covered_raw, list) else set()

    for cand in ctx.candidates:
        p = cand.proposal
        op, skill = p.get("op"), p.get("target_skill")
        component, cause = _cell_of(p)
        cs = trigger_set(p)
        title = h1_title(cand.md)
        similar = _similar_entry(p)
        target = p.get("target_entry") if isinstance(p.get("target_entry"), str) else None

        # (i) similar.entry, when set, must be a real entry.
        if similar is not None and similar not in known_ids:
            out.append(GateFailure(7, f"candidate {cand.cid}: similar.entry {similar!r} is not in the memory index{unavailable}"))

        # (ii) cell occupancy: only confirmed, not superseded bad_cases the hooks can still select
        #      hold a cell shut; provisional / disputed occupants — and confirmed ones that can never
        #      be injected — allow an add that names one of them in similar and says what differs. A
        #      dead occupant used to hard-block its cell, which refused a live lesson in favour of an
        #      entry that will never fire; it is now judged the same as a provisional one.
        if op == "add" and p.get("type") == "bad_case" and component not in (None, "_") and cause is not None:
            same_cell = [e for e in live if e.get("type") == "bad_case" and e.get("target_skill") == skill
                         and e.get("component") == component and e.get("cause") == cause]
            confirmed = [e for e in same_cell if e.get("status") == "confirmed" and not e.get("superseded_by")
                         and not _never_injected(e)]
            if confirmed:
                # covered_by is read in (v) only, so it does not free a cell while the candidate is
                # still declared; the repair must not offer it as an alternative to modify/supersede.
                out.append(GateFailure(7, f"candidate {cand.cid}: cell {skill}/{component} x {cause} is occupied by confirmed "
                                          f"entry {confirmed[0]['entry_id']}; use op modify/supersede with target_entry, or "
                                          f"remove this candidate and list {confirmed[0]['entry_id']} in the declaration's "
                                          "covered_by"))
            elif same_cell:
                ids = [e["entry_id"] for e in same_cell]
                if similar not in ids or not _similar_has_difference(p):
                    out.append(GateFailure(7, f"candidate {cand.cid}: cell {skill}/{component} x {cause} already holds "
                                              f"entries {ids} (provisional/disputed, or confirmed but never injected); an add "
                                              "here needs similar.entry set to one of them and a non-empty similar.difference"))

        # (iii) an add whose trigger set equals a live entry's is a duplicate.
        identical: str | None = None
        if op == "add" and cs:
            for e in live:
                if _entry_triggers(e) == cs:
                    identical = e["entry_id"]
                    out.append(GateFailure(7, f"candidate {cand.cid}: trigger set is identical to entry {identical}; an add is "
                                              f"refused: use op modify/supersede with target_entry, or remove this candidate "
                                              f"and list {identical} in the declaration's covered_by"))
                    break

        # (iv) overlap in the same skill + component (subset, Jaccard, near-identical title) needs similar.
        flagged: list[str] = []
        for e in live:
            if e.get("target_skill") != skill or e.get("component") != component:
                continue
            if e["entry_id"] in (target, identical):
                continue
            es = _entry_triggers(e)
            overlap = bool(cs and es and (cs <= es or jaccard(cs, es) >= jaccard_min))
            if overlap or near_duplicate(title, str(e.get("title") or ""), ratio_min):
                flagged.append(e["entry_id"])
        if flagged and (similar not in flagged or not _similar_has_difference(p)):
            out.append(GateFailure(7, f"candidate {cand.cid}: overlaps entries {flagged} (shared triggers or a near-identical "
                                      "title); set similar.entry to one of them and say the difference"))

        # (v) legacy entries do not occupy cells, but a shared trigger must be acknowledged.
        for e in entries:
            if not e.get("legacy") or e.get("status") in ("superseded", "rejected"):
                continue
            shared = cs & _entry_triggers(e)
            if shared and similar != e["entry_id"] and e["entry_id"] not in covered_by:
                out.append(GateFailure(7, f"candidate {cand.cid}: trigger {sorted(shared)[0]!r} is also a trigger of legacy "
                                          f"entry {e['entry_id']}; name it in similar.entry or in the declaration's covered_by"))

    # (vi) the same judgement between candidates of this batch. similar.entry can only name an index
    #      entry, and a candidate has no entry id yet, so overlap inside a batch means: merge them.
    for a, b in combinations(ctx.candidates, 2):
        sa, sb = trigger_set(a.proposal), trigger_set(b.proposal)
        # Deliberately not scoped by skill/component: the injection matcher selects on trigger text
        # alone, so two candidates with an identical trigger set are indistinguishable to it no
        # matter which cell they claim, and that ambiguity is a defect regardless of cell.
        if sa and sb and sa == sb:
            out.append(GateFailure(7, f"candidates {a.cid} and {b.cid} have the same trigger set; merge them into one candidate"))
            continue
        if a.proposal.get("target_skill") != b.proposal.get("target_skill") or _cell_of(a.proposal)[0] != _cell_of(b.proposal)[0]:
            continue
        overlap = bool(sa and sb and (sa <= sb or sb <= sa or jaccard(sa, sb) >= jaccard_min))
        if overlap or near_duplicate(h1_title(a.md), h1_title(b.md), ratio_min):
            out.append(GateFailure(7, f"candidates {a.cid} and {b.cid} overlap (shared triggers or a near-identical title); "
                                      "merge them or make their triggers distinct"))
    return out


# --- rule 8: target_entry and applies_to ------------------------------------------------

def rule_8_target(ctx: GateContext) -> list[GateFailure]:
    out: list[GateFailure] = []
    known_ids = {e["entry_id"] for e in _index_entries(ctx.index)}
    unavailable = " (index unavailable)" if ctx.index is None else ""
    for cand in ctx.candidates:
        p = cand.proposal
        op, target = p.get("op"), p.get("target_entry")
        if op in ("modify", "supersede"):
            if not isinstance(target, str) or not target.strip():
                out.append(GateFailure(8, f"candidate {cand.cid}: op {op} needs target_entry set to an entry id from the index"))
            elif target not in known_ids:
                out.append(GateFailure(8, f"candidate {cand.cid}: target_entry {target!r} is not in the memory index{unavailable}"))
            else:
                # An entry id starts with the skill it is filed under, and `cli confirm` on a modify /
                # supersede marks target_entry superseded: a target from another skill would retire that
                # skill's entry and file the replacement in this skill's cell.
                split = paths.split_entry_id(target)
                if split is not None and split[0] != p.get("target_skill"):
                    out.append(GateFailure(8, f"candidate {cand.cid}: target_entry {target!r} belongs to {split[0]!r}, not to "
                                              f"target_skill {p.get('target_skill')!r}; an entry can only be modified or "
                                              "superseded by a lesson filed under its own skill"))
        elif op == "add" and target is not None:
            out.append(GateFailure(8, f"candidate {cand.cid}: target_entry must be null for op add"))
        if p.get("type") == "bad_case":
            skill = p.get("target_skill")
            if p.get("applies_to") != [skill]:
                out.append(GateFailure(8, f"candidate {cand.cid}: applies_to must equal [{skill!r}] for a bad_case (a "
                                          "cross-skill lesson is expressed through target_skill, not applies_to)"))
    return out


# --- rule 9: source binding (run_id + three-way digest sha) ------------------------------

def rule_9_source(ctx: GateContext) -> list[GateFailure]:
    """source.run_id is this run; source.digest_sha256 == checkpoint sha == sha of the digest on disk.
    The gate never rebuilds the digest (spec 5.3 rule 9, review H3)."""
    out: list[GateFailure] = []
    if not ctx.candidates:
        return out
    digest_path = ctx.run_dir / "artifacts" / DIGEST_NAME
    disk = paths.sha256_file(digest_path) if digest_path.is_file() else None
    checkpoint = ctx.checkpoint_digest_sha
    if disk is None:
        out.append(GateFailure(9, "artifacts/run_digest.json is missing; the hook writes it when extract_lessons starts and "
                                  "the gate does not rebuild it. If it cannot be found, declare no_new_lessons: true"))
    elif checkpoint is None:
        out.append(GateFailure(9, "the checkpoint carries no digest sha (state.json checkpoint.data.memory.digestSha256), so "
                                  "candidates cannot be bound to a digest the hook built; declare no_new_lessons: true"))
    elif checkpoint != disk:
        out.append(GateFailure(9, f"artifacts/run_digest.json was rewritten after the hook built it (checkpoint "
                                  f"{checkpoint[:12]}..., disk {disk[:12]}...); never run build_run_digest.py onto that path, "
                                  "use --out for a preview"))
    bound = disk is not None and checkpoint == disk
    for cand in ctx.candidates:
        source = cand.proposal.get("source")
        source = source if isinstance(source, dict) else {}
        run_id = source.get("run_id")
        if run_id != ctx.run_id:
            out.append(GateFailure(9, f"candidate {cand.cid}: source.run_id {run_id!r} is not this run ({ctx.run_id})"))
        if bound and source.get("digest_sha256") != disk:
            out.append(GateFailure(9, f"candidate {cand.cid}: source.digest_sha256 does not match artifacts/run_digest.json "
                                      f"({disk[:12]}...); recompute it with sha256sum after reading the digest"))
    return out


# --- rule 10: declaration consistency ---------------------------------------------------

def rule_10_declaration(ctx: GateContext) -> list[GateFailure]:
    """no_new_lessons vs candidates, at most max_candidates_per_run, no id listed twice, no candidate
    directory on disk that the declaration does not list. Part A already reports bad ids, missing
    dirs / files, bad JSON and the error-digest case."""
    out: list[GateFailure] = []
    d = ctx.declaration
    no_new = d.get("no_new_lessons")
    listed_raw = d.get("candidates")
    listed = [c for c in listed_raw if isinstance(c, str)] if isinstance(listed_raw, list) else []
    max_candidates = int(ctx.config["max_candidates_per_run"])
    if no_new is True:
        if listed:
            out.append(GateFailure(10, "no_new_lessons is true but candidates is not empty"))
        reason = d.get("no_lessons_reason")
        if not isinstance(reason, str) or not reason.strip():
            out.append(GateFailure(10, "no_new_lessons is true but no_lessons_reason is empty; say in one line why this run "
                                       "taught nothing new"))
    elif no_new is False and not listed:
        out.append(GateFailure(10, "no_new_lessons is false but candidates is empty; list the candidate directories or declare "
                                   "no_new_lessons: true with a reason"))
    if len(listed) > max_candidates:
        out.append(GateFailure(10, f"{len(listed)} candidates declared, at most {max_candidates} per run"))
    seen: set[str] = set()
    for cid in listed:
        if cid in seen:
            out.append(GateFailure(10, f"candidate {cid} is listed twice"))
        seen.add(cid)
    candidates_dir = ctx.run_dir / "artifacts" / "candidates"
    try:
        on_disk = sorted(p.name for p in candidates_dir.iterdir() if p.is_dir()) if candidates_dir.is_dir() else []
    except OSError:
        on_disk = []
    for name in on_disk:
        if name not in seen:
            out.append(GateFailure(10, f"artifacts/candidates/{name} exists on disk but is not listed in candidates; list it "
                                       "or delete it"))
    return out


RULES.extend([rule_2_evidence, rule_3_claims, rule_7_dedup, rule_8_target, rule_9_source, rule_10_declaration])


# --- main() and its inputs -------------------------------------------------------------------

def read_checkpoint_digest_sha(run_dir: Path) -> str | None:
    """state.json -> checkpoint.data.memory.digestSha256 (the hook wrote it when the unit started)."""
    state = _read_json_object(Path(run_dir) / "state.json")
    checkpoint = state.get("checkpoint") if isinstance(state, dict) else None
    data = checkpoint.get("data") if isinstance(checkpoint, dict) else None
    memory = data.get("memory") if isinstance(data, dict) else None
    sha = memory.get("digestSha256") if isinstance(memory, dict) else None
    return sha if isinstance(sha, str) and sha else None


def load_index(memory_root: Path) -> dict | None:
    """sure/memory/index.json, or None when missing, unreadable or not the schema this gate understands.
    A None index only makes similar.entry / target_entry unverifiable; plain adds still pass."""
    obj = _read_json_object(Path(memory_root) / "index.json")
    if obj is None or obj.get("schema") != INDEX_SCHEMA or not isinstance(obj.get("entries"), list):
        return None
    return obj


def default_repo_root() -> Path:
    """The checkout this library lives in: sure/runtime/memory -> repo root."""
    return paths.LIB_DIR.parents[2]


def main(argv: list[str]) -> int:
    """Gate contract shared with every other unit gate: --run-dir --produces, exit 0 = pass,
    exit 1 = fail with the repair on stderr (hooks read stderr first, stdout as fallback)."""
    parser = argparse.ArgumentParser(prog="check_memory_extraction.py",
                                     description="Gate for the extract_lessons unit (memory design spec 5.3, ten rules).")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to extraction_declaration.json")
    parser.add_argument("--repo-root", default=None, help="checkout root; default: the one holding this library")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else default_repo_root()
    produces = Path(args.produces).expanduser()
    if not produces.is_file():
        print(f"{DECLARATION_NAME} not found at {produces}", file=sys.stderr)
        return 1
    # config.json / units.json are read separately from check_extraction: their errors carry the
    # absolute host path (paths.load_json does a bare Path.read_text), and that path must never
    # reach stderr, since stderr becomes repair text a hook captures, shows to the agent, and stores.
    try:
        config = paths.load_config()
    except (OSError, ValueError):
        print("check_memory_extraction cannot read config.json", file=sys.stderr)
        return 1
    try:
        units = paths.load_units()
    except (OSError, ValueError):
        print("check_memory_extraction cannot read units.json", file=sys.stderr)
        return 1
    try:
        failures = check_extraction(
            run_dir, repo_root, config=config, units=units,
            index=load_index(paths.memory_root(repo_root)), checkpoint_digest_sha=read_checkpoint_digest_sha(run_dir))
    except Exception as exc:  # a crashing gate must still hand the agent something to act on; {exc} is never
                              # interpolated here, only the exception type, so a path embedded in an exception
                              # message (e.g. FileNotFoundError) cannot reach stderr / repair text.
        print(f"{GATE_NAME} crashed: {type(exc).__name__}. Fix the declaration and candidates per "
              "EXTRACTION.md, or declare no_new_lessons: true.", file=sys.stderr)
        return 1
    if failures:
        print(format_repair(failures), file=sys.stderr)
        return 1
    declaration = _read_json_object(produces) or {}
    if declaration.get("no_new_lessons") is True:
        print("check_memory_extraction OK: no new lessons declared")
    else:
        listed = declaration.get("candidates")
        print(f"check_memory_extraction OK: {len(listed) if isinstance(listed, list) else 0} candidate(s) validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
