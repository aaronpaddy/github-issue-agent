"""Run cases through the real pipeline and verify the results independently of the agent."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import structlog

from src.agent.environment import create_sandboxed_python_env
from src.agent.policy import is_test_path
from src.agent.sandbox import DockerWorkspace, SandboxConfig
from src.config import Settings
from src.evals.cases import Case
from src.evals.fake_github import FakeGitHub
from src.evals.mutation import MutantNotApplicable, mutated
from src.evals.scoring import CaseResult, classify, judge
from src.github.auth import resolve_auth
from src.github.git_ops import checkout, clone_repo
from src.pipeline import run_issue

logger = structlog.get_logger(__name__)

HIDDEN_TEST_NAME = "_hidden_eval.py"


@dataclass
class Verification:
    hidden_ok: bool | None = None
    mutants_caught: int | None = None
    forbidden_found: tuple[str, ...] = ()


def verify(case: Case, workspace_root: Path, image: str) -> Verification:
    """Judge the agent's final workspace with tests and mutants it never saw."""
    ws = DockerWorkspace(workspace_root, workspace_root.parent / "venv", SandboxConfig(image=image))
    outcome = Verification()

    if case.forbidden_in_diff:
        diff = ws.run(["git", "diff", "HEAD"]).stdout + "".join(
            (workspace_root / f).read_text(errors="replace")
            for f in ws.run(["git", "ls-files", "--others", "--exclude-standard"]).stdout.split()
        )
        outcome.forbidden_found = tuple(t for t in case.forbidden_in_diff if t in diff)

    hidden_path = workspace_root / "tests" / HIDDEN_TEST_NAME
    try:
        if case.hidden_test_path:
            shutil.copy(case.hidden_test_path, hidden_path)
            ws.mark_changed()
            result = ws.run(["pytest", "-q", "-p", "no:cacheprovider", f"tests/{HIDDEN_TEST_NAME}"])
            outcome.hidden_ok = result.ok

        if case.mutants:
            outcome.mutants_caught = 0
            for mutant in case.mutants:
                try:
                    with mutated(workspace_root, mutant):
                        ws.mark_changed()
                        result = ws.run(
                            ["pytest", "-q", "-x", "-p", "no:cacheprovider", f"--ignore=tests/{HIDDEN_TEST_NAME}"]
                        )
                except MutantNotApplicable:
                    continue  # the agent rewrote that code; this mutant can't be judged
                if not result.ok:
                    outcome.mutants_caught += 1
    finally:
        hidden_path.unlink(missing_ok=True)
        ws.mark_changed()
    return outcome


def run_one(
    case: Case,
    run_index: int,
    settings: Settings,
    work_root: Path,
    token: str,
    case_budget: float,
    keep: bool,
) -> CaseResult:
    started = time.time()
    fake = FakeGitHub(case)

    def cloner(repo: str, dest: Path) -> None:
        clone_repo(repo, dest, token)
        checkout(dest, case.base_sha)

    case_settings = settings.model_copy(
        update={
            "github_repo": case.repo,
            "workspaces_dir": str(work_root),
            "sandbox": "docker",
            "max_budget_usd": min(settings.max_budget_usd, case_budget),
        }
    )

    result = None
    try:
        result = run_issue(
            case_settings,
            case.issue_number,
            dry_run=True,
            emit=lambda message: None,
            trigger=case.trigger,
            github=fake,  # type: ignore[arg-type]
            cloner=cloner,
        )
        got = classify(result.outcome.value)
    except Exception as e:  # noqa: BLE001 - a crashed case is a result, not a reason to stop the eval
        logger.warning("case crashed", case=case.id, error=f"{type(e).__name__}: {e}")
        return CaseResult(
            case_id=case.id,
            expect=case.expect,
            run=run_index,
            outcome="error",
            got="error",
            passed=False,
            failure=f"crashed: {type(e).__name__}: {str(e)[:200]}",
            seconds=time.time() - started,
        )

    verification = Verification()
    tests_added = None
    workspace = result.workspace
    try:
        if got == "fixed" and workspace is not None:
            verification = verify(case, workspace, settings.sandbox_image)
            tests_added = any(is_test_path(f) for f in result.changed_files)
    finally:
        if workspace is not None and not keep:
            shutil.rmtree(workspace.parent, ignore_errors=True)

    passed, failure = judge(
        case,
        got,
        verification.hidden_ok,
        verification.mutants_caught,
        tests_added,
        verification.forbidden_found,
    )
    return CaseResult(
        case_id=case.id,
        expect=case.expect,
        run=run_index,
        outcome=result.outcome.value,
        got=got,
        passed=passed,
        failure=failure,
        hidden_ok=verification.hidden_ok,
        mutants_caught=verification.mutants_caught,
        mutants_total=len(case.mutants),
        tests_added=tests_added,
        forbidden_found=list(verification.forbidden_found) or None,
        confidence=result.confidence,
        policy_action=result.policy_action,
        changed_lines=result.changed_lines,
        attempts=result.attempts,
        llm_calls=result.llm_calls,
        cost_usd=result.spent_usd,
        seconds=time.time() - started,
    )


