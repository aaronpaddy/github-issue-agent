"""GitHubClient: all GitHub-side operations (read issue, comment, open PR).

Deliberately separate from the Agent Core (src/agent/*), which has no
knowledge GitHub exists. This client is what the CLI uses to turn an
AgentResult into a real PR or a real comment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from github.GithubException import GithubException, UnknownObjectException
from github.Repository import Repository

from github import Github

AGENT_BRANCH_PREFIX = "issue-agent/issue-"


@dataclass
class IssueContext:
    number: int
    title: str
    body: str
    comments: list[str]
    state: str = "open"
    is_pull_request: bool = False


@dataclass
class ExistingPR:
    number: int
    url: str


@dataclass
class Overlap:
    """Another open PR that touches some of the same files."""

    number: int
    title: str
    files: list[str]


class GitHubClient:
    def __init__(self, token: str, repo_full_name: str):
        self._gh = Github(token)
        self.repo: Repository = self._gh.get_repo(repo_full_name)

    def get_issue(self, number: int) -> IssueContext:
        issue = self.repo.get_issue(number)
        comments = [c.body for c in issue.get_comments()]
        return IssueContext(
            number=issue.number,
            title=issue.title,
            body=issue.body or "",
            comments=comments,
            state=issue.state,
            is_pull_request=issue.pull_request is not None,
        )

    def comment_on_issue(self, number: int, body: str) -> None:
        issue = self.repo.get_issue(number)
        issue.create_comment(body)

    def open_pull_request(
        self,
        branch: str,
        title: str,
        body: str,
        base: str = "main",
        draft: bool = False,
    ) -> str:
        try:
            pr = self.repo.create_pull(title=title, body=body, head=branch, base=base, draft=draft)
        except GithubException as e:
            # Some plans don't allow draft PRs; fall back to a normal PR that says so in the title.
            if not (draft and e.status == 422):
                raise
            pr = self.repo.create_pull(
                title=f"[Needs review] {title}", body=body, head=branch, base=base
            )
        return pr.html_url

    def find_open_agent_pr(self, issue_number: int) -> ExistingPR | None:
        """An open PR the agent already raised for this issue, on any of its branches."""
        pattern = re.compile(rf"^{re.escape(AGENT_BRANCH_PREFIX)}{issue_number}(-|$)")
        for pr in self.repo.get_pulls(state="open"):
            if pattern.match(pr.head.ref):
                return ExistingPR(number=pr.number, url=pr.html_url)
        return None

    def branch_name_for(self, issue_number: int, job_id: str) -> str:
        """The agent's branch for an issue, made unique if an old one is still around."""
        name = f"{AGENT_BRANCH_PREFIX}{issue_number}"
        try:
            self.repo.get_branch(name)
        except UnknownObjectException:
            return name
        return f"{name}-{job_id[:6]}"

    def overlapping_prs(self, files: Iterable[str]) -> list[Overlap]:
        wanted = set(files)
        overlaps: list[Overlap] = []
        for pr in self.repo.get_pulls(state="open"):
            shared = sorted(wanted & {f.filename for f in pr.get_files()})
            if shared:
                overlaps.append(Overlap(number=pr.number, title=pr.title, files=shared))
        return overlaps

    def default_branch(self) -> str:
        return self.repo.default_branch
