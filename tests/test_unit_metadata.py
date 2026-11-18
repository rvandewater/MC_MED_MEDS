"""The ``mc_med_unit_metadata`` stage: unit siblings linked by their stem in ``parent_codes``, and units
stated in descriptions, in ``codes.parquet``."""

import doctest
import re

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

from MC_MED_MEDS import unit_metadata
from MC_MED_MEDS.unit_metadata import DESCRIPTION_UNIT_PATTERN, complete_unit_metadata

CORE_COUNTS = ("units", "sibling_groups", "sibling_parents", "stem_rows")


def core_counts(counts: dict[str, int]) -> dict[str, int]:
    return {k: counts[k] for k in CORE_COUNTS}


def write_meds_parquet(df: pl.DataFrame, fp) -> None:
    """Write ``df`` with ``code`` and ``description`` as Arrow ``string``, as the pipeline writes them."""
    table = df.to_arrow()
    meds_types = {"code": pa.string(), "description": pa.string()}
    pq.write_table(
        table.cast(pa.schema([pa.field(f.name, meds_types.get(f.name, f.type)) for f in table.schema])), fp
    )


def write_codes(fp, units, dtype=None):
    codes = [f"LAB//{i}" for i in range(len(units))]
    df = pl.DataFrame(
        {"code": codes, "description": [None] * len(units), "unit": units},
        schema={"code": pl.String, "description": pl.String, "unit": dtype or pl.String},
    )
    write_meds_parquet(df, fp)
    return codes


def test_unit_is_kept_as_recorded_and_written_as_large_string(tmp_path):
    fp = tmp_path / "codes.parquet"
    write_codes(fp, ["mg/dL", None, "Geen"])  # `Geen` is kept as recorded, not normalised
    assert core_counts(complete_unit_metadata(fp)) == {
        "units": 2,
        "sibling_groups": 0,
        "sibling_parents": 0,
        "stem_rows": 0,
    }
    table = pq.read_table(fp)
    CodeMetadataSchema.validate(table)
    assert table.schema.field("unit").type == pa.large_string()
    assert table.column("unit").to_pylist() == ["mg/dL", None, "Geen"]


def test_a_string_unit_passes_through(tmp_path):
    fp = tmp_path / "codes.parquet"
    write_codes(fp, ["mg", None, " %"])
    assert complete_unit_metadata(fp)["units"] == 2
    assert pl.read_parquet(fp)["unit"].to_list() == ["mg", None, " %"]
    # Idempotent: a second run changes nothing.
    before = fp.read_bytes()
    complete_unit_metadata(fp)
    assert fp.read_bytes() == before


def test_no_unit_column_is_left_alone(tmp_path):
    fp = tmp_path / "codes.parquet"
    write_meds_parquet(pl.DataFrame({"code": ["A"], "description": ["a"]}), fp)
    assert core_counts(complete_unit_metadata(fp)) == {
        "units": 0,
        "sibling_groups": 0,
        "sibling_parents": 0,
        "stem_rows": 0,
    }
    out = pq.read_table(fp)
    assert out.column_names == ["code", "description"]
    CodeMetadataSchema.validate(out)


def test_unit_siblings_get_their_stem_as_parent_and_a_stem_row(tmp_path):
    fp = tmp_path / "codes.parquet"
    df = pl.DataFrame(
        {
            "code": [
                "LAB//Na//mmol/L",  # siblings: same stem, different unit, same description
                "LAB//Na//mEq/L",
                "LAB//Na//UNKNOWN",  # no unit: not unit-suffixed, no parent
                "LAB//K//mmol/L",  # lone unit-suffixed code: no parent, no stem row
                "VITAL//HR",  # last component is not its unit
                "LAB//Hb//g/dL",  # siblings with different descriptions and one shared parent
                "LAB//Hb//mmol/L",
            ],
            "description": [
                "Sodium",
                "Sodium",
                "Sodium",
                "Potassium",
                "Heart rate",
                "Hb (mass)",
                "Hb (molar)",
            ],
            "code_template": ["t"] * 7,
            "unit": ["mmol/L", "mEq/L", None, "mmol/L", "bpm", "g/dL", "mmol/L"],
            "parent_codes": [None, None, None, None, None, ["LOINC//718-7", "X//1"], ["LOINC//718-7"]],
        },
    )
    write_meds_parquet(df, fp)
    expected = {"units": 6, "sibling_groups": 2, "sibling_parents": 4, "stem_rows": 2}
    assert core_counts(complete_unit_metadata(fp)) == expected
    table = pq.read_table(fp)
    CodeMetadataSchema.validate(table)
    assert table.schema.field("parent_codes").type == pa.list_(pa.string())
    rows = {r["code"]: r for r in table.to_pylist()}
    assert table.column("code").to_pylist() == [*df["code"].to_list(), "LAB//Na", "LAB//Hb"]
    assert [rows[c]["parent_codes"] for c in df["code"]] == [
        ["LAB//Na"],
        ["LAB//Na"],
        None,
        None,
        None,
        ["LOINC//718-7", "X//1", "LAB//Hb"],
        ["LOINC//718-7", "LAB//Hb"],
    ]
    # The stem rows: shared description (else null), shared vocabulary parents, no unit, nulls elsewhere.
    assert rows["LAB//Na"] == {
        "code": "LAB//Na",
        "description": "Sodium",
        "code_template": None,
        "unit": None,
        "parent_codes": None,
    }
    assert rows["LAB//Hb"]["description"] is None
    assert rows["LAB//Hb"]["parent_codes"] == ["LOINC//718-7"]
    assert rows["LAB//Hb"]["unit"] is None
    # Idempotent: a second run adds no parent and no row.
    before = pq.read_table(fp)
    assert core_counts(complete_unit_metadata(fp)) == {**expected, "sibling_parents": 0, "stem_rows": 0}
    assert pq.read_table(fp).equals(before)


