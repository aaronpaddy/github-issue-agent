"""Run the evals: `python -m src.evals list` and `python -m src.evals run`."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.config import load_settings
from src.evals.cases import load_cases
from src.evals.runner import run_eval, validate_case
from src.evals.scoring import CaseResult, render_markdown, summarize

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "evals" / "results"


def _agent_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List the cases")
    sub.add_parser("report", help="Re-render evals/RESULTS.md from the latest saved results")
    check = sub.add_parser("validate", help="Check each case is a fair test (no model calls, no cost)")
    check.add_argument("--case", action="append", help="Only this case id (repeatable)")
    run = sub.add_parser("run", help="Run cases through the real pipeline")
    run.add_argument("--case", action="append", help="Only this case id (repeatable)")
    run.add_argument("--repeat", type=int, default=1, help="Runs per case (default 1)")
    run.add_argument("--budget", type=float, default=1.5, help="Total dollar budget (default 1.50)")
    run.add_argument("--case-budget", type=float, default=0.30, help="Dollar cap per run (default 0.30)")
    run.add_argument("--jobs", type=int, default=1, help="Cases to run in parallel (default 1)")
    run.add_argument("--keep", action="store_true", help="Keep each case's workspace for inspection")
    run.add_argument("--no-write", action="store_true", help="Do not write results to evals/")
    args = parser.parse_args()

    if args.command == "report":
        latest = max(RESULTS_DIR.glob("*.json"))
        payload = json.loads(latest.read_text())
        results = [CaseResult(**r) for r in payload["results"]]
        (ROOT / "evals" / "RESULTS.md").write_text(
            render_markdown(results, summarize(results), payload["meta"])
        )
        print(f"re-rendered evals/RESULTS.md from {latest.name}")
        return

    cases = load_cases()
    if args.command == "list":
        for case in cases:
            print(f"{case.id:<34} expects {case.expect:<10} {case.description}")
        return

    if args.case:
        unknown = set(args.case) - {c.id for c in cases}
        if unknown:
            sys.exit(f"unknown case(s): {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in args.case]

    settings = load_settings()
    work_root = ROOT / "workspaces" / "evals"

    if args.command == "validate":
        from src.github.auth import resolve_auth

        bad = 0
        for case in cases:
            problems = validate_case(case, settings, work_root, resolve_auth(settings, case.repo).token)
            print(f"[{'OK  ' if not problems else 'BAD '}] {case.id}" + "".join(f"\n        - {p}" for p in problems), flush=True)
            bad += bool(problems)
        sys.exit(1 if bad else 0)

    def progress(result: CaseResult) -> None:
        mark = "PASS" if result.passed else "FAIL"
        why = f"  <- {result.failure}" if result.failure else ""
        print(f"[{mark}] {result.case_id} (run {result.run}) got={result.got} ${result.cost_usd:.3f} {result.seconds:.0f}s{why}", flush=True)

    print(f"running {len(cases)} case(s) x{args.repeat}, budget ${args.budget:.2f}, {args.jobs} parallel", flush=True)
    results = run_eval(
        cases, settings, work_root, repeat=args.repeat, budget_usd=args.budget,
        case_budget=args.case_budget, jobs=args.jobs, keep=args.keep, progress=progress,
    )

    summary = summarize(results)
    meta = {
        "model": settings.claude_model,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "agent_commit": _agent_commit(),
        "repeat": args.repeat,
    }
    report = render_markdown(results, summary, meta)
    print("\n" + report)

    if not args.no_write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        payload = {"meta": meta, "summary": summary, "results": [r.to_dict() for r in results]}
        (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(payload, indent=2))
        (ROOT / "evals" / "RESULTS.md").write_text(report)
        print(f"wrote evals/results/{stamp}.json and evals/RESULTS.md")


if __name__ == "__main__":
    main()
