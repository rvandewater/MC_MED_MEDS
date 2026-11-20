"""End-to-end test of the full ETL on the tiny synthetic MC-MED-shaped dataset of ``conftest.py``."""

import json
from datetime import datetime
from pathlib import Path

import polars as pl


def test_output_layout(meds_output: Path):
    assert list((meds_output / "data").rglob("*.parquet")), "No MEDS data shards written"
    for expected in ("dataset.json", "codes.parquet", "subject_splits.parquet"):
        assert (meds_output / "metadata" / expected).exists(), f"Missing metadata/{expected}"


def test_visit_id_is_hadm_id(meds_output: Path, events: pl.DataFrame):
    """The visit's CSN is written as `hadm_id`, the hospital-encounter column of the other MEDS ETLs."""
    dataset = json.loads((meds_output / "metadata" / "dataset.json").read_text())
    assert dataset["raw_source_id_columns"] == ["hadm_id"]
    assert "csn" not in events.columns
    assert set(events["hadm_id"].drop_nulls().unique().to_list()) == {101, 102, 201, 301, 401}


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
        "ED_DIAGNOSIS//ICD//10CM//D00.0",  # space-separated timestamp format
        "ED_DIAGNOSIS//ICD//9CM//222.2",
        "HOSPITAL_ADMISSION//Medicine",
        "HOSPITAL_DISCHARGE//Home",
        "HOSPITAL_DISCHARGE//Expired",
        "MEDS_DEATH",
        "ORDER//Lab//P1",
        "MEDICATION_ADMINISTRATION//P2",
        "LAB//Analyte X//mmol/L",
        "LAB//Analyte X//umol/L",
        "LAB//Analyte Y//UNK",
        "RADIOLOGY//Synthetic study",
        "VITAL//HR//Monitor",
        "VITAL//Temp//Chart",
        "HOME_MEDICATION//START//9001",
        "HOME_MEDICATION//END//9001",
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
    assert events.filter(
        (pl.col("hadm_id") == 201) & pl.col("code").str.starts_with("ED_DIAGNOSIS")
    ).is_empty()


def test_lab_units_are_stripped_and_blank_is_unk(events: pl.DataFrame, codes: pl.DataFrame):
    """CSN 102's labs record their unit as " mmol/L " and "  ": the unit in the code is stripped, so the first
    joins `LAB//Analyte X//mmol/L`, and a blank unit is `UNK`, as a missing one."""
    labs = events.filter(pl.col("code").str.starts_with("LAB//"))
    assert dict(labs.group_by("code").len().iter_rows()) == {
        "LAB//Analyte X//mmol/L": 2,
        "LAB//Analyte X//umol/L": 1,
        "LAB//Analyte Y//UNK": 2,
    }
    assert not events.filter(pl.col("code").str.contains(r"\s$")).height
    unit = dict(codes.select("code", "unit").iter_rows())
    assert unit["LAB//Analyte X//mmol/L"] == "mmol/L"
    assert unit["LAB//Analyte Y//UNK"] is None


def test_multi_valued_fields_split_into_one_event_each(events: pl.DataFrame):
    def visit_codes(csn: int, prefix: str) -> set[str]:
        sub = events.filter((pl.col("hadm_id") == csn) & pl.col("code").str.starts_with(prefix))
        return set(sub["code"].to_list())

    assert visit_codes(101, "ED_DIAGNOSIS//") == {
        "ED_DIAGNOSIS//ICD//10CM//A00.0",
        "ED_DIAGNOSIS//ICD//10CM//B00.1",
        "ED_DIAGNOSIS//ICD//9CM//001.0",
        "ED_DIAGNOSIS//ICD//9CM//V01.1",
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
    dx = events.filter((pl.col("hadm_id") == 101) & pl.col("code").str.starts_with("ED_DIAGNOSIS//"))
    text = dict(zip(dx["code"].to_list(), dx["text_value"].to_list()))
    assert text["ED_DIAGNOSIS//ICD//10CM//A00.0"] == "Synthetic dx A"
    assert text["ED_DIAGNOSIS//ICD//10CM//B00.1"] is None


def test_diagnosis_time_falls_back_to_disposition(events: pl.DataFrame):
    visit = events.filter(pl.col("hadm_id") == 401)
    assert "ED_DEPARTURE" not in visit["code"].to_list()
    dx_times = visit.filter(pl.col("code").str.starts_with("ED_DIAGNOSIS//"))["time"].unique().to_list()
    assert dx_times == [datetime(2280, 7, 1, 11, 0)]


def test_hospital_discharge_time(events: pl.DataFrame):
    discharge = events.filter(pl.col("code").str.starts_with("HOSPITAL_DISCHARGE//"))
    times = dict(zip(discharge["hadm_id"].to_list(), discharge["time"].to_list()))
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
        "PMH//ICD//10CM//Z00.0",
        "PMH//ICD//10CM//I10",
        "PMH//ICD//10CM//S72.00XA",
        "PMH//ICD//9CM//401.9",
        "PMH//ICD//9CM//E849.0",
        "PMH//ICD//9CM//V45.81",
    }
    description = dict(zip(codes["code"].to_list(), codes["description"].to_list()))
    assert description["PMH//ICD//10CM//Z00.0"] == "Synthetic history A"
    assert description["PMH//ICD//9CM//401.9"] is None  # empty Desc10


