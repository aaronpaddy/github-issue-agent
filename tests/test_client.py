from types import SimpleNamespace

import pytest
from github.GithubException import GithubException, UnknownObjectException

from src.github.client import GitHubClient


def make_client(repo) -> GitHubClient:
    client = GitHubClient.__new__(GitHubClient)  # skip the network handshake in __init__
    client.repo = repo
    return client


def pr(number, ref, files=(), title="t"):
    return SimpleNamespace(
        number=number,
        title=title,
        html_url=f"https://example/pull/{number}",
        head=SimpleNamespace(ref=ref),
        get_files=lambda: [SimpleNamespace(filename=f) for f in files],
    )


def test_finds_an_open_agent_pr_for_the_issue():
    repo = SimpleNamespace(get_pulls=lambda state: [pr(1, "feature/x"), pr(7, "issue-agent/issue-4")])
    found = make_client(repo).find_open_agent_pr(4)
    assert found is not None
    assert found.number == 7


def test_finds_it_on_a_suffixed_branch_too():
    repo = SimpleNamespace(get_pulls=lambda state: [pr(9, "issue-agent/issue-4-a1b2c3")])
    assert make_client(repo).find_open_agent_pr(4) is not None


def test_issue_4_does_not_match_issue_40():
    repo = SimpleNamespace(get_pulls=lambda state: [pr(9, "issue-agent/issue-40")])
    assert make_client(repo).find_open_agent_pr(4) is None


def test_branch_name_is_plain_when_free_and_suffixed_when_taken():
    def get_branch(name):
        if name == "issue-agent/issue-4":
            return object()
        raise UnknownObjectException(404, {}, {})

    taken = make_client(SimpleNamespace(get_branch=get_branch))
    assert taken.branch_name_for(4, "abcdef123") == "issue-agent/issue-4-abcdef"
    assert taken.branch_name_for(5, "abcdef123") == "issue-agent/issue-5"


def test_overlapping_prs_reports_only_shared_files():
    repo = SimpleNamespace(
        get_pulls=lambda state: [
            pr(8, "a", files=["tests/test_settle.py", "src/x.py"], title="Add tests"),
            pr(9, "b", files=["docs/other.md"]),
        ]
    )
    overlaps = make_client(repo).overlapping_prs(["tests/test_settle.py", "README.md"])
    assert [(o.number, o.files) for o in overlaps] == [(8, ["tests/test_settle.py"])]


def test_draft_falls_back_to_a_normal_pr_when_drafts_are_unsupported():
    calls = []

    def create_pull(**kwargs):
        calls.append(kwargs)
        if kwargs.get("draft"):
            raise GithubException(422, {"message": "Draft pull requests are not supported"}, {})
        return SimpleNamespace(html_url="https://example/pull/1")

    client = make_client(SimpleNamespace(create_pull=create_pull))
    url = client.open_pull_request("b", "Fix it", "body", base="main", draft=True)

    assert url == "https://example/pull/1"
    assert calls[1]["title"] == "[Needs review] Fix it"
    assert not calls[1].get("draft")


def test_other_errors_are_not_swallowed():
    def create_pull(**kwargs):
        raise GithubException(500, {}, {})

    with pytest.raises(GithubException):
        make_client(SimpleNamespace(create_pull=create_pull)).open_pull_request("b", "t", "x", draft=True)


def test_get_issue_reports_state_and_whether_it_is_a_pull_request():
    def get_issue(number):
        return SimpleNamespace(
            number=number,
            title="t",
            body=None,
            state="closed",
            pull_request=None,
            get_comments=lambda: [SimpleNamespace(body="hi")],
        )

    issue = make_client(SimpleNamespace(get_issue=get_issue)).get_issue(4)
    assert issue.state == "closed"
    assert not issue.is_pull_request
    assert issue.body == ""
    assert issue.comments == ["hi"]
