"""AgentLoop: the reason/act/observe state machine. GitHub, webhooks, and
queues are kept out of this file on purpose — it only knows about a Job, a
Workspace, an LLMClient, and a ToolRegistry.

Two safety properties are enforced here, not left to the model:
  1. Validation is authoritative and runs OUTSIDE the model's control — the
     model can call run_tests/run_command as it works, but the pass/fail
     decision that gates opening a PR always comes from validator.validate()
     called directly by this loop, never from the model's self-report.
  2. The retry cap (Job.max_attempts) is enforced by this loop's control
     flow. The model cannot iterate past it no matter what it asks for.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from src.agent.cost import BudgetExceededError
from src.agent.llm import LLMClient
from src.agent.state import AgentStatus, Attempt, Job, ValidationOutcome
from src.agent.tools.base import ToolRegistry
from src.agent.validator import Validator
from src.agent.workspace import Workspace

ValidateFn = Callable[[Workspace], ValidationOutcome]

logger = structlog.get_logger(__name__)

MAX_TOOL_ITERATIONS_PER_ATTEMPT = 20

SYSTEM_PROMPT = """\
You are an autonomous software engineering agent. You have been assigned a \
GitHub issue to fix in the repository checked out at your workspace root.

You can only interact with the repository through the tools provided — \
there is no other way to read or change files. Work methodically:

1. Investigate: use list_files, read_file, and search_code to understand \
the issue and locate the relevant code before changing anything.
2. Implement: use edit_file / create_file to make the fix. Prefer the \
smallest correct change. Add or update tests that cover the bug.
3. Self-check: use run_tests, run_command, and git_diff to sanity-check \
your work as you go.

When you believe your change is complete and correct, STOP calling tools \
and respond with a plain-text summary of what you changed and why. The \
application will then run the full validation suite (tests, lint, format, \
types) itself and tell you the authoritative result — if it fails, you'll \
get the failure output back and should continue fixing the issue.

Be economical: every tool result is re-read on each later step and costs \
money. Prefer search_code to locate code, then read only the line ranges you \
need instead of whole files, and avoid repeating calls whose results you \
already have.

Do not claim the issue is fixed unless you have actually located and \
addressed its root cause. If the issue is too ambiguous to act on \
confidently, say so plainly instead of guessing.
"""


class AgentResult:
    def __init__(self, job: Job, success: bool, summary: str):
        self.job = job
        self.success = success
        self.summary = summary


class AgentLoop:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, validate_fn: ValidateFn | None = None):
        self.llm = llm
        self.tools = tools
        self.validate_fn = validate_fn or Validator().validate

    def run(self, job: Job, workspace: Workspace) -> AgentResult:
        job.status = AgentStatus.INVESTIGATING
        messages: list[dict] = [{"role": "user", "content": self._initial_prompt(job)}]

        while True:
            attempt_number = job.current_attempt_number()
            log = logger.bind(job_id=job.id, attempt=attempt_number)
            log.info("attempt.start")

            job.status = AgentStatus.IMPLEMENTING
            try:
                final_text = self._act_until_done(messages, workspace, log)
            except BudgetExceededError as e:
                reason = f"Stopped before reaching a validated fix: {e}"
                log.warning("budget.exceeded")
                job.escalation_reason = reason
                job.finish(AgentStatus.ESCALATED)
                return AgentResult(job=job, success=False, summary=reason)

            job.status = AgentStatus.VALIDATING
            outcome = self.validate_fn(workspace)
            if outcome.passed and not self._has_changes(workspace):
                outcome = ValidationOutcome(
                    passed=False,
                    checks={**outcome.checks, "changes": False},
                    detail=(
                        "No files were changed, so nothing was fixed. Validation passing "
                        "on an unmodified repository proves nothing. Make the fix, or if "
                        "the issue can't be resolved, explain why."
                    ),
                )
            job.attempts.append(Attempt(number=attempt_number, validation=outcome, summary=final_text))
            log.info("attempt.validated", passed=outcome.passed, checks=outcome.checks)

            if outcome.passed:
                job.finish(AgentStatus.DONE)
                return AgentResult(job=job, success=True, summary=final_text)

            if job.attempts_exhausted():
                reason = self._escalation_message(job, outcome)
                job.escalation_reason = reason
                job.finish(AgentStatus.ESCALATED)
                return AgentResult(job=job, success=False, summary=reason)

            messages.append({"role": "user", "content": self._failure_prompt(outcome)})

    def _act_until_done(self, messages: list[dict], workspace: Workspace, log) -> str:
        """Runs the tool-calling inner loop until the model stops calling
        tools (signaling it believes the fix is complete) or hits the
        per-attempt tool-call cap."""
        last_text = ""
        for iteration in range(MAX_TOOL_ITERATIONS_PER_ATTEMPT):
            response = self.llm.call(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=self.tools.schemas(),
            )
            messages.append({"role": "assistant", "content": response.raw_content})
            last_text = response.text or last_text

            if not response.tool_calls:
                return last_text

            tool_result_blocks = []
            for call in response.tool_calls:
                result = self.tools.execute(workspace, call.name, call.input)
                log.info("tool_call", name=call.name, ok=result.ok)
                content = result.output if result.ok else f"ERROR: {result.error}"
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": content,
                        "is_error": not result.ok,
                    }
                )
            messages.append({"role": "user", "content": tool_result_blocks})

        messages.append(
            {
                "role": "user",
                "content": (
                    "You've reached the tool-call limit for this attempt. Stop taking "
                    "further actions and summarize the current state of your change."
                ),
            }
        )
        return last_text or "(tool-call limit reached without a summary)"

    @staticmethod
    def _has_changes(workspace: Workspace) -> bool:
        result = workspace.run(["git", "status", "--porcelain"])
        return not result.ok or bool(result.stdout.strip())

    @staticmethod
    def _initial_prompt(job: Job) -> str:
        return (
            f"{job.issue_context()}\n\n"
            "Investigate and fix this issue in the repository. Add or update tests "
            "that cover it."
        )

    @staticmethod
    def _failure_prompt(outcome: ValidationOutcome) -> str:
        failed = [name for name, ok in outcome.checks.items() if not ok]
        return (
            f"Validation failed ({', '.join(failed)}). Full output:\n\n{outcome.detail}\n\n"
            "Investigate the failure and revise your fix."
        )

    @staticmethod
    def _escalation_message(job: Job, outcome: ValidationOutcome) -> str:
        failed = [name for name, ok in outcome.checks.items() if not ok]
        message = (
            f"Could not produce a passing fix for issue #{job.issue_number} within "
            f"{job.max_attempts} attempts. Last failing checks: {', '.join(failed)}.\n\n"
            f"{outcome.detail}"
        )
        last_note = job.attempts[-1].summary.strip() if job.attempts else ""
        if last_note:
            message += f"\n\nThe agent's last message:\n{last_note}"
        return message
