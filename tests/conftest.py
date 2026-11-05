"""A tiny, fully synthetic MC-MED-shaped dataset and one ETL run over it, shared by the tests.

MC-MED is credentialed-access (no open demo), so CI cannot download it. Instead this writes a
handful of made-up rows with the column layout documented on PhysioNet
(https://physionet.org/content/mc-med/1.0.1/) and runs ``meds-extract-run`` on them with
``do_download=false``. No values here come from the real dataset.

The rows reproduce the quirks of the real files that the config has to handle:

- missing categorical values written as quoted empty strings (``""``), which are read as empty
  text rather than null;
- several diagnosis codes in one ``Dx_ICD10`` / ``Dx_ICD9`` field (joined by ", ") and several
  chief complaints in one ``CC`` field (joined by "," or ", ");
- whole-day ``Hosp_LOS``, including 0 for an admission whose ED departure is on the next day;
- in-hospital deaths, recorded only as an "Expired" discharge disposition;
- a visit without a departure time;
- orders without a ``Procedure_ID``;
- past-medical-history codes without the dot, labelled ``Dx10`` / ``Dx9``.
"""

import subprocess
from pathlib import Path

import polars as pl
import pytest

# Patients (MRN 1-4). MEDS-Extract needs >= 1 per train/tuning/held_out split.
#   CSN 101: patient 1, discharged from the ED; blank payor; two complaints, two codes each.
#   CSN 102: patient 1, admitted for 3 days; died in hospital.
#   CSN 201: patient 2, admitted with Hosp_LOS 0 and an ED departure on the next day; blank race,
#            acuity, admit service and diagnoses.
#   CSN 301: patient 3, discharged from the ED with an "Expired" disposition but no Hosp_LOS;
#            space-separated timestamps.
#   CSN 401: patient 4, discharged; no departure time, no triage temperature.
VISITS = """\
MRN,CSN,Visit_no,Visits,Age,Gender,Race,Ethnicity,Means_of_arrival,Triage_Temp,Triage_HR,Triage_RR,\
Triage_SpO2,Triage_SBP,Triage_DBP,Triage_acuity,CC,ED_dispo,Hours_to_next_visit,\
Dispo_class_next_visit,ED_LOS,Hosp_LOS,DC_dispo,Payor_class,Admit_service,Dx_ICD9,Dx_ICD10,Dx_name,\
Arrival_time,Roomed_time,Dispo_time,Admit_time,Departure_time
1,101,1,2,40,F,Race A,Ethnicity A,Self,37.0,80,16,98,120,80,3-Urgent,"COMPLAINT A,COMPLAINT B",\
Discharge,100.0,Inpatient,4.0,,Home,"",,"001.0, V01.1","A00.0, B00.1",Synthetic dx A,\
2200-01-01T10:00:00Z,2200-01-01T10:30:00Z,2200-01-01T13:00:00Z,,2200-01-01T14:00:00Z
1,102,2,2,40,F,Race A,Ethnicity A,EMS,38.5,110,22,93,100,60,2-Emergent,COMPLAINT B,Inpatient,,,\
6.0,3,Expired,Medicare,Medicine,222.2,B00.0,Synthetic dx B,2200-01-05T18:00:00Z,2200-01-05T18:10:00Z,\
2200-01-05T22:00:00Z,2200-01-05T22:30:00Z,2200-01-06T00:00:00Z
2,201,1,1,65,M,"",Ethnicity B,Self,36.8,72,14,99,135,85,"",COMPLAINT C,Observation,,,7.0,0,Home,\
Medicaid,"","","","",2250-06-01T20:00:00Z,2250-06-01T20:20:00Z,2250-06-02T01:00:00Z,\
2250-06-02T01:30:00Z,2250-06-02T03:00:00Z
3,301,1,1,30,M,Race C,Ethnicity A,Self,37.2,90,18,97,125,75,5-Non-Urgent,COMPLAINT D,Discharge,,,\
1.0,,Expired,"",,444.4,D00.0,Synthetic dx D,2300-03-01 12:00:00,2300-03-01 12:05:00,\
2300-03-01 12:45:00,,2300-03-01 13:00:00
4,401,1,1,50,U,Race B,Ethnicity B,Other/Unknown,,85,18,97,130,80,4-Semi-Urgent,\
"COMPLAINT A, COMPLAINT E",Discharge,,,,,Home,"",,555.5,E00.0,Synthetic dx E,2280-07-01T09:00:00Z,\
2280-07-01T09:10:00Z,2280-07-01T11:00:00Z,,
"""

