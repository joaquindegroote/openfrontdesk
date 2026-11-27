# Contributing

Issues and pull requests are welcome. This is how the project is worked on, so a change fits
in without surprises.

## Setting up

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd console && npm install && npm run lint && npm run build
```

The tests need no API key: a scripted model stands in for the LLM. The ones that run two engine
workers need a Postgres in `OFD_TEST_POSTGRES_URL` and are skipped without it.

## What a change needs

- A test, or a measurement. A change in behavior comes with a test that fails without it.
  A change to a prompt or a judge comes with numbers from the evals, before and after, on the
  scenario it's about. [evals/README.md](evals/README.md) has how to run them.
- The whole suite after a prompt change. A rule added for one scenario has broken another
  more than once, and only a full pass shows it.
- Code before prompt. If something must never happen (a booking nobody confirmed, a price
  nobody set), look for a rule, a check or a fixed reply first. The prompt is the last resort.
- A new invariant gets tried on old runs. Checks that read transcripts are run over the
  stored conversations before they're trusted, to count their false alarms.

## Adding things

- A scenario: a YAML file in [`evals/scenarios/`](evals/scenarios/) with a persona, a frozen
  clock and what must be true at the end. Prefer the clinic's final state and phrases checked in
  code over rubric items graded by an LLM.
- A judge case: a short conversation, the action and the verdict a careful person would
  give, in [`evals/judge-cases/`](evals/judge-cases/).
- A booking system: an adapter that implements `BookingBackend` and passes
  [`engine/tests/test_backend_contract.py`](engine/tests/test_backend_contract.py).

## Style

Commits follow [Conventional Commits](https://www.conventionalcommits.org/), with a subject that
says what changed for whoever uses it. `ruff` formats and lints the Python, `eslint` the console.
Docs are written in plain sentences, and numbers come with their sample size.
