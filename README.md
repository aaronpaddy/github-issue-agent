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
| `ANTHROPIC_API_KEY` | Anthropic API key (optional if the SDK can find credentials another way) |
| `GITHUB_TOKEN` | Token with `repo` scope on the target repository (not needed when using a GitHub App) |
| `GITHUB_APP_ID` | Optional. GitHub App ID, to act as a bot (see below) |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Optional. Path to the app's `.pem` private key |
| `GITHUB_REPO` | Target repository, e.g. `owner/name` |
| `CLAUDE_MODEL` | Model ID (default `claude-sonnet-5`) |
| `MAX_ATTEMPTS` | Max implement/validate cycles per issue (default `2`) |
| `MAX_BUDGET_USD` | Hard spend cap per run in USD (default `1.00`) |
| `WORKSPACES_DIR` | Where per-run clones and virtualenvs are created (default `workspaces`) |

### Running as a GitHub App

By default the agent acts with `GITHUB_TOKEN`, so pull requests show the token's owner as the author. To have PRs, comments, and commits show up as `<your-app>[bot]` instead, register a GitHub App and set `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY_PATH`. The agent then authenticates as the app's installation on the target repository, and `GITHUB_TOKEN` is ignored.

The app needs these repository permissions: **Contents** (read and write), **Pull requests** (read and write), **Issues** (read and write), and **Metadata** (read). It must be installed on each repository the agent works on. Keep the downloaded `.pem` private key outside the repository.

## Usage

Give the CLI an issue number on the configured repository:

```bash
python -m src.cli --issue 12
python -m src.cli --issue 12 --repo owner/name   # override GITHUB_REPO
```

Use `--dry-run` to run the full clone, install, investigate, implement, and validate loop without pushing a branch, opening a PR, or commenting on the issue. The workspace is kept so you can inspect the diff:

```bash
python -m src.cli --issue 12 --dry-run
```

Each run:

1. Clones the repository fresh from GitHub into `workspaces/<job-id>/repo`, so local uncommitted work never leaks into a PR.
2. Creates a virtualenv in `workspaces/<job-id>/venv` (outside the repo, so it can't be committed) and installs the project with its `dev` extras, so checks run against the project's own dependencies.
3. Runs every check once before the agent starts to capture a baseline.
4. Runs the agent loop. After each attempt the checks run again and are compared with the baseline.

### Cost controls

Every model call is priced from the API's reported token usage, and a run stops before its next call once `MAX_BUDGET_USD` is spent, escalating instead of continuing. The actual spend is printed at the end of each run. To keep runs cheap:

- Prompt caching is enabled, so the conversation re-sent on each loop step is billed at a fraction of the normal input price.
- Extended thinking is off.
- Tool output is capped, and `read_file` accepts line ranges, so large files don't bloat the conversation.

### Validation is baseline-relative

Real repositories rarely start clean, so requiring every check to pass would make most repos impossible. Instead, a check that passed at baseline must still pass, and a check that was already failing may keep failing as long as the agent introduces no *new* issue. Issues are compared with line numbers stripped, so code that only shifts down a few lines isn't counted as a regression. A run that leaves the repository unchanged is rejected, since passing on untouched code proves nothing.

`black` reports at file granularity, so new mis-formatted code inside a file that was already unformatted at baseline isn't flagged.

The checks are defined in `src/agent/validator.py` (`DEFAULT_CHECKS`) for a Python project using pytest, ruff, black, and mypy. Adjust them for other repositories.

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
    cost.py         # Token/dollar accounting and the per-run budget
    validator.py    # Baseline-relative pytest/ruff/black/mypy gate
    environment.py  # Builds the target project's virtualenv
    workspace.py    # Workspace interface and the path-jailed local implementation
    tools/          # The nine tools and the tool registry
  github/
    client.py       # Read issues, comment, open pull requests (PyGithub)
    git_ops.py      # Clean clone, commit, and push (token via env, never in URLs)
  config.py         # Environment-driven settings
  cli.py            # Command-line entry point
tests/
```

## Status and limitations

Early-stage. The agent runs from the command line, one issue at a time.

- Execution is not sandboxed. Tests and linters run directly on the host, so only run it against repositories you trust, and supervise it. A containerized workspace is the intended next step.
- There is no webhook service or job queue yet; issues are processed one at a time via the CLI.
- Validation commands are configured in code rather than discovered per repository.
- Only Python projects installable with `pip install -e ".[dev]"` are supported.
