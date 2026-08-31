#!/usr/bin/env python3
"""Publish one run's gate-passed memory candidates into sure/memory/provisional/ (spec §6.2).

Called through the two skills' scripts/publish_memory.py wrappers from the post_finish hook:

    publish_memory.py --run-dir <.sure/runs/<run_id>> --repo-root <repo> [--no-promote]

For every candidate named in artifacts/extraction_declaration.json publish_run
- takes the slug from proposal.md's H1 (a title with no ascii letters or digits falls back to
  "<last 8 chars of run_id>-<nn>"; a slug that is already taken gets "-2", "-3", ...),
- writes provisional/<target_skill>/<slug>/entry.md (five provenance lines + H1 + body) and
  proposal.json (the proposal plus an evidence_sha256 map),
- writes meta/<target_skill>/<slug>.json with zero counters, derived_from (this run's usage
  inject rows whose unit is one of the candidate's claim units), fix_exercised (the
  candidate's component unit passed after more than one attempt in the run digest) and
  hook_trigger (bad_case: the triggers proposals.trigger_hits finds in the digest's repair
  texts / log tail; fact: every trigger; the hooks only ever match on hook_trigger),
- appends a publish row to decisions.jsonl through paths.decision_row / paths.append_decision,
and copies artifacts/run_digest.json to digests/<run_id>.json. A candidate that already has a
publish row for this run is skipped, so running publish twice for one run changes nothing; an
entry of this run that has a meta but no publish row (a kill between those two writes) is rolled
back first by _reclaim_orphans, so the retry reuses its slug instead of burning a second one.
modify / supersede candidates land in provisional like any other entry and never touch their
target; only `cli confirm` applies them. If the target was already rejected the new meta says
orphan=true, and mark_orphans() lets `cli reject` flag existing children later.

Every per-candidate failure becomes one line in PublishReport.errors and the other candidates
still publish. main() runs publish, then promote.promote_all, then rebuilds the index, then
prunes the two per-run stores, and turns any error into exit 1 with the messages on stderr; the
hook only records diagnostics. A prune failure is not one of those errors: retention is
housekeeping and must not read as a failed publish.

Write-to-disk shape adapted from the old sure_check adopt_memory.py (_adopt_add,
_provenance_lines, _split_proposal_md, _is_single_name) with provisional/ as the destination.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime: `python publish.py` finds the package

from memory import paths, proposals, usage  # noqa: E402

META_SCHEMA = "sure.memory.meta.v1"
# config.json key "digest_retain_runs"; absent -> this. Far below usage.DEFAULT_USAGE_RETAIN_RUNS
# on purpose: a usage row is what promotion counts, a digest is per-run scratch nothing replays.
DEFAULT_DIGEST_RETAIN_RUNS = 50
ENTRY_TYPES = ("bad_case", "fact")
OPS = ("add", "modify", "supersede")
# The lines publish writes above the H1; a body line spelled like one would override the entry's
# routing when the index parses the header, so publish refuses it (the gate does too).
PROVENANCE_PREFIXES = ("Trigger:", "Cell:", "Source:", "Added:", "Status:", "Superseded-by:")
_LINE_REF_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)$")
_MAX_SLUG_ATTEMPTS = 1000


class PublishError(Exception):
    """One candidate could not be published; the message is the report line."""


@dataclass
class PublishReport:
    """Result of publish_run (and, wrapped, of main()'s sure.memory.publish_summary.v1).
    skipped_reason is one of: no_run_dir, no_declaration, declaration_unreadable,
    no_new_lessons, no_candidates, already_published, or publish_crashed. The first six are
    set by publish_run itself; publish_crashed is set only by main() when publish_run raises
    something publish_run did not catch (a bug, not a per-candidate failure) -- errors then
    carries exactly one 'publish: <ExceptionType>' line, the message text withheld so a crash
    cannot leak a host path into the JSON the hooks read."""

    published: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class _RunContext:
    """Facts shared by every candidate of one run (read once, before the lock is taken)."""

    run_dir: Path
    repo_root: Path
    root: Path
    run_id: str
    config: dict
    units: dict
    digest: dict | None
    trigger_texts: list[str]
    skill: str | None
    target_id: str | None
    product_dir: Path | None
    inject_rows: list[dict]
    already_published: set[str]
    today: str


# --- small readers --------------------------------------------------------------


def _read_json(path: Path) -> Any:
    """JSON or None; publish never fails because an optional input is missing or broken."""
    try:
        return paths.load_json(path)
    except (OSError, ValueError):
        return None


def _is_single_name(part: str) -> bool:
    """True when part is usable as exactly one directory name (no separators, drive, '.', '..',
    trailing dots or spaces). Adapted from the old adopt_memory._is_single_name.

    Both separators and a drive prefix are checked textually, exactly as in proposals.py:
    PurePath follows the host platform, so on Linux (where publish actually runs) PurePosixPath
    reads a backslash-joined name and a bare drive prefix as one plain name. Publish is the last
    check before a candidate lands in sure/memory/, not a duplicate of the gate's."""
    return (
        bool(part)
        and part not in (".", "..")
        and "/" not in part
        and "\\" not in part
        and re.match(r"^[A-Za-z]:", part) is None
        and not PurePath(part).drive
        and len(PurePath(part).parts) == 1
        and part == part.rstrip(" .")
    )


def _triggers(proposal: dict) -> list[str]:
    raw = proposal.get("trigger")
    return [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []


def _proposal_target(proposal: dict) -> str | None:
    source = proposal.get("source") if isinstance(proposal.get("source"), dict) else {}
    target = source.get("target")
    if isinstance(target, dict):
        target = target.get("id")
    return target if isinstance(target, str) and target else None


def _proposal_skill(proposal: dict) -> str | None:
    source = proposal.get("source") if isinstance(proposal.get("source"), dict) else {}
    skill = source.get("skill")
    return skill if isinstance(skill, str) and skill else None


# --- digest facts -----------------------------------------------------------------


def _digest_run(digest: dict | None) -> dict:
    run = digest.get("run") if isinstance(digest, dict) else None
    return run if isinstance(run, dict) else {}


def _digest_target_id(digest: dict | None) -> str | None:
    target = _digest_run(digest).get("target")
    value = target.get("id") if isinstance(target, dict) else None
    return value if isinstance(value, str) and value else None


def _digest_skill(digest: dict | None) -> str | None:
    value = _digest_run(digest).get("skill")
    return value if isinstance(value, str) and value else None


def _digest_unit(digest: dict | None, unit_id: str) -> dict | None:
    units = digest.get("units") if isinstance(digest, dict) else None
    for unit in units if isinstance(units, list) else []:
        if isinstance(unit, dict) and unit.get("id") == unit_id:
            return unit
    return None


def fix_exercised(digest: dict | None, component: str) -> bool:
    """§6.2: the candidate's component unit passed in the source run after at least one failure."""
    unit = _digest_unit(digest, component)
    if unit is None:
        return False
    attempts = unit.get("attempts")
    return unit.get("outcome") == "passed" and isinstance(attempts, int) and attempts > 1


def hook_trigger(entry_type: str, triggers: list[str], texts: list[str]) -> list[str]:
    """§1.7 hook_trigger: the subset of `trigger` the hooks (match.ts) may fire on.
    bad_case: only the triggers proposals.trigger_hits finds in `texts` — the caller passes
    proposals.trigger_texts, the SAME texts gate rule 4 checked (unclipped repairs re-read from
    events.jsonl when available, digest texts otherwise, plus the gate repair of every prior run in
    the digest). Reading the clipped digest here instead let a trigger that lived only in the
    clipped-away middle pass the gate and then publish an entry that could never fire, and dropping
    the prior runs would do the same to a lesson about a unit that runs after extract_lessons.
    Triggers the agent only saw in evidence files stay in `trigger`
    for index.md / prompt routing but never drive injection. fact: every trigger.
    No observable texts -> [] for a bad_case (the gate never passes such a candidate anyway)."""
    if entry_type != "bad_case":
        return list(triggers)
    return [t for t in triggers if any(proposals.trigger_hits(t, text) for text in texts)]


def derived_from(inject_rows: list[dict], claims: Any) -> list[str]:
    """§6.2: entry ids injected in this run at a unit the candidate's claims talk about
    (machine lineage; the agent never fills derived_from). Order of first appearance, no repeats."""
    claim_units: set[str] = set()
    for claim in claims if isinstance(claims, list) else []:
        if isinstance(claim, dict) and isinstance(claim.get("unit"), str):
            claim_units.add(claim["unit"])
    found: list[str] = []
    for row in inject_rows:
        if row.get("unit") not in claim_units:
            continue
        for entry in row.get("entries") if isinstance(row.get("entries"), list) else []:
            entry_id = entry.get("entry_id") if isinstance(entry, dict) else None
            if isinstance(entry_id, str) and entry_id not in found:
                found.append(entry_id)
    return found


def _load_inject_rows(root: Path, run_id: str) -> list[dict]:
    rows, _bad = paths.read_jsonl(root / "usage" / f"{run_id}.jsonl")
    return [row for row in rows if row.get("kind") == "inject"]


def _published_entry_ids(root: Path) -> set[str]:
    """Every entry id that has a publish row (§1.6: the row key is `action`)."""
    rows, _bad = paths.read_jsonl(root / "decisions.jsonl")
    return {str(row.get("entry_id")) for row in rows if row.get("action") == "publish" and isinstance(row.get("entry_id"), str)}


def _reclaim_orphans(root: Path, run_id: str) -> list[str]:
    """Roll back what a process death between the meta write and the publish row left behind.

    _publish_candidate writes entry.md, proposal.json and meta, then appends the publish row; a
    SIGKILL or a timeout kill in that window bypasses its `except Exception` rollback. The index
    ignores the entry (no publish row) but cli list shows it and cli confirm stages it for export,
    and re-running publish for the same run stores the same lesson again under <slug>-2. Only this
    run's own untouched provisional entries are reclaimed: an entry a human already confirmed,
    rejected or staged is left exactly where it is. Caller holds the memory lock.

    A death one step earlier, before the meta write, leaves a directory no meta names, which this
    pass cannot scope by run and _entry_taken still sees -- so every later publish of that slug,
    from any run, would take <slug>-2 forever. Those are swept whatever run left them: publish
    writes meta right after the files, reject moves the directory to rejected/ and supersede keeps
    both, so provisional/<skill>/<slug>/ without a meta is never a state anything else produces."""
    published = _published_entry_ids(root)
    reclaimed: list[str] = []
    for meta_path in sorted((root / "meta").glob("*/*.json")):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict) or meta.get("status") != "provisional" or meta.get("confirmed"):
            continue
        created = meta.get("created")
        if not isinstance(created, dict) or created.get("run_id") != run_id:
            continue
        entry_id = meta.get("entry_id")
        parts = paths.split_entry_id(entry_id) if isinstance(entry_id, str) else None
        if parts is None or entry_id in published:
            continue
        skill, slug = parts
        if (root / "outbox" / skill / slug).exists() or (root / "rejected" / skill / slug).exists():
            continue
        shutil.rmtree(root / "provisional" / skill / slug, ignore_errors=True)
        try:
            meta_path.unlink()
        except OSError:
            continue
        reclaimed.append(entry_id)
    for entry_dir in sorted((root / "provisional").glob("*/*")):
        skill, slug = entry_dir.parent.name, entry_dir.name
        if not entry_dir.is_dir() or (root / "meta" / skill / f"{slug}.json").exists():
            continue
        shutil.rmtree(entry_dir, ignore_errors=True)
        reclaimed.append(f"{skill}/{slug}")
    return reclaimed


def _already_published(root: Path, run_id: str) -> set[str]:
    """Candidate dir ids that already have a publish row for this run (per-candidate idempotency,
    so a run whose second candidate failed once can be re-run without duplicating the first)."""
    rows, _bad = paths.read_jsonl(root / "decisions.jsonl")
    return {
        str(row.get("candidate"))
        for row in rows
        if row.get("action") == "publish" and row.get("run_id") == run_id and row.get("candidate")
    }


# --- product dir and evidence -----------------------------------------------------


def product_dir_for(run_dir: Path, repo_root: Path) -> Path | None:
    """Rule 2 (b) target directory: onboard model_dir from model_input_resolved.json,
    eval runtime.run_dir from eval_input_resolved.json. None when neither is readable."""
    art = run_dir / "artifacts"
    onboard = _read_json(art / "model_input_resolved.json")
    if isinstance(onboard, dict):
        model_dir = onboard.get("model_dir")
        if isinstance(model_dir, str) and model_dir:
            return Path(model_dir)
        name = onboard.get("model_name")
        if isinstance(name, str) and name:
            return repo_root / "sure" / "models" / name
    eval_input = _read_json(art / "eval_input_resolved.json")
    if isinstance(eval_input, dict):
        runtime = eval_input.get("runtime")
        run_dir_value = runtime.get("run_dir") if isinstance(runtime, dict) else None
        if isinstance(run_dir_value, str) and run_dir_value:
            return Path(run_dir_value)
    return None


def split_line_ref(ref: str) -> tuple[str, int | None]:
    """'path:12' -> ('path', 12); anything else -> (ref, None). Only a trailing pure integer counts."""
    match = _LINE_REF_RE.match(ref)
    if match is None:
        return ref, None
    return match["path"], int(match["line"])


def _is_unsafe_rel(rel: str) -> bool:
    """Same predicate as proposals.is_unsafe_evidence_path, including its textual checks for the
    windows-shaped forms PurePosixPath does not recognise: on Linux a drive-prefixed path has no
    drive, and a backslash-separated path has no '..' part."""
    pure = PurePath(rel)
    return (
        not rel
        or pure.is_absolute()
        or bool(pure.drive)
        or rel.startswith(("/", "\\"))
        or ".." in pure.parts
        or re.match(r"^[A-Za-z]:", rel) is not None
        or ".." in rel.replace("\\", "/").split("/")
    )


def resolve_evidence(ref: str, run_dir: Path, product_dir: Path | None) -> Path | None:
    """Same order as gate rule 2: (a) inside the run dir (resolved, must stay inside),
    (b) inside the product dir (lexical join, symlinks kept). Absolute or '..' refs never resolve."""
    rel, _line = split_line_ref(ref)
    if _is_unsafe_rel(rel):
        return None
    inside_run = run_dir / rel
    try:
        if inside_run.is_file() and inside_run.resolve().is_relative_to(run_dir.resolve()):
            return inside_run
    except OSError:
        pass
    if product_dir is not None:
        inside_product = product_dir / rel
        if inside_product.is_file():
            return inside_product
    return None


def evidence_sha256(evidence: Any, run_dir: Path, product_dir: Path | None) -> dict[str, str | None]:
    """{path: sha256 or None} for every evidence ref (line suffix dropped, one key per file)."""
    out: dict[str, str | None] = {}
    for ref in evidence if isinstance(evidence, list) else []:
        if not isinstance(ref, str):
            continue
        rel, _line = split_line_ref(ref)
        if rel in out:
            continue
        resolved = resolve_evidence(ref, run_dir, product_dir)
        try:
            out[rel] = paths.sha256_file(resolved) if resolved is not None else None
        except OSError:
            out[rel] = None
    return out


# --- proposal.md and header ---------------------------------------------------------


def split_proposal_md(md_text: str) -> tuple[str, list[str]]:
    """(H1 title text, body lines) with blank lines around the body removed.

    A '# ' line inside a fenced block is a sample, not the title. proposals.parse_bad_case skips
    fenced lines before looking for the H1, so a proposal opening with a fenced sample passed the
    gate under its real title and was published under the sample line -- and the entry id, the
    index title and the single line the hooks inject all come from here, with the id fixed at
    publish, so the wrong title would never come back off."""
    lines = md_text.splitlines()
    h1_idx = None
    in_fence = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("# "):
            h1_idx = i
            break
    if h1_idx is None:
        raise PublishError("proposal.md has no '# ' H1 line")
    title = lines[h1_idx][2:].strip()
    if not title:
        raise PublishError("proposal.md H1 is empty")
    body = lines[h1_idx + 1 :]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return title, body


def header_field_problem(value: str) -> str | None:
    """Why value cannot go into a provenance header line or a README route row, or None.
    Allow-list on str.isprintable(): NEL / U+2028 / U+2029 also break splitlines()."""
    for ch in value:
        if ch == "|":
            return "a '|' (would re-split a README route-table row)"
        if ch == ";":
            return "a ';' (the Trigger: header separator)"
        if not ch.isprintable():
            return f"the non-printable character U+{ord(ch):04X}"
    return None


def provenance_line_in_body(lines: list[str]) -> str | None:
    """First body line (outside code fences) that would be read back as a provenance header."""
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith(PROVENANCE_PREFIXES):
            return stripped
    return None


def provenance_lines(
    *, trigger: list[str], target_skill: str, component: str, cause: str, run_id: str, target: str, today: str
) -> list[str]:
    """The five header lines of §5.1. Trigger may be empty (facts): the line is then just 'Trigger:'."""
    return [
        f"Trigger: {'; '.join(trigger)}".rstrip(),
        f"Cell: {target_skill}/{component} x {cause}",
        f"Source: {run_id} → {target}",
        f"Added: {today}",
        "Status: provisional",
    ]


def references_path(repo_root: Path, target_skill: str, entry_type: str, slug: str) -> Path:
    """Where `cli export` would put this entry; a file there means the slug is taken."""
    if entry_type == "fact":
        return repo_root / "sure" / "skills" / "_shared" / "memory" / "facts" / f"{slug}.md"
    return repo_root / "sure" / "skills" / target_skill / "references" / "memory" / "bad_cases" / f"{slug}.md"


# --- writing one candidate ------------------------------------------------------------


def _load_candidate(ctx: _RunContext, cand_id: str) -> tuple[dict, str]:
    if not _is_single_name(cand_id):
        raise PublishError(f"candidate id {cand_id!r} is not a single directory name")
    cdir = ctx.run_dir / "artifacts" / "candidates" / cand_id
    if not cdir.is_dir():
        raise PublishError(f"candidate dir not found: artifacts/candidates/{cand_id}")
    try:
        proposal = json.loads((cdir / "proposal.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise PublishError(f"proposal.json is unreadable: {exc.__class__.__name__}") from exc
    try:
        md_text = (cdir / "proposal.md").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise PublishError(f"proposal.md is unreadable: {exc.__class__.__name__}") from exc
    if not isinstance(proposal, dict):
        raise PublishError("proposal.json is not a JSON object")
    return proposal, md_text


def _validate_proposal(ctx: _RunContext, proposal: dict) -> tuple[str, str, str, str, str]:
    """Re-check the few fields publish turns into paths or header lines. The gate already
    enforced all of this, but candidate files sit on disk between the gate and post_finish."""
    entry_type = proposal.get("type")
    target_skill = proposal.get("target_skill")
    op = proposal.get("op")
    if entry_type not in ENTRY_TYPES:
        raise PublishError(f"type must be one of {list(ENTRY_TYPES)}, got {entry_type!r}")
    if not isinstance(target_skill, str) or target_skill not in ctx.config.get("target_skills", []):
        raise PublishError(f"target_skill {target_skill!r} is not in config.json target_skills")
    # Gate rule 1's pairing, re-checked because publish turns target_skill into the entry id while
    # cli export routes on `type`: a fact under another skill is exported to _shared/memory/facts/
    # and read back from there as a second entry, so one lesson ends up with two ids and two metas.
    if entry_type == "fact" and target_skill != "_shared":
        raise PublishError("a fact must use target_skill '_shared' (facts live in sure/skills/_shared/memory/facts/)")
    if entry_type == "bad_case" and target_skill == "_shared":
        raise PublishError("target_skill '_shared' is for facts; a bad_case must name the skill whose unit hit it")
    if op not in OPS:
        raise PublishError(f"op must be one of {list(OPS)}, got {op!r}")
    if op != "add" and paths.split_entry_id(proposal.get("target_entry")) is None:
        raise PublishError(f"{op} needs target_entry '<skill>/<slug>', got {proposal.get('target_entry')!r}")
    cell = proposal.get("cell") if isinstance(proposal.get("cell"), dict) else {}
    component, cause = cell.get("component"), cell.get("cause")
    if not isinstance(component, str) or not isinstance(cause, str):
        raise PublishError("cell.component and cell.cause must be strings")
    known_units = ctx.units.get("skills", {}).get(target_skill, [])
    if component != "_" and component not in known_units:
        raise PublishError(f"cell.component {component!r} is not a unit of {target_skill} (units.json)")
    # Gate rule 1's cell binding, re-checked for the same reason: match.ts selects a bad_case on
    # component === unit, so an entry whose component no claim names is offered at a unit it was
    # not learned on.
    if (entry_type == "bad_case" and component != "_" and target_skill == ctx.skill
            and _digest_unit(ctx.digest, component) is not None
            and component not in proposals.claim_units(proposal)):
        raise PublishError(f"cell.component {component!r} is not named by any claim of this candidate")
    fields = [("cell.component", component), ("cell.cause", cause)]
    fields += [(f"trigger[{i}]", t) for i, t in enumerate(_triggers(proposal))]
    for name, value in fields:
        problem = header_field_problem(value)
        if problem is not None:
            raise PublishError(f"{name} contains {problem}: {value!r}")
    return entry_type, target_skill, op, component, cause


def _entry_taken(ctx: _RunContext, target_skill: str, entry_type: str, slug: str) -> bool:
    """An entry_id is unique across the whole library, not just provisional/."""
    root = ctx.root
    return (
        (root / "provisional" / target_skill / slug).exists()
        or (root / "outbox" / target_skill / slug).exists()
        or (root / "rejected" / target_skill / slug).exists()
        or (root / "meta" / target_skill / f"{slug}.json").exists()
        or references_path(ctx.repo_root, target_skill, entry_type, slug).exists()
    )


def _claim_entry_dir(ctx: _RunContext, target_skill: str, entry_type: str, slug: str) -> tuple[Path, str]:
    """mkdir provisional/<target_skill>/<slug>; on collision try <slug>-2, <slug>-3, ...
    os.mkdir + FileExistsError is the claim (§6.1), the taken-check only picks the next number."""
    base = ctx.root / "provisional" / target_skill
    paths.ensure_dir(base)
    for n in range(1, _MAX_SLUG_ATTEMPTS + 1):
        candidate = slug if n == 1 else f"{slug}-{n}"
        if _entry_taken(ctx, target_skill, entry_type, candidate):
            continue
        target = base / candidate
        try:
            os.mkdir(target)
        except FileExistsError:
            continue
        paths.group_writable(target)
        return target, candidate
    raise PublishError(f"no free slug for {slug!r} after {_MAX_SLUG_ATTEMPTS} attempts")


def _target_rejected(root: Path, target_entry: Any) -> bool:
    parts = paths.split_entry_id(target_entry) if isinstance(target_entry, str) else None
    if parts is None:
        return False
    skill, slug = parts
    if (root / "rejected" / skill / slug).exists():
        return True
    meta = _read_json(root / "meta" / skill / f"{slug}.json")
    return isinstance(meta, dict) and meta.get("status") == "rejected"


def build_meta(
    ctx: _RunContext, *, entry_id: str, entry_type: str, proposal: dict, entry_sha256: str, evidence_map: dict
) -> dict:
    """§6.3 meta with zero counters, plus the §1.7 additions index / cli / match.ts need without
    opening proposal.json (op, target_entry, similar_entry, orphan, hook_trigger) and entry_sha256
    (§6.4 inclusion rule)."""
    cell = proposal.get("cell") if isinstance(proposal.get("cell"), dict) else {}
    similar = proposal.get("similar") if isinstance(proposal.get("similar"), dict) else {}
    target_skill = str(proposal.get("target_skill"))
    op = str(proposal.get("op"))
    target_entry = proposal.get("target_entry") if isinstance(proposal.get("target_entry"), str) else None
    applies_to = proposal.get("applies_to")
    component = str(cell.get("component", "_"))
    triggers = _triggers(proposal)
    return {
        "schema": META_SCHEMA,
        "entry_id": entry_id,
        "type": entry_type,
        "status": "provisional",
        "target_skill": target_skill,
        "applies_to": [str(s) for s in applies_to] if isinstance(applies_to, list) and applies_to else [target_skill],
        "component": component,
        "cause": str(cell.get("cause", "n.a.")),
        "trigger": triggers,
        "hook_trigger": hook_trigger(entry_type, triggers, ctx.trigger_texts),
        "scope": proposal.get("scope") if isinstance(proposal.get("scope"), str) else None,
        "injections": 0,
        "useful_activated": 0,
        "useful_unattributed": 0,
        "useful_runs": [],
        "disputed": 0,
        "last_hit": None,
        "created": {"run_id": ctx.run_id, "date": ctx.today},
        "confirmed": None,
        "exported": None,
        "derived_from": derived_from(ctx.inject_rows, proposal.get("claims")),
        "fix_exercised": fix_exercised(ctx.digest, component),
        "evidence_sha256": evidence_map,
        "superseded_by": None,
        "superseded_at": None,
        "checked_at": proposal.get("checked_at") if isinstance(proposal.get("checked_at"), str) else None,
        "op": op,
        "target_entry": target_entry,
        "similar_entry": similar.get("entry") if isinstance(similar.get("entry"), str) else None,
        "orphan": op != "add" and _target_rejected(ctx.root, target_entry),
        "entry_sha256": entry_sha256,
    }


def _publish_candidate(ctx: _RunContext, cand_id: str) -> str:
    """Write one candidate; returns its entry_id. Caller holds the memory lock."""
    proposal, md_text = _load_candidate(ctx, cand_id)
    entry_type, target_skill, op, component, cause = _validate_proposal(ctx, proposal)
    title, body = split_proposal_md(md_text)
    forged = provenance_line_in_body(body)
    if forged is not None:
        raise PublishError(f"proposal.md body has a provenance-looking line {forged!r}; publish writes the header itself")
    if any(not ch.isprintable() for ch in title):
        raise PublishError("proposal.md H1 contains a non-printable character")
    # Fallback slug for titles with no ascii letters/digits (e.g. Chinese): <run_id last 8>-<nn>.
    fallback = paths.slugify(f"{ctx.run_id[-8:]}-{cand_id.split('-', 1)[0]}", "entry")
    slug = paths.slugify(title, fallback)
    target = ctx.target_id or _proposal_target(proposal) or "unknown"
    entry_dir, slug = _claim_entry_dir(ctx, target_skill, entry_type, slug)
    entry_id = f"{target_skill}/{slug}"
    meta_path = ctx.root / "meta" / target_skill / f"{slug}.json"
    try:
        header = provenance_lines(
            trigger=_triggers(proposal), target_skill=target_skill, component=component, cause=cause,
            run_id=ctx.run_id, target=target, today=ctx.today,
        )
        # §5.1: the five provenance lines come first, then a blank line, then the body's H1.
        # Every reader (index.parse_header, promote.split_header, cli._split_header) starts at line 0.
        entry_text = "\n".join([*header, "", f"# {title}", "", *body]) + "\n"
        paths.atomic_write_text(entry_dir / "entry.md", entry_text)
        evidence_map = evidence_sha256(proposal.get("evidence"), ctx.run_dir, ctx.product_dir)
        proposal_copy = dict(proposal)
        proposal_copy["evidence_sha256"] = evidence_map
        paths.atomic_write_json(entry_dir / "proposal.json", proposal_copy)
        meta = build_meta(
            ctx, entry_id=entry_id, entry_type=entry_type, proposal=proposal,
            entry_sha256=paths.sha256_text(entry_text), evidence_map=evidence_map,
        )
        paths.atomic_write_json(meta_path, meta)
        # §1.6: the only publish-row builder is paths.decision_row; publish_run holds the lock.
        row = paths.decision_row(
            "publish", entry_id, "auto",
            run_id=ctx.run_id,
            skill=ctx.skill or _proposal_skill(proposal),
            target_skill=target_skill,
            type=entry_type,
            op=op,
            target_entry=proposal.get("target_entry") if isinstance(proposal.get("target_entry"), str) else None,
            candidate=cand_id,
        )
        paths.append_decision(ctx.root, row)
    except Exception:
        # Leave no half entry behind: without meta + decisions row the index would ignore it anyway,
        # and a re-run publishes this candidate again because it has no publish row.
        shutil.rmtree(entry_dir, ignore_errors=True)
        if meta_path.exists():
            try:
                meta_path.unlink()
            except OSError:
                pass
        raise
    return entry_id


# --- run level ------------------------------------------------------------------------


def _copy_digest(src: Path, dst: Path, report: PublishReport) -> None:
    if not src.is_file():
        return
    try:
        paths.atomic_write_bytes(dst, src.read_bytes())
    except OSError as exc:
        report.errors.append(f"digest copy: {exc.__class__.__name__}")


def publish_run(run_dir: Path, repo_root: Path, *, config: dict, units: dict) -> PublishReport:
    """§6.2 for one run. Never raises for a bad candidate; see PublishReport."""
    run_dir = Path(run_dir).resolve()
    repo_root = Path(repo_root).resolve()
    report = PublishReport()
    if not run_dir.is_dir():
        report.skipped_reason = "no_run_dir"
        report.errors.append(f"run dir not found: {run_dir.name}")
        return report
    root = paths.memory_root(repo_root)
    paths.ensure_memory_tree(root)
    run_id = run_dir.name
    art = run_dir / "artifacts"
    _copy_digest(art / "run_digest.json", root / "digests" / f"{run_id}.json", report)

    declaration_path = art / "extraction_declaration.json"
    if not declaration_path.is_file():
        report.skipped_reason = "no_declaration"
        return report
    declaration = _read_json(declaration_path)
    if not isinstance(declaration, dict):
        report.skipped_reason = "declaration_unreadable"
        report.errors.append("artifacts/extraction_declaration.json is not a JSON object")
        return report
    if declaration.get("no_new_lessons") is True:
        report.skipped_reason = "no_new_lessons"
        return report
    candidates = [c for c in declaration.get("candidates") or [] if isinstance(c, str)]
    if not candidates:
        report.skipped_reason = "no_candidates"
        return report

    digest = _read_json(art / "run_digest.json")
    ctx = _RunContext(
        run_dir=run_dir, repo_root=repo_root, root=root, run_id=run_id, config=config, units=units,
        digest=digest if isinstance(digest, dict) else None,
        trigger_texts=proposals.trigger_texts(run_dir, digest if isinstance(digest, dict) else None, config),
        skill=_digest_skill(digest), target_id=_digest_target_id(digest),
        product_dir=product_dir_for(run_dir, repo_root),
        inject_rows=_load_inject_rows(root, run_id),
        already_published=_already_published(root, run_id),
        today=paths.utc_today(),
    )
    todo = [c for c in candidates if c not in ctx.already_published]
    if not todo:
        report.skipped_reason = "already_published"
        return report
    with paths.memory_lock(root):
        _reclaim_orphans(root, run_id)  # anything a killed run of this same run_id half-wrote
        for cand_id in todo:
            try:
                report.published.append(_publish_candidate(ctx, cand_id))
            except PublishError as exc:
                report.errors.append(f"{cand_id}: {exc}")
            except OSError as exc:
                report.errors.append(f"{cand_id}: {exc.__class__.__name__}")
            except Exception as exc:
                # A candidate can only fail its own entry, never the run (spec: "one bad candidate
                # does not block the others"): _publish_candidate's own cleanup handler has already
                # rolled back its half-written directory/meta by the time this is reached, so it is
                # safe to record the failure and keep going. Unlike the two branches above, the
                # exception type is a bug class we did not anticipate, so only its name is recorded
                # (never {exc}), matching the redaction in main()'s crash handler below.
                report.errors.append(f"{cand_id}: {exc.__class__.__name__}")
    return report


def prune_digests(memory_root: Path, *, retain: int) -> usage.PruneReport:
    """Keep the newest `retain` digests/<run_id>.json and delete the rest.

    digests/ grows one file per run like usage/ does, but the two are not worth the same.
    A usage row is what spec 8.2 counts, so usage.prune_usage folds every row it drops into
    an archive first; a digest is the scratch copy of one run's run_digest.json that only
    `cli runs` and `cli stats` ever look at, and nothing replays it, so it is deleted
    outright and kept for far fewer runs.

    Ordering is by file name: a run id starts with its UTC timestamp, which is what
    cli.cmd_runs already reads `--since` against. Not mtime, which a file copy resets and
    which a shared filesystem does not keep faithfully anyway.

    No lock, unlike prune_usage: every unlink stands on its own, the newest digest (this
    run's, just written by _copy_digest) is never in the prune set, and cli._digests reads
    whatever subset it finds. A kill part way through leaves fewer files and nothing else."""
    report = usage.PruneReport()
    directory = Path(memory_root) / "digests"
    if not directory.is_dir():
        return report
    files = sorted(directory.glob("*.json"))
    report.kept = len(files)
    for path in files[: max(0, len(files) - max(0, int(retain)))]:
        try:
            path.unlink()
        except OSError as exc:
            # The run id is not a host path, and it is the only useful part of the name.
            report.errors.append(f"digests/{path.name}: {exc.__class__.__name__}")
            continue
        report.pruned.append(path.stem)
    report.kept = len(list(directory.glob("*.json")))
    return report


def mark_orphans(repo_root: Path, rejected_entry_id: str) -> list[str]:
    """After `cli reject <id>`: set orphan=true on every meta whose target_entry is that id.
    Takes the memory lock itself, so callers must not hold it. Returns the entry ids it marked."""
    root = paths.memory_root(Path(repo_root))
    marked: list[str] = []
    with paths.memory_lock(root):
        for meta_path in sorted((root / "meta").glob("*/*.json")):
            meta = _read_json(meta_path)
            if not isinstance(meta, dict) or meta.get("target_entry") != rejected_entry_id or meta.get("orphan") is True:
                continue
            meta["orphan"] = True
            paths.atomic_write_json(meta_path, meta)
            marked.append(str(meta.get("entry_id")))
    return marked


# --- cli ----------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Publish a run's memory candidates, promote, rebuild the index.")
    parser.add_argument("--run-dir", required=True, help=".sure/runs/<run_id>")
    parser.add_argument("--repo-root", required=True, help="checkout root (sure/memory lives under it)")
    parser.add_argument("--no-promote", action="store_true", help="skip promote.py (tests)")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    repo_root = Path(args.repo_root).resolve()
    config = paths.load_config()
    units = paths.load_units()

    try:
        report = publish_run(run_dir, repo_root, config=config, units=units)
    except Exception as exc:  # never traceback into the hook; it only wants diagnostics. {exc} is never
                              # interpolated here, only the exception type, so a path embedded in an
                              # exception message (e.g. FileNotFoundError) cannot reach the summary JSON
                              # that the hooks read (same redaction as proposals.py's crash handler).
        report = PublishReport(skipped_reason="publish_crashed", errors=[f"publish: {exc.__class__.__name__}"])

    promoted = 0
    if not args.no_promote:
        try:
            from memory import promote  # imported here so --no-promote never needs the module

            promoted = len(promote.promote_all(repo_root, config=config))
        except Exception as exc:
            report.errors.append(f"promote: {exc.__class__.__name__}")

    index_rebuilt = False
    try:
        from memory import index  # imported here so a broken index module cannot stop publish

        index.write_index(repo_root, index.build_index(repo_root, config=config, units=units), config)
        index_rebuilt = True
    except Exception as exc:
        report.errors.append(f"index: {exc.__class__.__name__}")

    # Retention runs last, deliberately. post_finish is the only moment the harness runs
    # Python against sure/memory/ on its own (pre_start's entry point is index.py), so a prune
    # has to live somewhere in here; putting it after publish, promote and the index rebuild
    # means this run's decisions are already final, and a prune that is slow, that fails, or
    # that a timeout kill cuts short can neither delay nor change any of them. In the steady
    # state it is also nearly free: a run adds one usage file and one digest, and a pass drops
    # what is over the retention count. Errors here stay out of report.errors -- housekeeping
    # that could not finish is not a failed publish -- and carry no host path, because the
    # hooks read this summary. The usage pass is told where .sure/runs is, so it can leave the
    # file of a run that is still in flight alone; this run's own directory sits in there too.
    pruned: dict[str, Any] = {"usage": 0, "digests": 0, "errors": []}
    memory_root = paths.memory_root(repo_root)
    try:
        dropped = usage.prune_usage(
            memory_root,
            retain=int(config.get("usage_retain_runs", usage.DEFAULT_USAGE_RETAIN_RUNS)),
            runs_root=run_dir.parent,
        )
        pruned["usage"] = len(dropped.pruned)
        pruned["errors"].extend(dropped.errors)
    except Exception as exc:
        pruned["errors"].append(f"usage: {exc.__class__.__name__}")
    try:
        dropped = prune_digests(
            memory_root, retain=int(config.get("digest_retain_runs", DEFAULT_DIGEST_RETAIN_RUNS))
        )
        pruned["digests"] = len(dropped.pruned)
        pruned["errors"].extend(dropped.errors)
    except Exception as exc:
        pruned["errors"].append(f"digests: {exc.__class__.__name__}")

    summary = {
        "schema": "sure.memory.publish_summary.v1",
        "run_id": run_dir.name,
        "published": report.published,
        "skipped_reason": report.skipped_reason,
        "errors": report.errors,
        "promoted": promoted,
        "index_rebuilt": index_rebuilt,
        "pruned": pruned,
    }
    print(json.dumps(summary, ensure_ascii=False))
    for error in report.errors:
        print(f"publish_memory: {error}", file=sys.stderr)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
