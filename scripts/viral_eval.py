#!/usr/bin/env python3
"""Diagnostic report for ClippyMe two-stage viral selection.

Examples
--------
    # Inspect a completed job (prints the stored comparison if evaluation ran)
    python scripts/viral_eval.py --job-dir output/<job_id>

    # Compare a legacy single-stage selection with a two-stage selection
    python scripts/viral_eval.py --legacy legacy_shorts.json --two-stage two_stage_review.json

    # Add a manually reviewed reference set of [start, end] ranges
    python scripts/viral_eval.py --legacy ... --two-stage ... --reference reference.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from clippyme.pipeline import two_stage_ops  # noqa: E402


def _load(path: str) -> dict | list | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _shorts(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("shorts"), list):
            return payload["shorts"]
        if isinstance(payload.get("selections"), list):
            return payload["selections"]
    return []


def _discovery_count(payload) -> int:
    if isinstance(payload, dict):
        report = payload.get("two_stage") if isinstance(payload.get("two_stage"), dict) else payload
        count = (
            (report.get("discovery") or {}).get("consolidation") or {}
        ).get("final")
        if isinstance(count, int):
            return count
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            return len(candidates)
    return 0


def _reference(payload) -> list[list[float]] | None:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("ranges") or payload.get("reference_ranges")
    else:
        return None
    ranges = []
    for item in values or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            ranges.append([float(item[0]), float(item[1])])
        elif isinstance(item, dict) and "start" in item and "end" in item:
            ranges.append([float(item["start"]), float(item["end"])])
    return ranges or None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", help="Job output directory to inspect")
    parser.add_argument("--two-stage", help="JSON file with two-stage shorts/selections")
    parser.add_argument("--legacy", help="JSON file with legacy single-stage shorts")
    parser.add_argument("--reference", help="JSON file with manually preferred [start,end] ranges")
    args = parser.parse_args(argv)

    if args.job_dir:
        report_path = os.path.join(args.job_dir, "two_stage_report.json")
        report = _load(report_path)
        if not report:
            print(f"No two-stage report at {report_path}")
            return 1
        print("Two-stage report")
        print("=" * 48)
        print(f"Status       : {'no clips selected' if report.get('reviewed_empty') else 'selected'}")
        print(f"Models       : {report.get('models')}")
        print(f"Discovery    : {report.get('discovery', {}).get('consolidation')}")
        print(f"Review       : {report.get('review')}")
        print(f"Totals       : {report.get('totals')}")
        print("\nSelected clips:")
        for item in report.get("selections") or []:
            print(f"  #{item.get('rank')} {item.get('quality_score')}/10 "
                  f"[{item.get('start')}-{item.get('end')}] {item.get('reason')}")
        if report.get("rejected"):
            print("\nRejected:")
            for item in report["rejected"][:40]:
                print(f"  {item.get('candidate_id')}: {item.get('reason')}")
        comparison = _load(os.path.join(args.job_dir, "viral_eval_report.json"))
        if comparison:
            print("\n" + two_stage_ops.format_eval_report(comparison))
        return 0

    if not args.two_stage:
        parser.error("--two-stage or --job-dir is required")
    two_stage = _load(args.two_stage)
    if two_stage is None:
        print(f"Could not read {args.two_stage}")
        return 1
    if not args.legacy:
        # Without a legacy run, still show the two-stage selections.
        for item in _shorts(two_stage):
            print(f"[{float(item['start']):.1f}-{float(item['end']):.1f}] "
                  f"{item.get('viral_score', '?')} {item.get('viral_reason', '')[:100]}")
        return 0

    legacy = _load(args.legacy)
    if legacy is None:
        print(f"Could not read {args.legacy}")
        return 1
    reference = _reference(_load(args.reference)) if args.reference else None
    report = two_stage_ops.compare_with_legacy(
        _shorts(legacy), _shorts(two_stage),
        discovery_candidates=_discovery_count(two_stage),
        reference_ranges=reference,
    )
    ts_report = two_stage.get("two_stage") if isinstance(two_stage, dict) else None
    if isinstance(ts_report, dict):
        report["two_stage_models"] = ts_report.get("models")
        report["two_stage_models_used"] = ts_report.get("models_used")
        report["two_stage_totals"] = ts_report.get("totals")
    if isinstance(legacy, dict):
        cost = legacy.get("cost_analysis") or {}
        report["legacy_model"] = cost.get("model")
    report["discovery_candidate_count"] = _discovery_count(two_stage)
    print(two_stage_ops.format_eval_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
