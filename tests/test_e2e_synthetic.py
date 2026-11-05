"""End-to-end test of the full ETL on the tiny synthetic MC-MED-shaped dataset of ``conftest.py``."""

from datetime import datetime
from pathlib import Path

import polars as pl


def test_output_layout(meds_output: Path):
    assert list((meds_output / "data").rglob("*.parquet")), "No MEDS data shards written"
    for expected in ("dataset.json", "codes.parquet", "subject_splits.parquet"):
        assert (meds_output / "metadata" / expected).exists(), f"Missing metadata/{expected}"


def test_events(events: pl.DataFrame):
    codes = set(events["code"].to_list())

    assert set(events["subject_id"].unique().to_list()) == {1, 2, 3, 4}

    expected_codes = {
        "GENDER//F",
        "GENDER//U",
        "RACE//Race A",
        "ED_ARRIVAL//Self",
        "ED_ARRIVAL//Other/Unknown",
        "PAYOR//Medicare",
        "TRIAGE//HR//bpm",
        "TRIAGE//ESI//3-Urgent",
        "TRIAGE//CHIEF_COMPLAINT//COMPLAINT C",
        "ED_DEPARTURE",
        "ED_DIAGNOSIS//ICD10CM//D00.0",  # space-separated timestamp format
        "ED_DIAGNOSIS//ICD9CM//222.2",
        "HOSPITAL_ADMISSION//Medicine",
        "HOSPITAL_DISCHARGE//Home",
        "ORDER//Lab//P1",
        "MEDICATION_ADMINISTRATION//P2",
        "LAB//Analyte X//mmol/L",
        "LAB//Analyte Y//UNK",
        "RADIOLOGY//Synthetic study",
        "VITAL//HR//Monitor",
        "VITAL//Temp//Chart",
        "HOME_MEDICATION//9001",
        "HOME_MEDICATION_END//9001",
    }
    assert expected_codes <= codes, f"Missing codes: {sorted(expected_codes - codes)}"

    # CSN-only tables got their subject from the visits join.
    vital_subjects = events.filter(pl.col("code").str.starts_with("VITAL//"))["subject_id"].unique()
    assert sorted(vital_subjects.to_list()) == [1]


def test_empty_values_become_unk(events: pl.DataFrame):
    """Quoted empty fields ("") are read as empty text, not null; they must not yield `X//` codes."""
    codes = set(events["code"].to_list())
    assert not [c for c in codes if c.endswith("//") or "////" in c]
    assert {"RACE//UNK", "PAYOR//UNK", "TRIAGE//ESI//UNK", "HOSPITAL_ADMISSION//UNK"} <= codes
    # An empty diagnosis field is no diagnosis at all.
    assert events.filter((pl.col("csn") == 201) & pl.col("code").str.starts_with("ED_DIAGNOSIS")).is_empty()


def test_multi_valued_fields_split_into_one_event_each(events: pl.DataFrame):
    def visit_codes(csn: int, prefix: str) -> set[str]:
        sub = events.filter((pl.col("csn") == csn) & pl.col("code").str.starts_with(prefix))
        return set(sub["code"].to_list())

    assert visit_codes(101, "ED_DIAGNOSIS//") == {
        "ED_DIAGNOSIS//ICD10CM//A00.0",
        "ED_DIAGNOSIS//ICD10CM//B00.1",
        "ED_DIAGNOSIS//ICD9CM//001.0",
        "ED_DIAGNOSIS//ICD9CM//V01.1",
    }
    complaint = "TRIAGE//CHIEF_COMPLAINT//"
    assert visit_codes(101, complaint) == {f"{complaint}COMPLAINT A", f"{complaint}COMPLAINT B"}
    assert visit_codes(401, complaint) == {f"{complaint}COMPLAINT A", f"{complaint}COMPLAINT E"}
    assert (
        not events.filter(
            pl.col("code").str.starts_with("ED_DIAGNOSIS//") | pl.col("code").str.starts_with(complaint)
        )["code"]
        .str.contains(",")
        .any()
    )


def test_diagnosis_name_is_event_text(events: pl.DataFrame):
    dx = events.filter((pl.col("csn") == 101) & pl.col("code").str.starts_with("ED_DIAGNOSIS//"))
    text = dict(zip(dx["code"].to_list(), dx["text_value"].to_list()))
    assert text["ED_DIAGNOSIS//ICD10CM//A00.0"] == "Synthetic dx A"
    assert text["ED_DIAGNOSIS//ICD10CM//B00.1"] is None


def test_diagnosis_time_falls_back_to_disposition(events: pl.DataFrame):
    visit = events.filter(pl.col("csn") == 401)
    assert "ED_DEPARTURE" not in visit["code"].to_list()
    dx_times = visit.filter(pl.col("code").str.starts_with("ED_DIAGNOSIS//"))["time"].unique().to_list()
    assert dx_times == [datetime(2280, 7, 1, 11, 0)]


def test_hospital_discharge_time(events: pl.DataFrame):
    discharge = events.filter(pl.col("code").str.starts_with("HOSPITAL_DISCHARGE//"))
    times = dict(zip(discharge["csn"].to_list(), discharge["time"].to_list()))
    # Only admitted visits (those with a Hosp_LOS) are discharged from the hospital.
    assert set(times) == {102, 201}
    # Arrival + 3 days, which is after ED departure.
    assert times[102] == datetime(2200, 1, 8, 18, 0)
    # Hosp_LOS 0 would put the discharge at arrival; it is clamped to ED departure instead.
    assert times[201] == datetime(2250, 6, 2, 3, 0)


def test_orders_without_procedure_id_use_the_name(events: pl.DataFrame, codes: pl.DataFrame):
    names = {"ORDER//Medications//Synthetic drug B", "MEDICATION_ADMINISTRATION//Synthetic drug B"}
    assert names <= set(events["code"].to_list())
    described = codes.filter(pl.col("code").is_in(list(names)))
    assert described["description"].to_list() == ["Synthetic drug B", "Synthetic drug B"]


def test_past_medical_history_codes_are_dotted_icd(events: pl.DataFrame, codes: pl.DataFrame):
    pmh = set(events.filter(pl.col("code").str.starts_with("PMH//"))["code"].to_list())
    assert pmh == {
        "PMH//ICD10CM//Z00.0",
        "PMH//ICD10CM//I10",
        "PMH//ICD10CM//S72.00XA",
        "PMH//ICD9CM//401.9",
        "PMH//ICD9CM//E849.0",
        "PMH//ICD9CM//V45.81",
    }
    description = dict(zip(codes["code"].to_list(), codes["description"].to_list()))
    assert description["PMH//ICD10CM//Z00.0"] == "Synthetic history A"
    assert description["PMH//ICD9CM//401.9"] is None  # empty Desc10


def test_ed_diagnoses_have_no_dataset_description(codes: pl.DataFrame):
    """Dx_name varies for one ICD code, so it must not end up (concatenated) in codes.parquet."""
    dx = codes.filter(pl.col("code").str.starts_with("ED_DIAGNOSIS//"))
    assert not dx.is_empty()
    assert dx["description"].is_null().all()
