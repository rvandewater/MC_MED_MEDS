"""The packaged suMEDS rules load in suMEDS and match the codes the ETL actually writes.

suMEDS validates the rules file itself (``sumeds.load_rules``) and resolves codes with the rule
objects its enrichment uses, so these tests only state what MC-MED's codes must resolve to. The
codes come from the ETL run over the synthetic data of ``conftest.py``, so a change to a code
layout that the rules no longer match fails here. Nothing is read over the network.
"""

from importlib.resources import files
from pathlib import Path

import polars as pl
from sumeds import load_rules

RULES_FP = Path(__file__).parents[1] / "src" / "MC_MED_MEDS" / "sumeds_rules.yaml"

# The ontology rule and vocabulary code each synthetic diagnosis code must resolve to. Every
# other code of the synthetic output must match no rule.
EXPECTED = {
    "ED_DIAGNOSIS//ICD10CM//A00.0": ("mcmed-ed-icd10cm", "A00.0"),
    "ED_DIAGNOSIS//ICD10CM//B00.1": ("mcmed-ed-icd10cm", "B00.1"),
    "ED_DIAGNOSIS//ICD10CM//B00.0": ("mcmed-ed-icd10cm", "B00.0"),
    "ED_DIAGNOSIS//ICD10CM//D00.0": ("mcmed-ed-icd10cm", "D00.0"),
    "ED_DIAGNOSIS//ICD10CM//E00.0": ("mcmed-ed-icd10cm", "E00.0"),
    "ED_DIAGNOSIS//ICD9CM//001.0": ("mcmed-ed-icd9cm", "001.0"),
    "ED_DIAGNOSIS//ICD9CM//V01.1": ("mcmed-ed-icd9cm", "V01.1"),
    "ED_DIAGNOSIS//ICD9CM//222.2": ("mcmed-ed-icd9cm", "222.2"),
    "ED_DIAGNOSIS//ICD9CM//444.4": ("mcmed-ed-icd9cm", "444.4"),
    "ED_DIAGNOSIS//ICD9CM//555.5": ("mcmed-ed-icd9cm", "555.5"),
    "PMH//ICD10CM//Z00.0": ("mcmed-pmh-icd10cm", "Z00.0"),
    "PMH//ICD10CM//I10": ("mcmed-pmh-icd10cm", "I10"),
    "PMH//ICD10CM//S72.00XA": ("mcmed-pmh-icd10cm", "S72.00XA"),
    "PMH//ICD9CM//401.9": ("mcmed-pmh-icd9cm", "401.9"),
    "PMH//ICD9CM//E849.0": ("mcmed-pmh-icd9cm", "E849.0"),
    "PMH//ICD9CM//V45.81": ("mcmed-pmh-icd9cm", "V45.81"),
}


def test_rules_file_is_packaged():
    assert files("MC_MED_MEDS").joinpath("sumeds_rules.yaml").is_file()


def test_rules_load_in_sumeds():
    """The rules parse in suMEDS (which rejects malformed rules) and every pattern spans a whole code."""
    rules = load_rules(RULES_FP)
    for rule in (*rules.vocabulary_rules, *rules.hierarchy):
        if rule.pattern is not None:
            assert rule.pattern.pattern.startswith("^"), rule.id
            assert rule.pattern.pattern.endswith("$"), rule.id


def test_ontology_rules_match_the_etl_codes(codes: pl.DataFrame):
    rules = load_rules(RULES_FP).ontology
    resolved = {}
    for code in codes["code"].to_list():
        hits = [(rule.id, value) for rule in rules if (value := rule.resolve(code)) is not None]
        assert len(hits) <= 1, f"{code} matches several rules: {hits}"
        if hits:
            resolved[code] = hits[0]
    assert resolved == EXPECTED
    assert {rule_id for rule_id, _ in resolved.values()} == {rule.id for rule in rules}


def test_hierarchy_rules_stay_within_one_code_family():
    """Each same-dataset hierarchy rule draws on rules of a single code prefix, so an ED diagnosis never gets
    a past-medical-history code as parent, or the other way round."""
    rules = load_rules(RULES_FP)
    prefix = {rule.id: rule.pattern.pattern.split("//")[0] for rule in rules.ontology}
    hierarchy = [rule for rule in rules.hierarchy if rule.mode == "ontology"]
    assert hierarchy
    covered = set()
    for rule in hierarchy:
        assert rule.rules, f"{rule.id} must name the ontology rules it links"
        assert len({prefix[r] for r in rule.rules}) == 1, rule.id
        covered |= set(rule.rules)
    assert covered == set(prefix)
