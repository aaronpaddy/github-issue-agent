"""A stand-in for the GitHub client, so an eval can run the real pipeline against a made-up issue.

It records what the agent would have posted instead of posting it. Only dry runs may use it.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.agent.state import IssueComment
from src.evals.cases import Case
from src.github.client import ExistingPR, IssueContext, Overlap


class FakeGitHub:
    def __init__(self, case: Case):
        self.case = case
        self.comments: list[tuple[int, str, str]] = []

    def get_issue(self, number: int) -> IssueContext:
        return IssueContext(
            number=self.case.issue_number,
            title=self.case.issue_title,
            body=self.case.issue_body,
            comments=[
                IssueComment(author=c.author, body=c.body, agent_kind=c.agent_kind, id=i)
                for i, c in enumerate(self.case.comments, start=1)
            ],
        )

    def find_open_agent_pr(self, number: int) -> ExistingPR | None:
        return None

    def acknowledge(self, number: int) -> None:
        pass

    def acknowledge_comment(self, issue_number: int, comment_id: int) -> None:
        pass

    def comment_on_issue(self, number: int, body: str, kind: str = "note") -> None:
        self.comments.append((number, body, kind))

    def overlapping_prs(self, files: Iterable[str]) -> list[Overlap]:
        return []

    def branch_name_for(self, issue_number: int, job_id: str) -> str:
        return f"issue-agent/issue-{issue_number}"

    def default_branch(self) -> str:
        return "main"
