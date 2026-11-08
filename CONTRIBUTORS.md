# Contributors Guide

Thank you for your interest in contributing to MC-MED-MEDS, the ETL that converts the
[MC-MED](https://physionet.org/content/mc-med/1.0.1/) dataset into [MEDS](https://medical-event-data-standard.github.io/).
The following sections outline the expectations for contributors.

## Good Practice

- Discuss large changes, such as a different code layout or new kinds of events, with the maintainers before
  significant work begins. Code layouts are what downstream users (MEDS-DEV predicates, the suMEDS rules)
  match on.
- Keep commits focused and provide clear commit messages.
- Include tests for new functionality and bug fixes: add made-up rows to the synthetic tables in
  `tests/conftest.py` that reproduce the case, and assert on the events the ETL writes.
- Ensure documentation is updated for user-facing changes: the comments next to the event in the MESSY
  config, and the README's "What gets extracted" table and caveats.
- Never commit, paste or print real MC-MED data. It is credentialed (PhysioNet Credentialed Health Data Use
  Agreement 1.5.0), so tests use made-up data only. Look at the real data only through aggregates, such as
  a [suMEDS](https://github.com/rvandewater/suMEDS) summary or `scripts/summary_stats.sh`, and share no
  count of fewer than 20 subjects.

## Installing the Project for Development

This project uses [`uv`](https://docs.astral.sh/uv/) for environment management. To set up the project for
development, clone the repository, then sync the uv environment and install the pre-commit hook:

```bash
# Create .venv with the project (editable) and the dev and tests dependency groups.
uv sync --all-groups
# Install the Git pre-commit hook so checks run automatically on commit.
uv run pre-commit install
```

`uv sync` installs `MC-MED-MEDS` in editable mode, so edits to the config take effect without reinstalling.
The package registers itself as `MC-MED` with MEDS-Extract (`MEDS_extract.pipelines`) and suMEDS
(`sumeds.rules`) through entry points in `pyproject.toml`; run `uv sync` again after changing those.
Prefixing commands with `uv run` (e.g. `uv run pytest`) runs them inside the project environment without
activating it. Activating the environment is optional; if you prefer to drop the `uv run` prefix, activate
it first:

```bash
# Optional: lets you run `pytest`, `pre-commit` or `meds-extract-run` directly, without `uv run`.
source .venv/bin/activate
```

## Project Layout

This is a config-only ETL package:

- [`src/MC_MED_MEDS/configs/event_configs.yaml`](src/MC_MED_MEDS/configs/event_configs.yaml) is the MESSY
  config: where the raw data lives and how its rows become MEDS events. It is the whole ETL.
- [`src/MC_MED_MEDS/sumeds_rules.yaml`](src/MC_MED_MEDS/sumeds_rules.yaml) holds the suMEDS rules that
  validate the ICD codes and add their titles and parents. Its patterns must match the code layouts of the
  config.
- [`tests/`](tests) holds the synthetic dataset (`conftest.py`) and the tests that run the ETL on it.
- [`scripts/summary_stats.sh`](scripts/summary_stats.sh) and [`tests/regression/`](tests/regression) hold
  the summary-stats baseline of the full release (see below).

## Documentation Standards

Document every mapping decision in a comment next to its event in the MESSY config, and summarize the
user-facing ones in the README. Python code (the tests) uses
[Google style docstrings](https://www.sphinx-doc.org/en/master/usage/extensions/example_google.html).

## Running Checks

Pre-commit should run automatically upon commit (if you have installed the hooks above). You can also run it
directly via:

```bash
uv run pre-commit run --all-files
```

In CI, `mdformat` also formats the bash blocks of Markdown files with `shfmt`. Without `shfmt` installed
locally that step is skipped, so keep shell snippets `shfmt`-clean: no line continuations, and a single
space before a trailing `#` comment.

You can also run the tests. They run offline in under a minute and need no MC-MED data:

```bash
uv run pytest tests/ -v
```

The suite builds the synthetic dataset with `meds-extract-run spec=MC-MED skip_stages=[sumeds_enrich]` once
per session and checks the events it writes (`test_e2e_synthetic.py`). It also checks that the suMEDS rules
match those codes (`test_sumeds_rules.py`) and that `meds-extract-run spec=MC-MED` enriches them against a tiny
synthetic ICD pack, soft-fails without one, and gives the plain ETL with the stage skipped
(`test_run_with_enrichment.py`). Tests and pre-commit checks are run as part of the continuous integration
process, on Python 3.11 to 3.13, so any failures will prevent pull requests from being merged.

## Checking a Change Against the Full Release

CI cannot rebuild the credentialed release. A change that moves the output must regenerate
`tests/regression/full_summary_stats.json` in the same pull request, where you have access to the data:

```bash
# Build the release with this checkout, summarize it, and print how it differs from the baseline.
scripts/summary_stats.sh full --input-dir "$RAW_INPUT_DIR"
# Check a build without writing anything; exits 1 if tests/regression/policy.yaml flags an error.
scripts/summary_stats.sh full --meds-dir "$MEDS_OUTPUT_DIR" --compare
```

The Summary stats workflow warns when a pull request changes `src/` without regenerating the baseline.

## Releasing

The package version is set in `pyproject.toml` (`project.version`). To cut a release, bump it with `uv` in a
pull request and merge that into `main`:

```bash
# Or patch / major; updates pyproject.toml and uv.lock.
uv version --bump minor
git commit -am "Release $(uv version --short)"
```

When a push to `main` carries a version that has no git tag yet, the `Publish Python distribution to PyPI`
workflow ([`.github/workflows/python-build.yaml`](.github/workflows/python-build.yaml)) publishes it to PyPI
as [`MC-MED-MEDS`](https://pypi.org/p/MC-MED-MEDS) and creates the matching tag and GitHub Release (with
generated notes). Pushes that don't change the version only build the package. Don't create release tags by
hand; the workflow creates them.

Publishing uses a PyPI [trusted publisher](https://docs.pypi.org/trusted-publishers/), which must be set up
once before the first release: owner `rvandewater`, repository `MC_MED_MEDS`, workflow `python-build.yaml`,
environment `pypi`. Before the project exists on PyPI, add it as a pending publisher.

Every build stamps the version into `metadata/dataset.json` as `dataset_version: "1.0.1:<version>"` (the MC-MED
release, then this package's version). The summary-stats policy ignores that field, so a version bump alone
needs no new baseline. When a release changes the output, update the pin in
[MEDS-DEV](https://github.com/Medical-Event-Data-Standard/MEDS-DEV)
(`src/MEDS_DEV/datasets/MC-MED/requirements.txt`).
