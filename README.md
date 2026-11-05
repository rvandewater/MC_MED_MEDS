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

Raw data is downloaded automatically per the `sources:` block of the packaged configuration; set any
required credentials as environment variables (see below). To run against pre-staged raw data
instead, pass `do_download=false input_dir=/path/to/raw/data`.

The output directory will contain the extracted MEDS dataset (`data/` and `metadata/`, per the
[MEDS standard](https://medical-event-data-standard.github.io/)), with dataset name and version
provenance stamped in `metadata/dataset.json` automatically.

## Dataset access

[MC-MED v1.0.1](https://physionet.org/content/mc-med/1.0.1/) (DOI
[10.13026/wvyw-g663](https://doi.org/10.13026/wvyw-g663)) is a **credentialed-access** PhysioNet
dataset: you need a credentialed PhysioNet account, the required CITI training, and a signed DUA
for the project. Export your PhysioNet credentials before running the ETL:

```bash
export PHYSIONET_USERNAME=...
export PHYSIONET_PASSWORD=...
meds-extract-run spec=MC-MED output_dir=/path/to/output download_dest_dir=/path/to/raw
```

Only the tabular CSVs the ETL reads are downloaded (`visits`, `orders`, `labs`, `rads`,
`numerics`, `meds`, `pmh`); the WFDB waveform tree is skipped. If you already have the files,
use `do_download=false input_dir=/path/to/mc-med/1.0.1`.

## What gets extracted

`subject_id` is MC-MED's `MRN`. Tables keyed only by visit (`CSN`) are joined to `visits.csv` to
obtain it, and every visit-scoped event carries its visit's `CSN` in an extra `csn` column (useful
for linking back to the raw tables and the waveforms).

| Source table   | Event(s)                           | Code pattern                                                                                          | Time                             | Value(s)                                     |
| -------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------- | -------------------------------------------- |
| `visits.csv`   | Demographics (static)              | `GENDER//…`, `RACE//…`, `ETHNICITY//…`                                                                | —                                |                                              |
|                | Arrival, age, payor                | `ED_ARRIVAL//{Means_of_arrival}`, `AGE`, `PAYOR//{Payor_class}`                                       | `Arrival_time`                   | `AGE`: perturbed age (years)                 |
|                | Triage                             | `TRIAGE//ESI//{acuity}`, `TRIAGE//CHIEF_COMPLAINT//{CC}`, `TRIAGE//{HR,RR,SPO2,SBP,DBP,TEMP}//{unit}` | `Arrival_time`                   | vitals as `numeric_value`                    |
|                | ED course                          | `ED_ROOMED`, `ED_DISPOSITION//{ED_dispo}`, `HOSPITAL_ADMISSION//{Admit_service}`, `ED_DEPARTURE`      | respective timestamp             | `ED_DEPARTURE`: ED LOS (hours)               |
|                | Primary ED diagnosis               | `ED_DIAGNOSIS//ICD10CM//{code}`, `ED_DIAGNOSIS//ICD9CM//{code}`                                       | `Departure_time`                 |                                              |
|                | Hospital discharge                 | `HOSPITAL_DISCHARGE//{DC_dispo}`                                                                      | `Arrival_time + Hosp_LOS`        | Hospital LOS (days)                          |
| `orders.csv`   | Orders; medication administrations | `ORDER//{Order_type}//{Procedure_ID}`, `MEDICATION_ADMINISTRATION//{Procedure_ID}`                    | `Order_time`, `First_admin_time` |                                              |
| `labs.csv`     | Lab component results              | `LAB//{Component_name}//{Component_units}`                                                            | `Result_time`                    | `numeric_value` (parsed), `text_value` (raw) |
| `rads.csv`     | Radiology reports                  | `RADIOLOGY//{Study}`                                                                                  | `Result_time`                    | `text_value`: impression                     |
| `numerics.csv` | Charted / monitor vitals           | `VITAL//{Measure}//{Chart,Monitor}`                                                                   | `Time`                           | `numeric_value`                              |
| `pmh.csv`      | Past medical history               | `PMH//{CodeType}//{Code}`                                                                             | `Noted_date`                     |                                              |
| `meds.csv`     | Home medication list               | `HOME_MEDICATION//{Med_ID}`, `HOME_MEDICATION_END//{Med_ID}`                                          | `Entry_date`, `End_date`         |                                              |

Code descriptions (ICD-10 names, order names, medication names, PMH descriptions) are written to
`metadata/codes.parquet`. A missing unit, arrival mode, order type, admit service or discharge
disposition becomes `UNK` rather than dropping the row.

### Modelling choices and caveats

- **Timestamps** are shifted per patient into 2150–2350 by the dataset authors. They are parsed
  leniently against several ISO-8601 layouts. Rows matching none are dropped, and MEDS-Extract logs
  a WARNING with the count, so check the logs of your first full run.
- **Triage** has no timestamp of its own and is placed at ED arrival.
- **Age** is perturbed (±2 years, clipped to 18–90) and there is no birth date, so it is an `AGE`
  measurement at each arrival rather than a `MEDS_BIRTH` event.
- **Hospital discharge** has no timestamp in MC-MED. It is reconstructed as
  `Arrival_time + Hosp_LOS` days, assuming `Hosp_LOS` covers the whole encounter, ED stay included.
- **Home medications** are placed at `Entry_date` (when they were recorded), not `Start_date`, so
  they never appear before the information was available.
- **Not extracted:** waveforms and `waveform_summary.csv`, the next-visit outcome columns
  (`Hours_to_next_visit`, `Dispo_class_next_visit`; these follow from the next visit's events), and
  the lab panel-level fields (`Display_name`, `Abnormal`, normal ranges).
- **Splits:** MEDS-Extract draws its own random subject-level splits. To use MC-MED's provided
  `split_random_*` / `split_chrono_*` files instead, convert them to a `{split: [subject_ids]}`
  JSON and pass it via `external_splits_json_fp`.

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

## Development

```bash
uv sync --group dev --group tests
uv run pre-commit install
uv run pytest tests/ -v
```

The test suite validates offline that the packaged MESSY file is registered and parses, then runs
the full pipeline end to end on a tiny, entirely synthetic dataset with MC-MED's documented column
layout (`tests/test_e2e_synthetic.py`). No real MC-MED data is used in CI.

To publish: set up a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/) for this
repository (see [the template's
instructions](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template#pypi) for a
walkthrough), then push a version tag (e.g. `git tag 0.1.0 && git push origin 0.1.0`) — the release
workflow builds, publishes to PyPI, and creates a GitHub release. Optionally archive releases on
[Zenodo](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content)
for a citable DOI, and update `CITATION.cff` accordingly.

This repository was generated from the
[ETL_MEDS_Template](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template) copier
template; run `uvx copier update` (with a clean git tree) to pull in template improvements.
