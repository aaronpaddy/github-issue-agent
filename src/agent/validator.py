"""Validator: runs the repo's real validation commands and produces a
structured ValidationOutcome. This is what turns "the model thinks it's
done" into "the change is objectively verified".

The check set is hardcoded per-repo for now. For aaronpaddy/slack-mcp-server
these four are declared in its own pyproject.toml dev dependencies.
"""

from __future__ import annotations

from src.agent.state import ValidationOutcome
from src.agent.workspace import Workspace

# name -> argv. Order matters: cheapest/fastest checks first so a quick
# lint failure doesn't wait behind a slow test run.
DEFAULT_CHECKS: dict[str, list[str]] = {
    "ruff": ["ruff", "check", "."],
    "black": ["black", "--check", "."],
    "mypy": ["mypy", "src"],
    "pytest": ["pytest", "-q"],
}


def validate(workspace: Workspace, checks: dict[str, list[str]] | None = None) -> ValidationOutcome:
    checks = checks or DEFAULT_CHECKS
    results: dict[str, bool] = {}
    detail_lines: list[str] = []

    for name, argv in checks.items():
        result = workspace.run(argv, timeout=180)
        results[name] = result.ok
        status = "PASS" if result.ok else "FAIL"
        detail_lines.append(f"--- {name}: {status} ---")
        if not result.ok:
            output = (result.stdout + "\n" + result.stderr).strip()
            detail_lines.append(output[:4000])

    passed = all(results.values())
    return ValidationOutcome(passed=passed, checks=results, detail="\n".join(detail_lines))
