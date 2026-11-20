"""A lab code's unit component is `Component_units` stripped, and `UNK` when missing or blank.

MEDS-Extract compares units stripped and drops blank ones when it writes `codes.parquet`, so a
code that kept the whitespace would split one unit over several codes (` mg`, `mg`, `mg `), and a
blank one would give a code ending in whitespace with no unit. The expressions are evaluated
straight from the shipped config with dftly, as MEDS-Extract evaluates them.
"""

from pathlib import Path

import polars as pl
import yaml
from dftly import Parser

CONFIG_FP = Path(__file__).parents[1] / "src" / "MC_MED_MEDS" / "configs" / "event_configs.yaml"


def test_lab_unit_is_stripped_and_blank_is_unk():
    block = yaml.safe_load(CONFIG_FP.read_text())["labs"]
    units = ["mg", " mg", "mg ", "\tmg\n", "mL / h", "  ", "", None]
    raw = pl.DataFrame(
        {"Component_units": units, "Component_name": ["Analyte"] * len(units)},
        schema_overrides={"Component_units": pl.String},
    )
    helper = {"_value_unit": block["_table"]["cols"]["_value_unit"]}
    with_cols = raw.with_columns(**Parser.to_polars(helper))
    codes = with_cols.select(**Parser.to_polars({"code": block["lab"]["code"]}))["code"].to_list()
    expected = ["mg", "mg", "mg", "mg", "mL / h", "UNK", "UNK", "UNK"]
    assert codes == [f"LAB//Analyte//{u}" for u in expected]
    # `unit` stays the raw column (empty text left out); MEDS-Extract strips it when it writes
    # `codes.parquet` and drops a blank one.
    assert block["lab"]["_metadata"]["_self"]["unit"].startswith("$Component_units")