ORDERS = """\
CSN,Order_time,Order_type,Procedure_name,Procedure_ID,First_admin_time,Result_time
101,2200-01-01T10:45:00Z,Lab,Synthetic panel,P1,,2200-01-01T11:30:00Z
102,2200-01-05T18:20:00Z,Medications,Synthetic drug A,P2,2200-01-05T18:40:00Z,
102,2200-01-05T18:25:00Z,Medications,Synthetic drug B,"",2200-01-05T18:45:00Z,
201,2250-06-01T20:30:00Z,Imaging,Synthetic study,P3,,2250-06-01T21:30:00Z
"""

LABS = """\
CSN,Order_time,Result_time,Display_name,Abnormal,Component_name,Component_result,Component_value,Component_units,Component_abnormal,Component_nml_low,Component_nml_high
101,2200-01-01T10:45:00Z,2200-01-01T11:30:00Z,Synthetic panel,N,Analyte X,5.1,5.1,mmol/L,N,3.5,5.5
101,2200-01-01T10:45:00Z,2200-01-01T11:30:00Z,Synthetic panel,Y,Analyte Y,Positive,Positive,,Y,,
"""

RADS = """\
CSN,Order_time,Result_time,Study,Impression
201,2250-06-01T20:30:00Z,2250-06-01T21:30:00Z,Synthetic study,Synthetic impression text.
"""

NUMERICS = """\
CSN,Source,Measure,Value,Time
101,Monitor,HR,81,2200-01-01T10:31:00Z
101,Chart,Temp,98.6,2200-01-01T10:35:00Z
102,Monitor,SpO2,94,2200-01-05T18:15:00Z
"""

PMH = """\
MRN,Noted_date,CodeType,Code,Desc10,CCS,DescCCS
1,2199-06-01T00:00:00Z,Dx10,Z000,Synthetic history A,1,Synthetic CCS
1,2199-06-01T00:00:00Z,Dx10,I10,Synthetic history B,1,Synthetic CCS
2,2250-01-01T00:00:00Z,Dx9,4019,"",2,Synthetic CCS
2,2250-01-01T00:00:00Z,Dx9,E8490,Synthetic history C,2,Synthetic CCS
3,2300-01-01T00:00:00Z,Dx9,V4581,Synthetic history D,3,Synthetic CCS
3,2300-01-01T00:00:00Z,Dx10,S7200XA,Synthetic history E,3,Synthetic CCS
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


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    """The synthetic tables, written as the raw MC-MED CSVs."""
    raw_dir = tmp_path_factory.mktemp("mc_med_raw")
    for name, content in TABLES.items():
        (raw_dir / f"{name}.csv").write_text(content)
    return raw_dir


def run_command(argv: list[str]) -> None:
    """Run a CLI, failing the test with its full output if it does not succeed."""
    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"{argv[0]} failed with exit code {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.fixture(scope="session")
def meds_output(tmp_path_factory, raw_dir: Path) -> Path:
    output_dir = tmp_path_factory.mktemp("mc_med") / "MEDS_cohort"
    run_command(
        [
            "meds-extract-run",
            "spec=MC-MED",
            f"output_dir={output_dir}",
            "do_download=false",
            f"input_dir={raw_dir}",
        ]
    )
    return output_dir


@pytest.fixture(scope="session")
def events(meds_output: Path) -> pl.DataFrame:
    return pl.concat([pl.read_parquet(fp) for fp in (meds_output / "data").rglob("*.parquet")])


@pytest.fixture(scope="session")
def codes(meds_output: Path) -> pl.DataFrame:
    return pl.read_parquet(meds_output / "metadata" / "codes.parquet")
