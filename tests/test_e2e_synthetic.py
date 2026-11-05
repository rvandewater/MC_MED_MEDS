"""End-to-end test of the full ETL on a tiny, fully synthetic MC-MED-shaped dataset.

MC-MED is credentialed-access (no open demo), so CI cannot download it. Instead this test writes a
handful of made-up rows with the column layout documented on PhysioNet
(https://physionet.org/content/mc-med/1.0.1/) and runs ``meds-extract-run`` on them with
``do_download=false``. No values here come from the real dataset.
"""

import subprocess
from pathlib import Path

import polars as pl
import pytest

# Three synthetic patients (MRN 1-3; MEDS-Extract needs >= 1 per train/tuning/held_out split).
# Patient 1 has two visits, the second admitted. Patient 3 uses space-separated timestamps.
VISITS = """\
MRN,CSN,Visit_no,Visits,Age,Gender,Race,Ethnicity,Means_of_arrival,Triage_Temp,Triage_HR,Triage_RR,\
Triage_SpO2,Triage_SBP,Triage_DBP,Triage_acuity,CC,ED_dispo,Hours_to_next_visit,\
Dispo_class_next_visit,ED_LOS,Hosp_LOS,DC_dispo,Payor_class,Admit_service,Dx_ICD9,Dx_ICD10,Dx_name,\
Arrival_time,Roomed_time,Dispo_time,Admit_time,Departure_time
1,101,1,2,40,F,Race A,Non-Hispanic,Walk-in,37.0,80,16,98,120,80,3,Synthetic complaint A,Discharge,\
100.0,Discharge,4.0,0.25,Home,Payor A,,111.1,A00.0,Synthetic dx A,2200-01-01T10:00:00Z,\
2200-01-01T10:30:00Z,2200-01-01T13:00:00Z,,2200-01-01T14:00:00Z
1,102,2,2,40,F,Race A,Non-Hispanic,Ambulance,38.5,110,22,93,100,60,2,Synthetic complaint B,Admit,,,\
6.0,3.0,Home,Payor A,Medicine,222.2,B00.0,Synthetic dx B,2200-01-05T18:00:00Z,2200-01-05T18:10:00Z,\
2200-01-05T22:00:00Z,2200-01-05T22:30:00Z,2200-01-06T00:00:00Z
2,201,1,1,65,M,Race B,Hispanic,Walk-in,36.8,72,14,99,135,85,4,Synthetic complaint C,Discharge,,,2.5,\
0.1,Home,Payor B,,333.3,C00.0,Synthetic dx C,2250-06-01T08:00:00Z,2250-06-01T08:20:00Z,\
2250-06-01T10:00:00Z,,2250-06-01T10:30:00Z
3,301,1,1,30,M,Race C,Non-Hispanic,Walk-in,37.2,90,18,97,125,75,5,Synthetic complaint D,Discharge,,,\
1.0,0.05,Home,Payor C,,444.4,D00.0,Synthetic dx D,2300-03-01 12:00:00,2300-03-01 12:05:00,\
2300-03-01 12:45:00,,2300-03-01 13:00:00
"""

ORDERS = """\
CSN,Order_time,Order_type,Procedure_name,Procedure_ID,First_admin_time,Result_time
101,2200-01-01T10:45:00Z,Lab,Synthetic panel,P1,,2200-01-01T11:30:00Z
102,2200-01-05T18:20:00Z,Medication,Synthetic drug,P2,2200-01-05T18:40:00Z,
201,2250-06-01T08:30:00Z,Imaging,Synthetic study,P3,,2250-06-01T09:30:00Z
"""

LABS = """\
CSN,Order_time,Result_time,Display_name,Abnormal,Component_name,Component_result,Component_value,Component_units,Component_abnormal,Component_nml_low,Component_nml_high
101,2200-01-01T10:45:00Z,2200-01-01T11:30:00Z,Synthetic panel,N,Analyte X,5.1,5.1,mmol/L,N,3.5,5.5
101,2200-01-01T10:45:00Z,2200-01-01T11:30:00Z,Synthetic panel,Y,Analyte Y,Positive,,,Y,,
"""

RADS = """\
CSN,Order_time,Result_time,Study,Impression
201,2250-06-01T08:30:00Z,2250-06-01T09:30:00Z,Synthetic study,Synthetic impression text.
"""

NUMERICS = """\
CSN,Source,Measure,Value,Time
101,Monitor,HR,81,2200-01-01T10:31:00Z
101,Chart,Temp,98.6,2200-01-01T10:35:00Z
102,Monitor,SpO2,94,2200-01-05T18:15:00Z
"""

