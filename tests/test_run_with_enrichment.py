"""`suMEDS-extract spec=MC-MED` runs the ETL and then suMEDS enrichment, offline.

This package registers its rules in suMEDS's `sumeds.rules` entry-point group under the same name
as its MEDS-Extract spec, so `suMEDS-extract` finds them from `spec=MC-MED` alone. The ontology
source is a tiny pack repository in suMEDS's `sumeds-ontologies/0.1` layout, built
here with made-up titles for the synthetic codes of ``conftest.py`` (and their categories), so
the test reads nothing over the network and does not depend on the published packs.
"""

import csv
import hashlib
import io
import json
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from conftest import run_command
from meds import CodeMetadataSchema

# vocabulary -> release -> code -> parent (None for a category). PMH//ICD10CM//S72.00XA is
# deliberately missing (it is not a valid ICD-10-CM code), so it must stay unenriched.
PACKS = {
    "ICD10CM": (
        "fy2016-fy2027",
        {
            "A00": None,
            "A00.0": "A00",
            "B00": None,
            "B00.0": "B00",
            "B00.1": "B00",
            "D00": None,
            "D00.0": "D00",
            "E00": None,
            "E00.0": "E00",
            "I10": None,
            "Z00": None,
            "Z00.0": "Z00",
        },
    ),
    "ICD9CM": (
        "v32",
        {
            "001": None,
            "001.0": "001",
            "222": None,
            "222.2": "222",
            "401": None,
            "401.9": "401",
            "444": None,
            "444.4": "444",
            "555": None,
            "555.5": "555",
            "E849": None,
            "E849.0": "E849",
            "V01": None,
            "V01.1": "V01",
            "V45": None,
            "V45.8": "V45",
            "V45.81": "V45.8",
        },
    ),
}


def title(vocabulary: str, code: str) -> str:
    return f"Synthetic {vocabulary} title {code}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="session")
def ontology_source(tmp_path_factory) -> Path:
    """A local `sumeds-ontologies/0.1` repository holding the synthetic ICD packs."""
    root = tmp_path_factory.mktemp("ontologies")
    columns = ["code", "description", "parent", "first_year", "last_year"]
    vocabularies = {}
    for vocabulary, (release, rows) in PACKS.items():
        directory = root / vocabulary / release
        directory.mkdir(parents=True)
        by_shard: dict[str, list[list[str]]] = {}
        for code, parent in rows.items():
            by_shard.setdefault(code[:1], []).append([code, title(vocabulary, code), parent or "", "", ""])
        shards = {}
        for key, shard_rows in by_shard.items():
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows([columns, *shard_rows])
            data = buffer.getvalue().encode()
            (directory / f"{key}.csv").write_bytes(data)
            shards[key] = {
                "file": f"{key}.csv",
                "rows": len(shard_rows),
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        manifest = {
            "schema": "sumeds-ontology-pack/0.1",
            "vocabulary": vocabulary,
            "release": release,
            "columns": columns,
            "fields": {"code": "code", "description": "description", "parent": "parent"},
            "shard_key_length": 1,
            "rows": len(rows),
            "bytes": sum(s["bytes"] for s in shards.values()),
            "shards": shards,
        }
        manifest_bytes = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(manifest_bytes)
        vocabularies[vocabulary] = {
            "manifest": f"{vocabulary}/{release}/manifest.json",
            "sha256": _sha256(manifest_bytes),
            "release": release,
            "rows": manifest["rows"],
            "bytes": manifest["bytes"],
        }
    index = {"schema": "sumeds-ontologies/0.1", "vocabularies": vocabularies}
    (root / "index.json").write_text(json.dumps(index))
    return root


@pytest.fixture(scope="session")
def enriched_output(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Path:
    output_dir = tmp_path_factory.mktemp("mc_med_enriched") / "MEDS_cohort"
    run_command(
        [
            "suMEDS-extract",
            "spec=MC-MED",
            f"output_dir={output_dir}",
            "do_download=false",
            f"input_dir={raw_dir}",
            "--",
            "--ontology-source",
            str(ontology_source),
        ]
    )
    return output_dir


@pytest.fixture(scope="session")
def enriched_codes(enriched_output: Path) -> pl.DataFrame:
    return pl.read_parquet(enriched_output / "metadata" / "codes.parquet")


def test_codes_parquet_is_replaced_by_valid_meds_metadata(enriched_output: Path):
    metadata = enriched_output / "metadata"
    CodeMetadataSchema.validate(pq.read_table(metadata / "codes.parquet"))
    assert not list(metadata.glob(".*enriched*")), "the temporary enrichment output was left behind"


def test_data_matches_the_plain_etl(enriched_output: Path, events: pl.DataFrame):
    enriched_events = pl.concat([pl.read_parquet(fp) for fp in (enriched_output / "data").rglob("*.parquet")])
    key = ["subject_id", "time", "code"]
    assert enriched_events.sort(key).select(key).equals(events.sort(key).select(key))


def test_icd_codes_get_titles_and_parents(enriched_codes: pl.DataFrame):
    rows = {r["code"]: r for r in enriched_codes.to_dicts()}

    # No description from the ETL: the pack title fills it.
    ed = rows["ED_DIAGNOSIS//ICD10CM//A00.0"]
    assert ed["description"] == title("ICD10CM", "A00.0")
    assert ed["parent_codes"] == ["ICD10CM//A00.0", "ICD10CM//A00"]
    assert rows["ED_DIAGNOSIS//ICD9CM//V01.1"]["description"] == title("ICD9CM", "V01.1")
    assert rows["PMH//ICD9CM//401.9"]["description"] == title("ICD9CM", "401.9")  # empty Desc10
    assert rows["PMH//ICD9CM//V45.81"]["parent_codes"] == ["ICD9CM//V45.81", "ICD9CM//V45.8", "ICD9CM//V45"]

    # A description from the ETL is kept; only the parents are added.
    pmh = rows["PMH//ICD10CM//Z00.0"]
    assert pmh["description"] == "Synthetic history A"
    assert pmh["parent_codes"] == ["ICD10CM//Z00.0", "ICD10CM//Z00"]

    # Codes that are not in the vocabulary, and non-ICD codes, are left alone.
    assert not rows["PMH//ICD10CM//S72.00XA"]["parent_codes"]
    assert not rows["ORDER//Lab//P1"]["parent_codes"]