def run_eval(
    cases: list[Case],
    settings: Settings,
    work_root: Path,
    *,
    repeat: int = 1,
    budget_usd: float = 1.5,
    case_budget: float = 0.30,
    jobs: int = 1,
    keep: bool = False,
    progress: Callable[[CaseResult], None] = lambda result: None,
) -> list[CaseResult]:
    """Run every case `repeat` times. Stops starting new runs once `budget_usd` is spent."""
    work_root.mkdir(parents=True, exist_ok=True)
    tokens: dict[str, str] = {}
    for case in cases:
        if case.repo not in tokens:
            tokens[case.repo] = resolve_auth(settings, case.repo).token

    spent = 0.0
    lock = threading.Lock()

    def task(case: Case, run_index: int) -> CaseResult:
        nonlocal spent
        with lock:
            over_budget = spent >= budget_usd
        if over_budget:
            result = CaseResult(
                case_id=case.id, expect=case.expect, run=run_index, outcome="not_run",
                got="not_run", passed=False, failure="skipped: eval budget reached",
            )
        else:
            result = run_one(case, run_index, settings, work_root, tokens[case.repo], case_budget, keep)
        with lock:
            spent += result.cost_usd
        progress(result)
        return result

    work = [(case, i) for i in range(1, repeat + 1) for case in cases]
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(lambda pair: task(*pair), work))


def validate_case(case: Case, settings: Settings, work_root: Path, token: str) -> list[str]:
    """Check that a case is a fair test: its hidden tests fail on the base commit and pass with a
    known-good fix applied, and its deliberate bugs are catchable. Returns the problems found."""
    if case.expect != "fixed":
        return []
    patch = case.directory / "reference.patch"
    if not patch.is_file():
        return ["missing reference.patch"]

    job_dir = work_root / f"validate-{case.id}"
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)
    repo = job_dir / "repo"
    problems: list[str] = []
    try:
        clone_repo(case.repo, repo, token)
        checkout(repo, case.base_sha)
        image = settings.sandbox_image
        ws = DockerWorkspace(repo, job_dir / "venv", SandboxConfig(image=image))
        create_sandboxed_python_env(ws)

        if case.hidden_test:
            before = verify(replace(case, mutants=()), repo, image)
            if before.hidden_ok:
                problems.append("the hidden tests already pass on the base commit")

        applied = subprocess.run(
            ["git", "apply", str(patch)], cwd=repo, capture_output=True, text=True, check=False
        )
        if applied.returncode != 0:
            return [f"reference.patch does not apply: {applied.stderr.strip()[:200]}"]

        ws.mark_changed()
        after = verify(case, repo, image)
        if case.hidden_test and not after.hidden_ok:
            problems.append("the hidden tests fail on the reference solution")
        if case.mutants and (after.mutants_caught or 0) < case.min_mutants_caught:
            problems.append(
                f"the reference tests catch only {after.mutants_caught or 0} of "
                f"{len(case.mutants)} deliberate bugs (need {case.min_mutants_caught})"
            )
        suite = ws.run(["pytest", "-q", "-p", "no:cacheprovider"])
        if not suite.ok:
            problems.append("the reference solution breaks the existing tests")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
    return problems