PMH = """\
MRN,Noted_date,CodeType,Code,Desc10,CCS,DescCCS
1,2199-06-01,ICD10,Z00.0,Synthetic history,1,Synthetic CCS
"""

MEDS = """\
MRN,Med_ID,NDC,Name,Generic_name,Med_class,Med_subclass,Active,Entry_date,Start_date,End_date
2,9001,00000-0000-00,Synthetic med 10 MG,synthetic med,Class A,Subclass A,Y,2250-01-01,2249-12-15,2250-03-01
"""

TABLES = {
    "visits": VISITS,
    "orders": ORDERS,
    "labs": LABS,
    "rads": RADS,
    "numerics": NUMERICS,
    "pmh": PMH,
    "meds": MEDS,
}


@pytest.fixture(scope="module")
def meds_output(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("mc_med")
    raw_dir = root / "raw"
    raw_dir.mkdir()
    for name, content in TABLES.items():
        (raw_dir / f"{name}.csv").write_text(content)

    output_dir = root / "MEDS_cohort"
    runner_fp = root / "runner.yaml"
    runner_fp.write_text("parallelize:\n  n_workers: 1\n")

    result = subprocess.run(
        [
            "meds-extract-run",
            "spec=MC-MED",
            f"output_dir={output_dir}",
            "do_download=false",
            f"input_dir={raw_dir}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"meds-extract-run failed with exit code {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return output_dir


def test_output_layout(meds_output: Path):
    assert list((meds_output / "data").rglob("*.parquet")), "No MEDS data shards written"
    for expected in ("dataset.json", "codes.parquet", "subject_splits.parquet"):
        assert (meds_output / "metadata" / expected).exists(), f"Missing metadata/{expected}"


def test_events(meds_output: Path):
    df = pl.concat([pl.read_parquet(fp) for fp in (meds_output / "data").rglob("*.parquet")])
    codes = set(df["code"].to_list())

    assert set(df["subject_id"].unique().to_list()) == {1, 2, 3}

    expected_codes = {
        "GENDER//F",
        "ED_ARRIVAL//Walk-in",
        "ED_DIAGNOSIS//ICD10CM//D00.0",  # space-separated timestamp format
        "TRIAGE//HR//bpm",
        "TRIAGE//ESI//3",
        "ED_DEPARTURE",
        "ED_DIAGNOSIS//ICD10CM//A00.0",
        "HOSPITAL_ADMISSION//Medicine",
        "HOSPITAL_DISCHARGE//Home",
        "ORDER//Lab//P1",
        "MEDICATION_ADMINISTRATION//P2",
        "LAB//Analyte X//mmol/L",
        "LAB//Analyte Y//UNK",
        "RADIOLOGY//Synthetic study",
        "VITAL//HR//Monitor",
        "VITAL//Temp//Chart",
        "PMH//ICD10//Z00.0",
        "HOME_MEDICATION//9001",
        "HOME_MEDICATION_END//9001",
    }
    assert expected_codes <= codes, f"Missing codes: {sorted(expected_codes - codes)}"

    # CSN-only tables got their subject from the visits join.
    vital_subjects = df.filter(pl.col("code").str.starts_with("VITAL//"))["subject_id"].unique()
    assert sorted(vital_subjects.to_list()) == [1]

    # Only the admitted visit produces an admission event.
    assert df.filter(pl.col("code").str.starts_with("HOSPITAL_ADMISSION//")).height == 1

    # Discharge = arrival + Hosp_LOS days (visit 102: 2200-01-05 18:00 + 3 days).
    discharge = df.filter((pl.col("code") == "HOSPITAL_DISCHARGE//Home") & (pl.col("csn") == 102))[
        "time"
    ].to_list()
    assert [str(t) for t in discharge] == ["2200-01-08 18:00:00"]

    # Numeric labs parse; non-numeric results keep only text_value.
    lab_y = df.filter(pl.col("code") == "LAB//Analyte Y//UNK")
    assert lab_y["numeric_value"].to_list() == [None]
    assert lab_y["text_value"].to_list() == ["Positive"]


def test_code_descriptions(meds_output: Path):
    codes = pl.read_parquet(meds_output / "metadata" / "codes.parquet")
    desc = dict(zip(codes["code"].to_list(), codes["description"].to_list()))
    assert desc.get("ED_DIAGNOSIS//ICD10CM//A00.0") == "Synthetic dx A"
    assert desc.get("ORDER//Lab//P1") == "Synthetic panel"
    assert desc.get("HOME_MEDICATION//9001") == "Synthetic med 10 MG"
