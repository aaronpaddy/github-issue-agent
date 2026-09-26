# github-issue-agent

An autonomous agent that takes a GitHub issue, investigates the repository, implements a fix, validates it, and opens a pull request. A human always reviews and merges.

Unlike a single prompt-to-patch call, the agent works in a loop: it reads code, edits files, runs the repository's real test/lint/type checks, and uses failures to revise its approach. It only opens a PR once the change passes objective validation. If it can't get there within a fixed number of attempts, it comments on the issue explaining what it tried instead of opening a broken PR.

## How it works

```
GitHub issue
     │
     ▼
Fetch issue + comments ──► Copy repo into a fresh working directory
     │
     ▼
┌─► Agent loop (reason → act → observe) using a fixed set of tools
│        │
│        ▼
│   Model stops calling tools ("I think it's done")
│        │
│        ▼
│   Validation runs (pytest, ruff, black, mypy), outside the model's control
│        │
│   fail │ pass
└────────┤   │
 retry   │   ▼
 (capped)│  Create branch ─► commit ─► push ─► open PR
         ▼
   Attempts exhausted: comment on the issue, no PR
```

### Design decisions

- **Validation is authoritative.** The model can run tests while it works, but whether a change is good enough for a PR is decided by the application running the validation suite itself, never by the model's own report.
- **Hard retry cap.** The attempt limit is enforced in the loop's control flow, so the model cannot request extra tries.
- **Constrained tools, not a shell.** The model interacts with the repo only through nine tools. `run_command` accepts only an allowlist (`pytest`, `ruff`, `black`, `mypy`, and read-only `git` subcommands) and rejects shell metacharacters.
- **Path-jailed workspace.** Every file operation resolves inside the workspace root, so `../` traversal is rejected.
- **Structured edits.** `edit_file` replaces an exact, unique excerpt rather than rewriting whole files, which keeps changes small and reviewable.
- **Never merges.** The agent opens pull requests only. It does not merge, force-push, or touch protected branches.

### Tools

| Tool | Purpose |
|---|---|
| `list_files` | List files under a directory |
| `read_file` | Read a file with line numbers |
| `search_code` | Grep-style search across Python sources |
| `edit_file` | Replace an exact, unique excerpt in a file |
| `create_file` | Create a new file |
| `run_tests` | Run pytest and return structured output |
| `run_command` | Run an allowlisted validation command |
| `git_diff` | Show the current uncommitted diff |
| `git_status` | Show the working tree status |

## Getting started

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env`:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | API key for the LLM provider |
| `GITHUB_TOKEN` | Token with `repo` scope on the target repository |
| `GITHUB_REPO` | Target repository, e.g. `owner/name` |
| `CLAUDE_MODEL` | Model ID (default `claude-sonnet-5`) |
| `MAX_ATTEMPTS` | Max implement/validate cycles per issue (default `3`) |

## Usage

Point the CLI at a local clone of the target repository and an issue number:

```bash
python -m src.cli --clone /path/to/local/clone --issue 12
```

Use `--dry-run` to run the full investigate, implement, and validate loop without pushing a branch, opening a PR, or commenting on the issue:

```bash
python -m src.cli --clone /path/to/local/clone --issue 12 --dry-run
```

Each run works on a disposable copy of the clone (`.issue-agent-work-<job-id>` beside it), so your checkout is never modified.

The validation commands are currently hardcoded in `src/agent/validator.py` for a Python project using pytest, ruff, black, and mypy. Adjust `DEFAULT_CHECKS` for other repositories.

## Development

```bash
pytest -q
ruff check src tests
mypy src
```

The test suite uses a scripted fake LLM, so it makes no API calls and covers the workspace path jail, the tool allowlist, the state machine, and the loop's retry and escalation behavior.

## Project layout

```
src/
  agent/
    core.py         # AgentLoop: the reason/act/observe loop and retry cap
    llm.py          # Thin wrapper over the Anthropic SDK's tool-use API
    state.py        # Job, AgentStatus, Attempt, ValidationOutcome
    validator.py    # Runs pytest/ruff/black/mypy and reports pass/fail
    workspace.py    # Workspace interface and the path-jailed local implementation
    tools/          # The nine tools and the tool registry
  github/
    client.py       # Read issues, comment, open pull requests (PyGithub)
    git_ops.py      # Branch, commit, and push
  config.py         # Environment-driven settings
  cli.py            # Command-line entry point
tests/
```

## Status and limitations

Early-stage. The agent runs from the command line against a local clone.

- Execution is not sandboxed. Tests and linters run directly on the host, so only run it against repositories you trust, and supervise it. A containerized workspace is the intended next step.
- There is no webhook service or job queue yet; issues are processed one at a time via the CLI.
- Validation commands are configured in code rather than discovered per repository.