def test_an_existing_stem_row_is_left_unchanged_and_sorting_kept(tmp_path):
    fp = tmp_path / "codes.parquet"
    df = pl.DataFrame(
        {
            "code": ["A//x//g", "A//x//mg", "B", "B//y//L", "B//y//mL", "C"],
            "description": ["x", "x", "b", "y", "y", "c"],
            "unit": ["g", "mg", None, "L", "mL", None],
            "parent_codes": [None, None, ["V//1"], None, None, None],
        }
    )
    write_meds_parquet(
        df.vstack(
            pl.DataFrame(
                {"code": ["B//y"], "description": ["own"], "unit": [None], "parent_codes": [["V//2"]]},
                schema=df.schema,
            )
        ).sort("code"),
        fp,
    )
    assert complete_unit_metadata(fp)["stem_rows"] == 1
    rows = pq.read_table(fp).to_pylist()
    assert [r["code"] for r in rows] == [
        "A//x",
        "A//x//g",
        "A//x//mg",
        "B",
        "B//y",
        "B//y//L",
        "B//y//mL",
        "C",
    ]
    assert rows[4] == {"code": "B//y", "description": "own", "unit": None, "parent_codes": ["V//2"]}


def test_parent_codes_is_created_for_siblings(tmp_path):
    fp = tmp_path / "codes.parquet"
    write_codes(fp, ["mg", "g"])  # LAB//0 and LAB//1: different stems, so no parent column needed
    complete_unit_metadata(fp)
    assert "parent_codes" not in pq.read_table(fp).column_names
    df = pl.DataFrame(
        {"code": ["X//a//mg", "X//a//g"], "unit": ["mg", "g"]},
    )
    write_meds_parquet(df, fp)
    assert complete_unit_metadata(fp)["sibling_parents"] == 2
    assert pq.read_table(fp).column("parent_codes").to_pylist() == [["X//a"], ["X//a"], None]


def test_units_stated_in_descriptions_fill_only_missing_units(tmp_path):
    fp = tmp_path / "codes.parquet"
    df = pl.DataFrame(
        {
            "code": ["A//mg", "B", "C", "D", "E"],
            "description": [
                "A [mg]",  # data unit agrees
                "B [g]",  # no data unit: filled
                "C [UNKNOWN]",  # a placeholder, not a unit: rejected
                "D [kg]",  # data unit disagrees: the data wins
                "E",  # states no unit
            ],
            "unit": ["mg", None, None, "lb", None],
        }
    )
    write_meds_parquet(df, fp)
    counts = complete_unit_metadata(fp, pattern=re.compile(r" \[(?P<unit>[^\]]+)\]$"))
    assert pq.read_table(fp).column("unit").to_pylist() == ["mg", "g", None, "lb", None]
    assert counts["description_units"] == 3
    assert counts["description_units_rejected"] == 1
    assert counts["description_units_filled"] == 1
    assert counts["description_unit_conflicts"] == 1
    # Idempotent.
    assert (
        complete_unit_metadata(fp, pattern=re.compile(r" \[(?P<unit>[^\]]+)\]$"))["description_units_filled"]
        == 0
    )


def test_stem_description_drops_only_the_unit_span(tmp_path):
    fp = tmp_path / "codes.parquet"
    pattern = re.compile(r" \[(?P<unit>[^\]]+)\]")
    df = pl.DataFrame(
        {
            "code": ["LAB//Hb//g/dL", "LAB//Hb//mmol/L", "LAB//K//mmol/L", "LAB//K//mEq/L"],
            "description": ["Hb [g/dL] (blood)", "Hb [mmol/L] (blood)", "K [mmol/L]", "Potassium [mEq/L]"],
            "unit": ["g/dL", "mmol/L", "mmol/L", "mEq/L"],
        }
    )
    write_meds_parquet(df, fp)
    assert complete_unit_metadata(fp, pattern=pattern)["stem_rows"] == 2
    rows = {r["code"]: r for r in pq.read_table(fp).to_pylist()}
    assert rows["LAB//Hb"]["description"] == "Hb (blood)"
    assert rows["LAB//K"]["description"] is None  # differs beyond the unit span


def test_mc_med_states_no_description_units():
    assert DESCRIPTION_UNIT_PATTERN is None


def test_docstring_examples():
    assert doctest.testmod(unit_metadata).failed == 0
