"""GitHubClient: all GitHub-side operations (read issue, comment, open PR).

Deliberately separate from the Agent Core (src/agent/*), which has no
knowledge GitHub exists. This client is what the CLI uses to turn an
AgentResult into a real PR or a real comment.
"""

from __future__ import annotations

from dataclasses import dataclass

from github.Repository import Repository

from github import Github


@dataclass
class IssueContext:
    number: int
    title: str
    body: str
    comments: list[str]


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
    ) -> str:
        pr = self.repo.create_pull(title=title, body=body, head=branch, base=base)
        return pr.html_url

    def default_branch(self) -> str:
        return self.repo.default_branch
