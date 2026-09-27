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
from dataclasses import dataclass

import structlog

from src.agent.assessment import (
    REPORT_NO_CHANGE,
    REQUEST_CLARIFICATION,
    SUBMIT_RESULT,
    TERMINAL_TOOL_SCHEMAS,
    parse_assessment,
    parse_clarification,
    parse_no_change,
)
from src.agent.cost import BudgetExceededError
from src.agent.llm import LLMClient, ToolCall
from src.agent.state import (
    AgentStatus,
    Assessment,
    Attempt,
    Clarification,
    Job,
    NoChangeNeeded,
    ValidationOutcome,
)
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

When your change is complete, call submit_result rather than writing a final \
message. The application then runs the full validation suite (tests, lint, \
format, types) itself and reports the authoritative result; if it fails you'll \
get the output back and should keep fixing.

submit_result asks for an honest assessment, and it matters. High confidence \
means the issue was specific and you did exactly what it asked. Medium means \
you interpreted a loosely worded issue or made a judgment call. Low means you \
are guessing. Work you are sure of is presented as ready to merge, while \
uncertain work is flagged for careful review, so overstating your confidence \
only hurts the maintainers. State your interpretation, and list every \
assumption the issue did not spell out.

If the issue is too vague to act on without inventing requirements (it names \
no concrete behavior, or several very different changes would each satisfy \
it), do not guess and do not make speculative changes. Call \
request_clarification with a specific question instead.

If, after investigating, the issue needs no code change (it is already fixed, \
for example by a recent commit, or the behavior it describes cannot be \
reproduced or is intended), do not invent a change. Call report_no_change_needed \
with your evidence.

Be economical: every tool result is re-read on each later step and costs \
money. Prefer search_code to locate code, then read only the line ranges you \
need instead of whole files, and avoid repeating calls whose results you \
already have.
"""

FINISH_NUDGE = (
    "You stopped without finishing. If your change is complete, call submit_result with "
    "your honest assessment. If the issue is too vague to act on, call request_clarification. "
    "If it needs no code change, call report_no_change_needed."
)


TOOL_CAP_MESSAGE = (
    "You've used all your tool calls for this attempt. Stop investigating. If your change is "
    "complete, call submit_result with an honest assessment (if you are unsure it is what was "
    "asked for, say so with low or medium confidence). If the issue turned out too vague to act "
    "on, call request_clarification. If it needs no code change, call report_no_change_needed."
)


class AgentResult:
    def __init__(self, job: Job, success: bool, summary: str):
        self.job = job
        self.success = success
        self.summary = summary


@dataclass
class ActOutcome:
    """How one round of tool use ended."""

    text: str
    assessment: Assessment | None = None
    clarification: Clarification | None = None
    no_change: NoChangeNeeded | None = None


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
                acted = self._act_until_done(messages, workspace, log)
            except BudgetExceededError as e:
                reason = f"Stopped before reaching a validated fix: {e}"
                log.warning("budget.exceeded")
                job.escalation_reason = reason
                job.finish(AgentStatus.ESCALATED)
                return AgentResult(job=job, success=False, summary=reason)

            if acted.clarification:
                log.info("clarification.requested")
                job.clarification = acted.clarification
                job.finish(AgentStatus.NEEDS_CLARIFICATION)
                return AgentResult(job=job, success=False, summary=acted.clarification.question)

            if acted.no_change:
                log.info("no_change.reported")
                job.no_change = acted.no_change
                job.finish(AgentStatus.NO_CHANGE_NEEDED)
                return AgentResult(job=job, success=False, summary=acted.no_change.reason)

            job.assessment = acted.assessment
            final_text = acted.text

            job.status = AgentStatus.VALIDATING
            outcome = self.validate_fn(workspace)
            if outcome.passed and not self._has_changes(workspace):
                outcome = ValidationOutcome(
                    passed=False,
                    checks={**outcome.checks, "changes": False},
                    detail=(
                        "No files were changed, so nothing was fixed. Validation passing "
                        "on an unmodified repository proves nothing. Make the fix; or, if the "
                        "issue needs no code change, call report_no_change_needed; or, if it is "
                        "too vague to act on, call request_clarification."
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

    def _act_until_done(self, messages: list[dict], workspace: Workspace, log) -> ActOutcome:
        """Runs the tool-calling inner loop until the model finishes with one of the terminal
        tools, or hits the per-attempt tool-call cap."""
        last_text = ""
        nudged = False
        tool_schemas = self.tools.schemas() + TERMINAL_TOOL_SCHEMAS

        for _ in range(MAX_TOOL_ITERATIONS_PER_ATTEMPT):
            response = self.llm.call(system=SYSTEM_PROMPT, messages=messages, tools=tool_schemas)
            messages.append({"role": "assistant", "content": response.raw_content})
            last_text = response.text or last_text

            if not response.tool_calls:
                if nudged:
                    # Still no structured result: hand back the text with no assessment,
                    # which the policy treats as unknown confidence.
                    return ActOutcome(text=last_text)
                nudged = True
                messages.append({"role": "user", "content": FINISH_NUDGE})
                continue

            finished: ActOutcome | None = None
            result_blocks = []
            for call in response.tool_calls:
                terminal = self._terminal_outcome(call, last_text)
                if terminal:
                    finished = terminal
                    content, is_error = "Recorded.", False
                else:
                    result = self.tools.execute(workspace, call.name, call.input)
                    log.info("tool_call", name=call.name, ok=result.ok)
                    content = result.output if result.ok else f"ERROR: {result.error}"
                    is_error = not result.ok
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": result_blocks})
            if finished:
                return finished

        return self._finish_at_cap(messages, last_text)

    def _finish_at_cap(self, messages: list[dict], last_text: str) -> ActOutcome:
        """Out of tool calls: give the model one last round, limited to the terminal tools, so
        the run still ends with an honest self-assessment instead of none."""
        messages.append({"role": "user", "content": TOOL_CAP_MESSAGE})
        response = self.llm.call(
            system=SYSTEM_PROMPT, messages=messages, tools=TERMINAL_TOOL_SCHEMAS
        )
        messages.append({"role": "assistant", "content": response.raw_content})
        last_text = response.text or last_text

        outcome: ActOutcome | None = None
        result_blocks = []
        for call in response.tool_calls:
            outcome = self._terminal_outcome(call, last_text) or outcome
            result_blocks.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": "Recorded."}
            )
        if result_blocks:
            messages.append({"role": "user", "content": result_blocks})
        return outcome or ActOutcome(text=last_text or "(tool-call limit reached without a summary)")

    @staticmethod
    def _terminal_outcome(call: ToolCall, last_text: str) -> ActOutcome | None:
        if call.name == SUBMIT_RESULT:
            assessment = parse_assessment(call.input)
            return ActOutcome(text=assessment.summary or last_text, assessment=assessment)
        if call.name == REPORT_NO_CHANGE:
            no_change = parse_no_change(call.input)
            return ActOutcome(text=no_change.reason, no_change=no_change)
        if call.name == REQUEST_CLARIFICATION:
            clarification = parse_clarification(call.input)
            return ActOutcome(text=clarification.question, clarification=clarification)
        return None

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