def test_ed_diagnoses_have_no_dataset_description(codes: pl.DataFrame):
    """Dx_name varies for one ICD code, so it must not end up (concatenated) in codes.parquet."""
    dx = codes.filter(pl.col("code").str.starts_with("ED_DIAGNOSIS//"))
    assert not dx.is_empty()
    assert dx["description"].is_null().all()


def test_in_hospital_deaths_are_meds_death(events: pl.DataFrame):
    deaths = events.filter(pl.col("code") == "MEDS_DEATH")
    times = dict(zip(deaths["hadm_id"].to_list(), deaths["time"].to_list()))
    assert sorted(deaths["subject_id"].to_list()) == [1, 3]
    # At the reconstructed hospital discharge (arrival + 3 days), like HOSPITAL_DISCHARGE//Expired.
    assert times[102] == datetime(2200, 1, 8, 18, 0)
    discharge = events.filter(pl.col("code") == "HOSPITAL_DISCHARGE//Expired")
    assert discharge["time"].to_list() == [times[102]]
    # Without a Hosp_LOS there is no discharge event; the death falls back to ED departure.
    assert times[301] == datetime(2300, 3, 1, 13, 0)
    assert events.filter(pl.col("code") == "MEDS_BIRTH").is_empty()


def test_numeric_codes_carry_their_unit(events: pl.DataFrame, codes: pl.DataFrame):
    """`unit` in codes.parquet: from `Component_units` for labs, and the unit PhysioNet states for the other
    numeric codes, one String per code (as MEDS-Extract >= 0.9 writes it), in UCUM.

    The code keeps its name, and suMEDS's `sumeds_normalize_units` keeps the spelling as recorded in
    `unit_source_value`.
    """
    assert codes["unit"].dtype == pl.String
    unit = dict(zip(codes["code"].to_list(), codes["unit"].to_list()))
    recorded = dict(zip(codes["code"].to_list(), codes["unit_source_value"].to_list()))
    assert recorded["LAB//Analyte X//umol/L"] == "umol/L"
    assert unit["LAB//Analyte X//mmol/L"] == "mmol/L"
    assert unit["VITAL//HR//Monitor"] == "{beats}/min"
    assert unit["VITAL//Temp//Chart"] == "[degF]"
    assert unit["VITAL//SpO2//Monitor"] == "%"
    assert unit["TRIAGE//TEMP//degC"] == "Cel"
    assert unit["TRIAGE//HR//bpm"] == "{beats}/min"
    assert unit["AGE"] == "a"
    assert unit["ED_DEPARTURE"] == "h"
    assert unit["HOSPITAL_DISCHARGE//Home"] == "d"
    # No unit recorded or documented: none given.
    assert unit["LAB//Analyte Y//UNK"] is None
    assert unit["VITAL//Pain//Chart"] is None
    assert unit["ORDER//Lab//P1"] is None


def test_lab_unit_siblings_get_their_stem(events: pl.DataFrame, codes: pl.DataFrame):
    """Analyte X is reported in two units: both codes get `LAB//Analyte X` as a parent, and the stem gets
    a codes.parquet row without a unit (and without events); a lone lab code gets neither."""
    rows = {r["code"]: r for r in codes.to_dicts()}
    assert rows["LAB//Analyte X//mmol/L"]["parent_codes"] == ["LAB//Analyte X"]
    assert rows["LAB//Analyte X//umol/L"]["parent_codes"] == ["LAB//Analyte X"]
    assert rows["LAB//Analyte X"]["unit"] is None
    assert events.filter(pl.col("code") == "LAB//Analyte X").is_empty()
    assert not rows["LAB//Analyte Y//UNK"]["parent_codes"]
    assert "LAB//Analyte Y" not in rows
    # Triage vitals end with their unit but have no sibling.
    assert not rows["TRIAGE//HR//bpm"]["parent_codes"]


def test_unit_guards_drop_no_event(events: pl.DataFrame):
    """The time-column guards that let the constant codes carry a unit drop no event that has a time."""
    counts = {
        c: events.filter(pl.col("code") == c).height for c in ("AGE", "ED_DEPARTURE", "TRIAGE//HR//bpm")
    }
    # Five visits; CSN 401 has no departure time.
    assert counts == {"AGE": 5, "ED_DEPARTURE": 4, "TRIAGE//HR//bpm": 5}
    # A missing triage value still gives its (valueless) event, as before.
    temps = events.filter(pl.col("code") == "TRIAGE//TEMP//degC")
    assert temps.height == 5 and temps["numeric_value"].null_count() == 1
