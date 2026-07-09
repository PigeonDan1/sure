"""CLI for the Paper_to_UserSpec pre-agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .confidence import (
    confidence_for_user_spec,
    spans_to_paper_evidence_cards,
)
from .converters import (
    missing_information_request,
    model_input_to_onboarding_prompt,
    model_input_to_preview,
    user_spec_to_main_flow_input,
    user_spec_to_model_input,
    user_spec_to_training_request,
)
from .extractor import extract_user_spec
from .io import (
    canonicalize_paper_text,
    parse_pdf_to_artifacts,
    read_json,
    read_text,
    read_yaml,
    write_json,
    write_text,
    write_yaml,
)
from .mineru_runtime import mineru_runtime_status
from .report1 import generate_report1
from .router import route_user_spec
from .validator import (
    merge_validation_report,
    validate_main_flow_input,
    validate_model_input,
    validate_user_spec,
)

_PDF_PARSE_ERRORS: tuple[type[BaseException], ...] = (RuntimeError,)
try:
    from pypdf.errors import PdfStreamError
    _PDF_PARSE_ERRORS = (RuntimeError, PdfStreamError)
except Exception:
    pass


def _build(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    paper_parse_report = {
        "enabled": False,
        "status": "not_applicable",
        "num_pages": None,
        "extracted_chars": 0,
        "warning": None,
    }
    if args.paper and args.paper_text:
        print("Provide exactly one of --paper or --paper-text, not both.", file=sys.stderr)
        return 2
    if not args.paper and not args.paper_text:
        print("Provide exactly one of --paper or --paper-text.", file=sys.stderr)
        return 2

    if args.paper_text:
        _paper_text, paper_parse_report = canonicalize_paper_text(
            read_text(args.paper_text),
            input_source=args.paper_text,
            out_dir=out_dir,
        )
        paper_text_path = str(out_dir / "canonical_paper.md")
        paper_text = read_text(paper_text_path)
        paper_path = None
        extracted_from = "paper_text"
    elif args.paper:
        try:
            _paper_text, paper_parse_report = parse_pdf_to_artifacts(
                args.paper,
                out_dir,
                parser_name=args.pdf_parser,
            )
        except _PDF_PARSE_ERRORS as exc:
            print(f"PDF parsing failed: {exc}", file=sys.stderr)
            print("Fallback: extract the paper text separately and rerun with --paper-text.", file=sys.stderr)
            return 2
        paper_text_path = str(out_dir / "canonical_paper.md")
        paper_text = read_text(paper_text_path)
        paper_path = str(args.paper)
        extracted_from = "pdf"
    if not args.debug_artifacts:
        extracted = out_dir / "extracted_paper.txt"
        if extracted.exists():
            extracted.unlink()

    user_spec = extract_user_spec(
        case_id=args.case_id,
        paper_text=paper_text,
        raw_goal=args.goal,
        paper_path=paper_path,
        paper_text_path=paper_text_path,
        repo_url=args.repo_url,
        model_card_url=args.model_card_url,
        extracted_from=extracted_from,
    )
    paper_evidence_cards = spans_to_paper_evidence_cards(user_spec.get("evidence_spans", []), namespace="paper_ev")
    scoring_cards = [*paper_evidence_cards, *_user_provided_url_cards(user_spec, len(paper_evidence_cards) + 1)]

    confidence, paper_confidence_report = confidence_for_user_spec(
        user_spec,
        scoring_cards,
        paper_parse_report=paper_parse_report,
    )
    user_spec["confidence"] = confidence
    paper_evidence_cards = list(paper_confidence_report.get("evidence_cards", paper_evidence_cards))
    user_spec["_evidence_cards"] = paper_evidence_cards

    routing_decision = route_user_spec(
        user_spec,
        repo_root=Path.cwd(),
        route_override=args.route_override,
    )
    route = routing_decision["route"]

    user_spec_report = validate_user_spec(user_spec)
    model_report: dict[str, Any] | None = None
    main_flow_report: dict[str, Any] | None = None
    next_file = routing_decision["next_artifact"]

    if route == "tool_onboarding":
        model_input = user_spec_to_model_input(user_spec)
        write_yaml(out_dir / "MODEL_INPUT.yaml", model_input)
        model_report = validate_model_input(model_input)
    elif route == "main_flow_evaluation":
        main_flow_input = user_spec_to_main_flow_input(user_spec, routing_decision)
        write_yaml(out_dir / "MAIN_FLOW_INPUT.yaml", main_flow_input)
        main_flow_report = validate_main_flow_input(main_flow_input)
    elif route == "controlled_training_conversion":
        write_json(out_dir / "training_conversion_request.json", user_spec_to_training_request(user_spec))
    else:
        write_json(out_dir / "missing_information_request.json", missing_information_request(user_spec))

    user_spec.pop("_evidence_cards", None)
    write_json(out_dir / "user_spec_query.json", user_spec)
    _write_jsonl(out_dir / "paper_evidence_cards.jsonl", paper_evidence_cards)
    write_json(out_dir / "paper_confidence_report.json", paper_confidence_report)
    if args.debug_artifacts:
        write_text(out_dir / "extracted_paper.txt", paper_text)
        write_text(out_dir / "parsed_sections.md", paper_text)
        write_json(out_dir / "tables.json", {"tables": [], "source": "not_extracted"})
        write_json(out_dir / "figures_index.json", {"figures": [], "source": "not_extracted"})
        write_json(out_dir / "evidence_map.json", {"evidence_spans": user_spec["evidence_spans"]})
        write_json(out_dir / "routing_decision.json", routing_decision)

    report = merge_validation_report(
        user_spec_report=user_spec_report,
        route=route,
        model_input_report=model_report,
        main_flow_report=main_flow_report,
    )
    report["confidence_validation"] = user_spec_report.get("confidence_validation", {})
    if user_spec["confidence"].get("decision_hint") in {"C", "D", "needs_human_review"} and route == "tool_onboarding":
        report.setdefault("warnings", []).append(
            "confidence decision_hint is C/D/needs_human_review; MODEL_INPUT is a draft and is not recommended for formal onboarding without review."
        )
        if report["status"] == "pass":
            report["status"] = "warning"
    report["pdf_extraction"] = paper_parse_report
    report["paper_parse_report"] = paper_parse_report
    write_json(out_dir / "validation_report.json", report)
    write_text(out_dir / "README.md", _run_readme(user_spec, report, next_file))
    print(f"Paper_to_UserSpec build: {report['status'].upper()} route={route} out={out_dir}")
    return 0 if report["status"] in {"pass", "warning"} else 1


def _user_provided_url_cards(user_spec: dict[str, Any], start_index: int) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    paper_fields = {span.get("field") for span in user_spec.get("evidence_spans", []) if isinstance(span, dict)}
    for field, value in [
        ("source.repo_url", user_spec.get("source", {}).get("repo_url")),
        ("source.model_card_url", user_spec.get("source", {}).get("model_card_url")),
    ]:
        if not value or field in paper_fields:
            continue
        cards.append(
            {
                "id": f"user_ev_{start_index + len(cards):04d}",
                "field": field,
                "claim_type": "user_provided_field",
                "claim_text": f"{field} provided by user",
                "evidence_text": str(value),
                "source_type": "user_provided",
                "source_name": "cli_argument",
                "source_url": str(value) if str(value).startswith("http") else None,
                "section_name": None,
                "confidence": 0.6,
            }
        )
    return cards


def _write_jsonl(path: str | Path, items: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _validate_userspec(args: argparse.Namespace) -> int:
    report = validate_user_spec(read_json(args.input))
    print(f"user_spec_query validation: {report['status'].upper()}")
    for error in report["blocking_errors"]:
        print(f"ERROR: {error}")
    return 0 if report["status"] in {"pass", "warning"} else 1


def _validate_model_input(args: argparse.Namespace) -> int:
    report = validate_model_input(read_yaml(args.input))
    print(f"MODEL_INPUT validation: {report['status'].upper()}")
    for error in report["blocking_errors"]:
        print(f"ERROR: {error}")
    return 0 if report["status"] in {"pass", "warning"} else 1


def _preview_onboarding(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_input = read_yaml(args.input)
    report = validate_model_input(model_input)
    if report["blocking_errors"]:
        print("Cannot preview onboarding because MODEL_INPUT validation failed.", file=sys.stderr)
        for error in report["blocking_errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    write_yaml(out_dir / "model.spec.preview.yaml", model_input_to_preview(model_input))
    write_text(out_dir / "onboarding_prompt.md", model_input_to_onboarding_prompt(model_input))
    print(f"Preview onboarding artifacts generated: {out_dir}")
    return 0


def _report1(args: argparse.Namespace) -> int:
    report = generate_report1(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        enable_external_audit=args.enable_external_audit,
        offline=args.offline,
        external_evidence_json=args.external_evidence_json,
        debug_artifacts=args.debug_artifacts,
    )
    if report.get("audit_required"):
        print(report["message"], file=sys.stderr)
        return 1
    decision = report.get("decision", {})
    print(
        "Report1 generated: "
        f"decision={decision.get('label')} "
        f"pre_sure_screening_score={report.get('scores', {}).get('pre_sure_screening_score')} "
        f"out={args.output_dir}"
    )
    return 0


def _mineru_check(args: argparse.Namespace) -> int:
    del args
    status = mineru_runtime_status()
    discovery = status["mineru_discovery"]
    version_probe = status.get("version_probe") or {}
    print(json.dumps(status, indent=2, sort_keys=True))
    if not discovery.get("available"):
        return 1
    return 0 if version_probe.get("ok") else 2


def _run_readme(user_spec: dict[str, Any], report: dict[str, Any], next_file: str) -> str:
    missing = user_spec.get("missing_fields", [])
    missing_text = ", ".join(missing) if missing else "None recorded."
    source = user_spec.get("source", {})
    input_line = f"Input source: {source.get('extracted_from')}"
    extracted_text = source.get("paper_text_path")
    if source.get("extracted_from") == "pdf":
        input_line = f"Input source: pdf ({source.get('paper_path')})"
        extracted_text_line = f"- Extracted text artifact: {extracted_text}"
    else:
        extracted_text_line = f"- Paper text path: {extracted_text}"
    return "\n".join(
        [
            f"# Paper_to_UserSpec Run: {user_spec.get('case_id')}",
            "",
            "Generated dry-run artifacts from paper text/PDF metadata into a structured SURE input draft.",
            "",
            f"- {input_line}",
            extracted_text_line,
            f"- Route selected: {user_spec.get('sure_routing', {}).get('route')}",
            f"- Validation status: {report.get('status')}",
            f"- Next SURE handoff file: {next_file}",
            f"- Human information still needed: {missing_text}",
            "",
            "This run did not reproduce the paper, download model weights, run inference, run benchmarks, or train models.",
            "",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper_to_userspec")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build user_spec_query and route-specific dry-run artifacts.")
    build.add_argument("--case-id", required=True)
    build.add_argument("--paper-text")
    build.add_argument("--paper")
    build.add_argument("--pdf-parser", choices=["pypdf", "mineru", "auto", "mineru-first"], default="pypdf")
    build.add_argument("--repo-url")
    build.add_argument("--model-card-url")
    build.add_argument("--debug-artifacts", action="store_true")
    build.add_argument("--goal", required=True)
    build.add_argument(
        "--route-override",
        choices=[
            "tool_onboarding",
            "main_flow_evaluation",
            "controlled_training_conversion",
            "needs_human_input",
        ],
    )
    build.add_argument("--out", required=True)
    build.set_defaults(func=_build)

    validate_userspec = sub.add_parser("validate-userspec")
    validate_userspec.add_argument("--input", required=True)
    validate_userspec.set_defaults(func=_validate_userspec)

    validate_model = sub.add_parser("validate-model-input")
    validate_model.add_argument("--input", required=True)
    validate_model.set_defaults(func=_validate_model_input)

    preview = sub.add_parser("preview-onboarding")
    preview.add_argument("--input", required=True)
    preview.add_argument("--out", required=True)
    preview.set_defaults(func=_preview_onboarding)

    report1 = sub.add_parser("report1", help="Generate Pre-SURE Screening Report from build artifacts.")
    report1.add_argument("--input-dir", required=True)
    report1.add_argument("--output-dir", required=True)
    report1.add_argument("--enable-external-audit", action="store_true")
    report1.add_argument("--offline", dest="offline", action="store_true", default=True)
    report1.add_argument("--online", dest="offline", action="store_false")
    report1.add_argument("--external-evidence-json")
    report1.add_argument("--debug-artifacts", action="store_true")
    report1.set_defaults(func=_report1)

    mineru_check = sub.add_parser("mineru-check", help="Check MinerU CLI discovery without parsing a PDF.")
    mineru_check.set_defaults(func=_mineru_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
