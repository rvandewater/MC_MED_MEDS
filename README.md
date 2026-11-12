# MC-MED MEDS Extraction ETL

[![PyPI - Version](https://img.shields.io/pypi/v/MC-MED-MEDS)](https://pypi.org/project/MC-MED-MEDS/)
[![tests](https://github.com/rvandewater/MC_MED_MEDS/actions/workflows/tests.yaml/badge.svg)](https://github.com/rvandewater/MC_MED_MEDS/actions/workflows/tests.yaml)
[![code-quality](https://github.com/rvandewater/MC_MED_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/MC_MED_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11--3.13-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/MC_MED_MEDS#license)

An ETL to extract the MC-MED (Multimodal Clinical Monitoring in the Emergency Department) dataset into the MEDS format. Built on [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract).

## Usage

```bash
pip install MC-MED-MEDS
meds-extract-run spec=MC-MED output_dir=/path/to/output
```

This one command runs the ETL and then, as its last step, fills the official ICD titles and parents
into `metadata/codes.parquet` with [suMEDS](https://github.com/rvandewater/SumMEDS) (installed with
this package; see [Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). For
the plain ETL, add `skip_stages=[sumeds_enrich]`.

Raw data is downloaded automatically per the `sources:` block of the packaged configuration; set any
required credentials as environment variables (see below). To run against pre-staged raw data
instead, pass `do_download=false input_dir=/path/to/raw/data`.

The output directory will contain the extracted MEDS dataset (`data/` and `metadata/`, per the
[MEDS standard](https://medical-event-data-standard.github.io/)), with dataset name and version
provenance, the license and the dataset's PhysioNet page stamped in `metadata/dataset.json`
automatically.

## Dataset access

[MC-MED v1.0.1](https://physionet.org/content/mc-med/1.0.1/) (DOI
[10.13026/wvyw-g663](https://doi.org/10.13026/wvyw-g663)) is a **credentialed-access** PhysioNet
dataset: you need a credentialed PhysioNet account, the required CITI training, and a signed DUA
for the project. Export your PhysioNet credentials before running the ETL:

```bash
export DATASET_DOWNLOAD_USERNAME=... # your PhysioNet username
export DATASET_DOWNLOAD_PASSWORD=... # and password
meds-extract-run spec=MC-MED output_dir=/path/to/output download_dest_dir=/path/to/raw
```

Only the tabular CSVs the ETL reads are downloaded (`visits`, `orders`, `labs`, `rads`,
`numerics`, `meds`, `pmh`); the WFDB waveform tree is skipped. If you already have the files,
use `do_download=false input_dir=/path/to/mc-med/1.0.1`.

## What gets extracted

`subject_id` is MC-MED's `MRN`. Tables keyed only by visit (`CSN`) are joined to `visits.csv` to
obtain it, and every visit-scoped event carries its visit's `CSN` in an extra `csn` column (useful
for linking back to the raw tables and the waveforms).

| Source table   | Event(s)                           | Code pattern                                                                                                   | Time                                                 | Value(s)                                     |
| -------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------- |
| `visits.csv`   | Demographics (static)              | `GENDER//…`, `RACE//…`, `ETHNICITY//…`                                                                         | —                                                    |                                              |
|                | Arrival, age, payor                | `ED_ARRIVAL//{Means_of_arrival}`, `AGE`, `PAYOR//{Payor_class}`                                                | `Arrival_time`                                       | `AGE`: perturbed age (years)                 |
|                | Triage                             | `TRIAGE//ESI//{acuity}`, `TRIAGE//CHIEF_COMPLAINT//{complaint}`, `TRIAGE//{HR,RR,SPO2,SBP,DBP,TEMP}//{unit}`   | `Arrival_time`                                       | vitals as `numeric_value`                    |
|                | ED course                          | `ED_ROOMED`, `ED_DISPOSITION//{ED_dispo}`, `HOSPITAL_ADMISSION//{Admit_service}`, `ED_DEPARTURE`               | respective timestamp                                 | `ED_DEPARTURE`: ED LOS (hours)               |
|                | ED diagnoses                       | `ED_DIAGNOSIS//ICD10CM//{code}`, `ED_DIAGNOSIS//ICD9CM//{code}` (dotted)                                       | `Departure_time` (else `Dispo_time`)                 | first ICD-10 code: `text_value` = `Dx_name`  |
|                | Hospital discharge                 | `HOSPITAL_DISCHARGE//{DC_dispo}` (admitted visits only)                                                        | `Arrival_time + Hosp_LOS`, no earlier than departure | Hospital LOS (days)                          |
|                | In-hospital death                  | `MEDS_DEATH` (`DC_dispo` starting with `Expired`)                                                              | as hospital discharge (else ED departure)            |                                              |
| `orders.csv`   | Orders; medication administrations | `ORDER//{Order_type}//{Procedure_ID}`, `MEDICATION_ADMINISTRATION//{Procedure_ID}` (`Procedure_name` if no ID) | `Order_time`, `First_admin_time`                     |                                              |
| `labs.csv`     | Lab component results              | `LAB//{Component_name}//{Component_units}`                                                                     | `Result_time`                                        | `numeric_value` (parsed), `text_value` (raw) |
| `rads.csv`     | Radiology reports                  | `RADIOLOGY//{Study}`                                                                                           | `Result_time`                                        | `text_value`: impression                     |
| `numerics.csv` | Charted / monitor vitals           | `VITAL//{Measure}//{Chart,Monitor}`                                                                            | `Time`                                               | `numeric_value`                              |
| `pmh.csv`      | Past medical history               | `PMH//ICD10CM//{code}`, `PMH//ICD9CM//{code}` (dotted)                                                         | `Noted_date`                                         |                                              |
| `meds.csv`     | Home medication list               | `HOME_MEDICATION//{Med_ID}`, `HOME_MEDICATION_END//{Med_ID}`                                                   | `Entry_date`, `End_date`                             |                                              |

Code descriptions (order names, medication names, PMH descriptions) are written to
`metadata/codes.parquet`; official ICD titles and parents come from suMEDS (see
[below](#enriching-code-metadata-with-sumeds)). A missing value in a categorical code component
(race, ethnicity, arrival mode, payor, acuity, order type, unit, admit service, dispositions)
becomes `UNK` rather than dropping the row. The CSVs write these as quoted empty strings (`""`),
which the config treats the same as null.

### Modelling choices and caveats

- **Timestamps** are shifted per patient into 2150–2350 by the dataset authors. They are parsed
  leniently against several ISO-8601 layouts. Rows matching none are dropped, and MEDS-Extract logs
  a WARNING with the count, so check the logs of your first full run. These per-event warnings are
  in `$OUTPUT_DIR/convert_to_MEDS_events/.logs/`, not in the runner's own output. In v1.0.1 every
  timestamp matches the first layout, so every dropped row is one whose time is missing.
- **Triage** has no timestamp of its own and is placed at ED arrival.
- **Age** is perturbed (±2 years, clipped to 18–90) and there is no birth date, so it is an `AGE`
  measurement at each arrival, and there is no `MEDS_BIRTH`.
- **Deaths** are in-hospital only. MC-MED records a death as a hospital discharge disposition
  starting with `Expired`, and no death registry is linked, so deaths after discharge are not
  captured and a subject without `MEDS_DEATH` is not known to be alive. `MEDS_DEATH` is placed at
  the reconstructed hospital discharge time (see below), or at ED departure for the few death
  visits without a `Hosp_LOS`. `MEDS_DEATH` is not always a subject's last event: like the
  discharge time, it is only accurate to about a day, so some in-stay vitals, labs or orders come
  after it (typically a few hours), and the source data has a few patients with a later ED visit or
  a past-medical-history or home-medication entry dated after their death.
- **Several values in one field.** `Dx_ICD10` and `Dx_ICD9` can hold up to 4 codes (joined by
  `", "`), and `CC` up to 8 chief complaints (joined by `","` or `", "`). Each value becomes its own
  event. MEDS-Extract cannot turn one row into a variable number of events, so the config has fixed
  slots (`ed_diagnosis_icd10_1` … `_4`, `triage_chief_complaint_1` … `_8`). Empty slots produce a
  null code and are dropped; the per-event WARNINGs count those as "with null code", which is
  expected. A field with more values than slots would lose the extra ones; MC-MED v1.0.1 has none.
- **ED diagnosis names.** `Dx_name` is the EHR's own name for the visit's diagnosis and varies for
  one ICD-10 code, so it is the `text_value` of the visit's first ICD-10 event rather than a code
  description.
- **Past medical history** codes are written without the dot in `pmh.csv` (`CodeType` `Dx10` /
  `Dx9`). The ETL restores the dot (after the 3rd character, or the 4th for ICD-9 E codes), so past
  and ED diagnoses share one layout, e.g. `E119` → `PMH//ICD10CM//E11.9`.
- **Hospital discharge** has no timestamp in MC-MED. It is reconstructed as
  `Arrival_time + Hosp_LOS` days, assuming `Hosp_LOS` covers the whole encounter, ED stay included.
  `Hosp_LOS` is a whole number of days (0 for about 4% of admissions), so the time is clamped to no
  earlier than ED departure. It is still only accurate to about a day: in-stay vitals, labs or
  orders can occur after it.
- **Home medications** are placed at `Entry_date` (when they were recorded), not `Start_date`, so
  they never appear before the information was available.
- **Not extracted:** waveforms and `waveform_summary.csv`, the next-visit outcome columns
  (`Hours_to_next_visit`, `Dispo_class_next_visit`; these follow from the next visit's events), and
  the lab panel-level fields (`Display_name`, `Abnormal`, normal ranges).
- **Splits:** MEDS-Extract draws its own random subject-level splits. To use MC-MED's provided
  `split_random_*` / `split_chrono_*` files instead, convert them to a `{split: [subject_ids]}`
  JSON and pass it via `external_splits_json_fp`.

### Enriching code metadata with suMEDS

ICD vocabulary work lives in [suMEDS](https://github.com/rvandewater/SumMEDS) rather than in this
ETL. The package ships the rules for it,
[`src/MC_MED_MEDS/sumeds_rules.yaml`](src/MC_MED_MEDS/sumeds_rules.yaml), registered in
suMEDS's `sumeds.rules` entry-point group under the same name as the MEDS-Extract spec, `MC-MED`.
The spec declares suMEDS's `sumeds_enrich` stage after the canonical pipeline
(`etl.post_stages`), so `meds-extract-run spec=MC-MED` enriches the finished
`metadata/codes.parquet` in place, and it stays valid MEDS code metadata. `--rules MC-MED` works
for any suMEDS command. By default the ICD data comes from suMEDS's ontology packs, fetched from
GitHub on first use and cached; no Athena download is needed.

**Was a build enriched?** `metadata/dataset.json` records it under `sumeds_enrichment`:
`"enriched": true`, with the rules, the suMEDS version and the coverage. Enrichment is on by default,
and a failed enrichment (for example, on a server without internet access) does not fail the ETL:
`metadata/codes.parquet` is left as the ETL wrote it, `dataset.json` records `"enriched": false`
with the error, and the exit code is still 0. The warning, with the command that finishes the
enrichment, is in `$OUTPUT_DIR/.logs/pipeline.log`, not on the console. Running `meds-extract-run`
again does not retry it (the stage is marked done); enrich the build afterwards, with the same
rules and options, with:

```bash
suMEDS-enrich /path/to/output
# Without internet access, from a copy of the sumeds-ontologies packs directory:
suMEDS-enrich /path/to/output --ontology-source /path/to/sumeds-ontologies/ontologies --offline
```

Options of the stage are set at run time through `overrides=`; the `++` is needed because the
overrides reach every stage of the pipeline:

```bash
# Skip the enrichment (the plain ETL):
meds-extract-run spec=MC-MED output_dir=/path/to/output skip_stages=[sumeds_enrich]
# Make a failed enrichment fail the run (exit code 1) instead of a warning:
meds-extract-run spec=MC-MED output_dir=/path/to/output "overrides=['++stage_cfg.strict=true']"
# Use a local copy of the packs (or set SUMEDS_ONTOLOGY_SOURCE=/path/to/packs):
meds-extract-run spec=MC-MED output_dir=/path/to/output \
	"overrides=['++stage_cfg.ontology_source=/path/to/sumeds-ontologies/ontologies']"
# Also look codes up in your own OHDSI Athena download:
meds-extract-run spec=MC-MED output_dir=/path/to/output "overrides=['++stage_cfg.athena_csv=/path/to/athena']"
```

**On a cluster whose compute nodes have no internet access**, fill a cache on shared storage once,
from a login node, then run the ETL offline on the compute nodes with the same cache:

```bash
export XDG_CACHE_HOME=/shared/project/cache
# On a login node:
suMEDS-enrich --prefetch --rules MC-MED
# On a compute node:
meds-extract-run spec=MC-MED output_dir=/path/to/output "overrides=['++stage_cfg.offline=true']"
```

The rules validate the `ED_DIAGNOSIS//ICD10CM//…`, `ED_DIAGNOSIS//ICD9CM//…`, `PMH//ICD10CM//…` and
`PMH//ICD9CM//…` codes against suMEDS's versioned ICD-10-CM and ICD-9-CM data (built from the
CDC/NCHS and CMS files). They add the official title where a code has none, and the ICD code
itself and its vocabulary ancestors as `parent_codes`. `parent_names` maps each of those to its
official title, so a past-medical-history code that keeps its `pmh.csv` description still carries
the official one. `parent_codes` also gets the nearest ancestor that is itself a code of the same
family (ED diagnoses and past medical history are linked separately); its description is in its
own row, so it has no `parent_names` entry. The same rules work with
`suMEDS … --rules …` to build a privacy-masked code catalog of the output.

## Summary statistics

[`tests/regression/full_summary_stats.json`](tests/regression/full_summary_stats.json) is a
[meds_summary_stats](https://github.com/Medical-Event-Data-Standard/meds_summary_stats) fingerprint of
the MEDS dataset this ETL builds from the full MC-MED 1.0.1 release with
`meds-extract-run spec=MC-MED`, so its code-metadata coverage includes the suMEDS enrichment. It covers
subject, event and measurement counts, digests of the code vocabulary, per-subject, value and time
distributions, code-metadata coverage, the splits and the schema. It names no code and reports no
cell of fewer than 20 subjects and no minimum or maximum, which is what makes it safe to publish.
Because it is committed next to the ETL, its diff shows what an ETL change did to the output, and
two ETL versions can be compared without access to the data.

Regenerate it in every pull request that changes the output, from a build of that branch:

```bash
# Build the release with this checkout, then summarize it
scripts/summary_stats.sh full --input-dir $RAW_INPUT_DIR
# Or summarize a build of this checkout you already have
scripts/summary_stats.sh full --meds-dir $MEDS_OUTPUT_DIR
```

Both print how the statistics changed against the committed baseline before overwriting it. Add
`--compare` to check a build without writing anything; it exits 1 when
[`tests/regression/policy.yaml`](tests/regression/policy.yaml) flags an error. Rebuilding the same
commit reproduces the baseline exactly, so any finding is a change in the output. CI cannot rebuild the
credentialed release, so the [Summary stats workflow](.github/workflows/summary-stats.yaml) only warns
when a pull request changes `src/` (the config or the suMEDS rules) without regenerating the
baseline. A MEDS-Extract, suMEDS or polars upgrade can also move the output, so check it with
`--compare`.

## How this package works

This is a *config-only* ETL package: its entire substance is
[`src/MC_MED_MEDS/configs/event_configs.yaml`](src/MC_MED_MEDS/configs/event_configs.yaml),
a MESSY (MEDS-Extract Specification Syntax YAML) file declaring where the raw data lives
(`sources:`) and how its rows map to MEDS events. The `MEDS_extract.pipelines` entry point in
`pyproject.toml` registers that file under the name `MC-MED`, and MEDS-Extract's
generic runner (`meds-extract-run`) does everything else: staged download, the canonical extraction
pipeline, and provenance stamping. See the
[MEDS-Extract documentation](https://github.com/mmcdermott/MEDS_extract) for the MESSY format and
runner details.
The MESSY file also declares suMEDS's `sumeds_enrich` stage in `etl.post_stages`, and a second
entry point registers [`src/MC_MED_MEDS/sumeds_rules.yaml`](src/MC_MED_MEDS/sumeds_rules.yaml) with
suMEDS under the same name, which is all the stage needs to enrich the output.

## Development

```bash
uv sync --all-groups
uv run pre-commit install
uv run pytest tests/ -v
```

See [`CONTRIBUTORS.md`](CONTRIBUTORS.md) for the contribution guidelines, the checks, regenerating the
summary-stats baseline, and releasing.

The test suite validates offline that the packaged MESSY file is registered and parses, then runs
the full pipeline end to end on a tiny, entirely synthetic dataset with MC-MED's documented column
layout and the quirks of the real files (`tests/conftest.py`, `tests/test_e2e_synthetic.py`), and
checks that the packaged suMEDS rules match the codes it writes (`tests/test_sumeds_rules.py`) and
that `meds-extract-run spec=MC-MED` enriches them, offline against a tiny synthetic ontology pack, that a
failed enrichment keeps the plain output and can be retried, and that `skip_stages=[sumeds_enrich]` gives the
plain ETL (`tests/test_run_with_enrichment.py`). No real MC-MED data is used in CI.

To release, bump `project.version` in `pyproject.toml` with `uv version --bump minor|patch|major` in a pull
request, merge it into `main`, and push a tag with the new version. The tag push uploads the version to PyPI
(through a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/)) and creates its GitHub Release;
see [`CONTRIBUTORS.md`](CONTRIBUTORS.md#releasing). Optionally archive releases on
[Zenodo](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content)
for a citable DOI, and update `CITATION.cff` accordingly.

This repository was generated from the
[ETL_MEDS_Template](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template) copier
template; run `uvx copier update` (with a clean git tree) to pull in template improvements.
