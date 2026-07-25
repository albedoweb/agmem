# Contributing to agmem

Thanks for taking a look. agmem is early — small, focused contributions land
fastest.

## Setup

```bash
git clone https://github.com/albedoweb/agmem && cd agmem
uv sync
uv run pytest -q        # full suite, ~3s
uvx ruff check src/ tests/
```

The optional hybrid-retrieval tests need the extras (`uv sync --extra hybrid`,
pulls PyTorch ~2 GB); without them they auto-skip — that's fine for most PRs.

## What makes a good PR here

- **Tests first-class.** Every behavior change comes with a test. The suite is
  fast and mock-light on purpose — prefer a real tmp-dir repo (see
  `tests/test_first_run.py`) over mocking internals.
- **Retrieval changes need numbers.** Anything touching ranking (`search.py`,
  indexing weights, tokenization) must be measured — say what you ran and
  include before/after metrics. Unverifiable ranking tweaks won't be merged.
- **Stay on-brand.** agmem's thesis is: local-first, plain inspectable text,
  no required daemon, no heavy deps in the default install. Features that add
  a server, a background process, or a mandatory model download need a very
  strong case (and will likely be asked to become opt-in extras).
- **Small diffs.** One concern per PR.

## Reporting bugs

Include: OS, Python version, `agmem --version`, the command you ran, and the
full stderr. If it's a retrieval-quality issue ("expected file X for query Y"),
include the query, the top-K you got, and what you expected — those reports
are gold.

## License

By contributing you agree your work is licensed under Apache-2.0.
