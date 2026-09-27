# Evals

An agent that opens pull requests needs a number that answers "how often is this right?", not a handful of good demos. This is that number.

## What it measures

Each case is an issue, plus the exact commit of a small Python library (`aaronpaddy/expense-splitter`) it applies to. The harness runs the **real pipeline** on it (triage, the Docker sandbox, the agent loop, the policy) against a fake GitHub, so nothing is posted and no PR is opened. Then it judges the result **independently of the agent**:

| Kind of case | The agent should | Passing means |
|---|---|---|
| **Fix** (bug, feature, tests) | make a validated change | the outcome is right **and** hidden tests it never saw pass **and** the tests it wrote catch deliberate bugs |
| **Vague issue** | ask for clarification, not guess | it asked |
| **Already solved** | report that no change is needed | it said so |
| **Reply** | resume after a person answers its question | it made a correct fix from the thread |

Wrongly holding back on a solvable issue counts as a failure, so the agent can't score well by refusing everything.

### How a fix is judged

- **Hidden tests.** Each fix case has tests written from the issue, kept out of the repository the agent sees. They run in the same sandbox on the agent's final change.
- **Mutation testing.** "Add tests for X" can't be checked by running the tests, since they'd pass on correct code. Instead the harness plants deliberate bugs in the code (swap `min` for `max`, flip sender and receiver, reverse a sort) and checks that the tests the agent wrote **fail**. Tests that can't notice a planted bug don't count.
- **Tests added.** For fixes it also checks that the agent added tests at all.

### Are the cases themselves fair?

Every fix case ships a `reference.patch`, a known-good solution. `python -m src.evals validate` proves each case is a fair test: the hidden tests **fail** on the buggy base commit, **pass** with the reference fix, the reference doesn't break the existing suite, and the planted bugs are catchable. It makes no model calls and costs nothing.

## Running it

```bash
python -m src.evals list                       # the cases
python -m src.evals validate                   # check the cases are fair (free)
python -m src.evals run --budget 1.5           # run all cases, stop starting new ones past $1.50
python -m src.evals run --case vague-currencies --repeat 3
python -m src.evals run --jobs 3               # run three cases at once
```

Needs the same setup as the agent (an Anthropic key, GitHub credentials to clone, Docker). Results are written to `evals/results/<timestamp>.json`, and the latest report to [`RESULTS.md`](RESULTS.md).

A model is not deterministic, so one run of one case is an anecdote. Use `--repeat` to see how stable a result is, and treat small differences between runs as noise.

## Adding a case

Create `evals/cases/<id>/case.json` (issue text, pinned commit, what to expect), and for a fix add a `hidden_test.py` and a `reference.patch`, then run `validate`. Pin `base_sha` to a commit where the problem exists, so the case stays valid as the repository moves on.

## Limits, stated plainly

- The cases are small and written by the author of the agent, against one small library. They are a regression suite and a sanity check, not a benchmark, and the pass rate says nothing about how it would do on a large or unfamiliar codebase.
- Hidden tests can be stricter or looser than what a maintainer would accept, and the agent may pick a reasonable design the tests didn't anticipate.
- The vague cases were tuned against while building the triage step, so they're partly a training set. A fair test of that step needs issues written by someone else.
