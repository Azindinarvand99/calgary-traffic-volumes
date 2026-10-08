#!/usr/bin/env python3
"""Download the City of Calgary's yearly "Traffic Volumes for YYYY" open datasets
and harmonise them into one schema.

Outputs (in data/):
    traffic_volumes_all_years.csv      long table, no geometry
    traffic_volumes_all_years.geojson  same rows with geometry (WGS84)
    segments_wide.csv                  one row per segment, one column per year
    QA_report.md                       everything odd that was noticed

No published value is altered, dropped, de-duplicated or interpolated. Outputs are
deterministic (no run timestamps), so re-running against unchanged source data
produces byte-identical files.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

DOMAIN = "data.calgary.ca"
CATALOG_URL = "https://api.us.socrata.com/api/catalog/v1"
TITLE_RE = re.compile(r"^\s*Traffic Volumes for (\d{4})\s*$", re.IGNORECASE)
PAGE_SIZE = 5000

# Datasets known to exist when this script was written. Discovery is done live
# through the catalog API (so new years are picked up automatically); this list is
# only used to notice when a previously published year disappears.
KNOWN_DATASETS = {
    2016: "6wve-2ets",
    2017: "nvuz-qykn",
    2018: "wwf6-cpsg",
    2019: "qeqv-tb2c",
    2022: "57me-rcwr",
    2023: "bjag-w7zi",
    2024: "cauu-7hnw",
}

# Source column name -> harmonised column name.
COLUMN_ALIASES = {
    "segment_id": ["secname", "segment_name", "section_name"],
    "year": ["year", "year_vol"],
    "volume": ["volume"],
    "length_m": ["shape_leng", "length_m", "shape_length"],
    "geometry": ["the_geom", "multilinestring"],
}
OPTIONAL_COLUMNS = ["section", "collection"]  # only published from 2022 / 2023 on

LONG_COLUMNS = [
    "year",
    "segment_id",
    "volume",
    "length_m",
    "section",
    "collection",
    "dataset_year",
    "source_dataset_id",
]

# Coordinates are rounded to this many decimals (~10 m) only for *comparing*
# geometries between years in the QA report. Published coordinates are untouched.
GEOM_MATCH_DECIMALS = 4

OUT_DIR = Path(__file__).resolve().parent.parent / "data"

session = requests.Session()
session.headers["User-Agent"] = "calgary-traffic-volumes (github actions; open data harmoniser)"
if os.environ.get("SOCRATA_APP_TOKEN"):
    session.headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]


def get_json(url: str, params: dict | None = None):
    last_error = None
    for attempt in range(5):
        try:
            response = session.get(url, params=params, timeout=120)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            last_error = error
            time.sleep(2**attempt)
    raise RuntimeError(f"Request failed after retries: {url} {params}: {last_error}")


def discover_datasets() -> dict[int, list[str]]:
    """Return {year: [dataset ids]} for every 'Traffic Volumes for YYYY' dataset."""
    found: dict[int, list[str]] = defaultdict(list)
    offset = 0
    while True:
        page = get_json(
            CATALOG_URL,
            {
                "domains": DOMAIN,
                "search_context": DOMAIN,
                "q": "Traffic Volumes for",
                "only": "datasets",
                "limit": 100,
                "offset": offset,
            },
        )
        results = page.get("results", [])
        for result in results:
            resource = result["resource"]
            match = TITLE_RE.match(resource.get("name", ""))
            if match:
                found[int(match.group(1))].append(resource["id"])
        offset += len(results)
        if not results or offset >= page.get("resultSetSize", 0):
            break
    return {year: sorted(ids) for year, ids in sorted(found.items())}


def fetch_rows(dataset_id: str) -> list[dict]:
    url = f"https://{DOMAIN}/resource/{dataset_id}.json"
    expected = int(get_json(url, {"$select": "count(*) as n"})[0]["n"])
    rows: list[dict] = []
    while True:
        page = get_json(url, {"$limit": PAGE_SIZE, "$offset": len(rows), "$order": ":id"})
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
    if len(rows) != expected or not rows:
        raise RuntimeError(f"{dataset_id}: fetched {len(rows)} rows, API reports {expected}")
    return rows


def resolve_columns(dataset_id: str, field_names: list[str]) -> dict[str, str]:
    mapping = {}
    for target, aliases in COLUMN_ALIASES.items():
        matches = [name for name in aliases if name in field_names]
        if len(matches) != 1:
            raise RuntimeError(
                f"{dataset_id}: cannot map a source column to '{target}' "
                f"(candidates {aliases}, dataset has {field_names}). "
                "Add the new column name to COLUMN_ALIASES."
            )
        mapping[target] = matches[0]
    return mapping


def format_volume(value) -> str:
    """'53000.0' and '53000' are the same number; write it once, as an integer.
    Anything that is not a whole number is passed through exactly as published."""
    if value is None:
        return ""
    text = str(value)
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def to_number(text: str):
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() else number


def geometry_key(geometry: dict | None):
    if not geometry or not geometry.get("coordinates"):
        return None

    def rounded(node):
        if isinstance(node, (int, float)):
            return round(node, GEOM_MATCH_DECIMALS)
        return tuple(rounded(child) for child in node)

    return rounded(geometry["coordinates"])


def load_year(dataset_year: int, dataset_id: str) -> tuple[list[dict], dict]:
    meta = get_json(f"https://{DOMAIN}/api/views/{dataset_id}.json")
    field_names = [column["fieldName"] for column in meta["columns"] if not column["fieldName"].startswith(":")]
    mapping = resolve_columns(dataset_id, field_names)
    unmapped = [n for n in field_names if n not in mapping.values() and n not in OPTIONAL_COLUMNS]

    records = []
    for row in fetch_rows(dataset_id):
        records.append(
            {
                "year": "" if row.get(mapping["year"]) is None else str(row[mapping["year"]]),
                "segment_id": "" if row.get(mapping["segment_id"]) is None else str(row[mapping["segment_id"]]),
                "volume": format_volume(row.get(mapping["volume"])),
                "length_m": "" if row.get(mapping["length_m"]) is None else str(row[mapping["length_m"]]),
                "section": str(row.get("section") or ""),
                "collection": str(row.get("collection") or ""),
                "dataset_year": str(dataset_year),
                "source_dataset_id": dataset_id,
                "geometry": row.get(mapping["geometry"]),
            }
        )
    records.sort(key=lambda r: (r["segment_id"], r["volume"], r["length_m"], json.dumps(r["geometry"])))

    description = " ".join((meta.get("description") or "").split())
    updated = meta.get("rowsUpdatedAt")
    info = {
        "dataset_year": dataset_year,
        "dataset_id": dataset_id,
        "name": meta.get("name", ""),
        "rows": len(records),
        "rows_updated": datetime.fromtimestamp(updated, timezone.utc).strftime("%Y-%m-%d") if updated else "unknown",
        "mapping": mapping,
        "optional_present": [c for c in OPTIONAL_COLUMNS if c in field_names],
        "unmapped": unmapped,
        "description": description,
        "licence": (meta.get("license") or {}).get("name") or meta.get("licenseId") or "not stated",
    }
    return records, info


# --------------------------------------------------------------------------- outputs


def write_long_csv(records: list[dict]) -> None:
    with open(OUT_DIR / "traffic_volumes_all_years.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LONG_COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def write_geojson(records: list[dict]) -> None:
    # One feature per line keeps git diffs readable.
    with open(OUT_DIR / "traffic_volumes_all_years.geojson", "w", newline="\n", encoding="utf-8") as handle:
        handle.write('{"type": "FeatureCollection",\n"name": "traffic_volumes_all_years",\n"features": [\n')
        for index, record in enumerate(records):
            properties = {column: record[column] for column in LONG_COLUMNS}
            for column in ("year", "volume", "length_m", "dataset_year"):
                properties[column] = to_number(record[column])
            for column in OPTIONAL_COLUMNS:
                properties[column] = record[column] or None
            feature = {"type": "Feature", "properties": properties, "geometry": record["geometry"] or None}
            handle.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":")))
            handle.write(",\n" if index < len(records) - 1 else "\n")
        handle.write("]}\n")


def write_wide_csv(records: list[dict], years: list[int]) -> None:
    cells: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        cells[record["segment_id"]][int(record["dataset_year"])].append(record["volume"])

    with open(OUT_DIR / "segments_wide.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["segment_id", *[f"volume_{y}" for y in years], "n_years", "first_year", "last_year"])
        for segment_id in sorted(cells):
            by_year = cells[segment_id]
            row = [segment_id]
            for year in years:
                values = by_year.get(year, [])
                # Duplicate rows for a segment in one year: identical volumes collapse to
                # one value, conflicting volumes are all kept, separated by "|".
                distinct = list(dict.fromkeys(values))
                row.append("|".join(distinct))
            present = sorted(by_year)
            row += [len(present), present[0], present[-1]]
            writer.writerow(row)


# ------------------------------------------------------------------------ QA report


def md_table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def build_qa_report(records, infos, discovered) -> str:
    years = [info["dataset_year"] for info in infos]
    by_year = {y: [r for r in records if int(r["dataset_year"]) == y] for y in years}
    out = [
        "# QA report",
        "",
        "Generated by `scripts/build_traffic_volumes.py`. Nothing listed here has been",
        "corrected: every row is passed through exactly as the City published it. This file",
        "only records what looked odd so that users can decide how to handle it.",
        "",
        "## 1. Source datasets",
        "",
    ]
    out += md_table(
        ["Dataset year", "Dataset ID", "Rows", "Source last updated", "Segment column", "Length column", "Geometry column", "Year column", "Extra columns"],
        [
            [
                i["dataset_year"],
                f"[{i['dataset_id']}](https://{DOMAIN}/d/{i['dataset_id']})",
                i["rows"],
                i["rows_updated"],
                f"`{i['mapping']['segment_id']}`",
                f"`{i['mapping']['length_m']}`",
                f"`{i['mapping']['geometry']}`",
                f"`{i['mapping']['year']}`",
                ", ".join(f"`{c}`" for c in i["optional_present"] + i["unmapped"]) or "–",
            ]
            for i in infos
        ],
    )
    out += ["", f"Total rows across all years: **{len(records)}**. Distinct `segment_id` values: **{len({r['segment_id'] for r in records})}**.", ""]

    # --- missing / unexpected years
    out += ["## 2. Missing years and dataset availability", ""]
    missing = [y for y in range(min(years), max(years) + 1) if y not in years]
    if missing:
        out.append(f"- No \"Traffic Volumes for YYYY\" dataset exists on the portal for: **{', '.join(map(str, missing))}** (gap inside {min(years)}–{max(years)}).")
    else:
        out.append(f"- No gaps: every year from {min(years)} to {max(years)} has a dataset.")
    for year, dataset_id in KNOWN_DATASETS.items():
        if dataset_id not in discovered.get(year, []):
            out.append(f"- **Previously available dataset has disappeared:** {year} (`{dataset_id}`) was not returned by the catalog.")
    for year, ids in discovered.items():
        if len(ids) > 1:
            out.append(f"- {year} has more than one matching dataset ({', '.join(ids)}); all were loaded.")
        for dataset_id in ids:
            if KNOWN_DATASETS.get(year) != dataset_id:
                out.append(f"- New dataset picked up automatically: {year} (`{dataset_id}`). Check its row in section 1.")
    for info in infos:
        if info["unmapped"]:
            out.append(f"- {info['dataset_year']}: source columns not carried into the harmonised schema: {', '.join(info['unmapped'])}.")
    out.append("")

    # --- coverage
    out += [
        "## 3. Coverage changes between years",
        "",
        "The number of published segments varies a lot from year to year, so a segment missing",
        "from a year usually means \"not published\", not \"no traffic\".",
        "",
    ]
    names = {y: {r["segment_id"] for r in by_year[y]} for y in years}
    rows = []
    for index, year in enumerate(years):
        if index == 0:
            rows.append([year, len(by_year[year]), len(names[year]), "–", "–", "–"])
        else:
            prev = names[years[index - 1]]
            rows.append([year, len(by_year[year]), len(names[year]), len(names[year] & prev), len(names[year] - prev), len(prev - names[year])])
    out += md_table(["Dataset year", "Rows", "Distinct segments", "Also in previous year", "New vs previous year", "Dropped vs previous year"], rows)
    in_all = set.intersection(*names.values())
    out += ["", f"Segments present in every dataset year: **{len(in_all)}**.", ""]

    # --- description oddities
    out += ["## 4. What each dataset says it measures", "", "Wording is taken from the dataset descriptions on the portal.", ""]
    rows = []
    for info in infos:
        description = info["description"]
        measures = [m for m in ("AAWT", "ADWT", "WAADT") if m in description]
        ref_years = sorted(set(re.findall(r"based on (\d{4}) data", description)))
        note = ""
        if ref_years and str(info["dataset_year"]) not in ref_years:
            note = f"**Description says volumes are \"based on {', '.join(ref_years)} data\"**, which does not match the dataset year."
        rows.append([info["dataset_year"], ", ".join(measures) or "not stated", info["licence"], note or "–"])
    out += md_table(["Dataset year", "Measure named in description", "Licence field on portal", "Note"], rows)
    out.append("")

    # --- year column mismatches
    out += ["## 5. Rows whose `year` value differs from the dataset they were published in", ""]
    mismatches = [r for r in records if r["year"] != r["dataset_year"]]
    if mismatches:
        out.append("`year` is kept as published; `dataset_year` (and `segments_wide.csv`) follow the dataset title.")
        out.append("")
        out += md_table(["Dataset year", "Published `year`", "segment_id", "volume"], [[r["dataset_year"], r["year"] or "(blank)", r["segment_id"], r["volume"]] for r in mismatches])
    else:
        out.append("None.")
    out.append("")

    # --- duplicates
    out += ["## 6. Duplicate segments within a year", "", "All duplicate rows are kept in the long CSV and GeoJSON. In `segments_wide.csv`, identical", "volumes collapse to one value and conflicting volumes are shown separated by `|`.", ""]
    rows = []
    for year in years:
        groups = defaultdict(list)
        for record in by_year[year]:
            groups[record["segment_id"]].append(record)
        for segment_id in sorted(groups):
            group = groups[segment_id]
            if len(group) > 1:
                volumes = [r["volume"] for r in group]
                same_geometry = len({geometry_key(r["geometry"]) for r in group}) == 1
                rows.append([year, segment_id, len(group), ", ".join(volumes), "conflicting" if len(set(volumes)) > 1 else "identical", "same" if same_geometry else "different"])
    out += md_table(["Dataset year", "segment_id", "Rows", "Volumes", "Volumes agree?", "Geometry"], rows) if rows else ["None."]
    out.append("")

    # --- renames
    out += [
        "## 7. Segments whose name changed (same geometry, different `segment_id`)",
        "",
        f"Detected by comparing geometries between years after rounding coordinates to {GEOM_MATCH_DECIMALS} decimals",
        "(about 10 m). Segments that were renamed *and* redrawn will not be caught, so this list is a",
        "lower bound. The names are **not** merged: each name is its own row in `segments_wide.csv`.",
        "",
    ]
    by_geometry = defaultdict(lambda: defaultdict(set))
    for record in records:
        key = geometry_key(record["geometry"])
        if key is not None:
            by_geometry[key][int(record["dataset_year"])].add(record["segment_id"])
    rows = []
    for year_names in by_geometry.values():
        all_names = set().union(*year_names.values())
        if len(year_names) > 1 and len(all_names) > 1 and any(a != b for a in year_names.values() for b in year_names.values()):
            rows.append(["; ".join(f"{y}: {', '.join(sorted(year_names[y]))}" for y in sorted(year_names))])
    out += md_table(["Names used for the same geometry, by dataset year"], sorted(rows)) if rows else ["None detected."]
    out.append("")

    # --- value checks
    out += ["## 8. Volume values", ""]
    rows = []
    for year in years:
        volumes = [r["volume"] for r in by_year[year]]
        numeric = [to_number(v) for v in volumes]
        numbers = [n for n in numeric if isinstance(n, (int, float))]
        rows.append(
            [
                year,
                sum(1 for v in volumes if v == ""),
                sum(1 for n in numeric if isinstance(n, str)),
                sum(1 for n in numbers if n == 0),
                sum(1 for n in numbers if n < 0),
                sum(1 for n in numbers if n % 1000 != 0),
                min(numbers) if numbers else "–",
                max(numbers) if numbers else "–",
            ]
        )
    out += md_table(["Dataset year", "Blank", "Non-numeric", "Zero", "Negative", "Not a multiple of 1,000", "Min", "Max"], rows)
    out.append("")
    not_round = [r for r in records if isinstance(to_number(r["volume"]), (int, float)) and to_number(r["volume"]) % 1000 != 0]
    if not_round:
        out += ["Rows whose volume is not a multiple of 1,000 (i.e. not rounded the way most of the series is):", ""]
        out += md_table(["Dataset year", "segment_id", "volume", "section", "collection"], [[r["dataset_year"], r["segment_id"], r["volume"], r["section"] or "–", r["collection"] or "–"] for r in not_round])
        out.append("")
    zeros = [r for r in records if to_number(r["volume"]) == 0]
    if zeros:
        out += ["Rows with a volume of zero:", ""]
        out += md_table(["Dataset year", "segment_id", "volume"], [[r["dataset_year"], r["segment_id"], r["volume"]] for r in zeros])
        out.append("")

    # --- other field checks
    out += ["## 9. Other field checks", ""]
    rows = []
    for year in years:
        group = by_year[year]
        geometry_types = Counter((r["geometry"] or {}).get("type", "missing") for r in group)
        rows.append(
            [
                year,
                sum(1 for r in group if r["segment_id"].strip() == ""),
                sum(1 for r in group if r["segment_id"] != r["segment_id"].strip()),
                sum(1 for r in group if r["length_m"] == ""),
                sum(1 for r in group if geometry_key(r["geometry"]) is None),
                ", ".join(f"{k} ({v})" for k, v in sorted(geometry_types.items())),
            ]
        )
    out += md_table(["Dataset year", "Blank segment_id", "segment_id with stray spaces", "Blank length", "Missing geometry", "Geometry types"], rows)
    out.append("")
    variants = defaultdict(set)
    for record in records:
        variants["".join(record["segment_id"].upper().split())].add(record["segment_id"])
    clashes = sorted(sorted(v) for v in variants.values() if len(v) > 1)
    if clashes:
        out.append("`segment_id` values that differ only by letter case or spaces (treated as different segments):")
        out += [f"- {', '.join(f'`{name}`' for name in group)}" for group in clashes]
    else:
        out.append("No `segment_id` values differ only by letter case or spaces.")
    out.append("")
    for column in OPTIONAL_COLUMNS:
        rows = [[year, ", ".join(f"{k or '(blank)'} ({v})" for k, v in sorted(Counter(r[column] for r in by_year[year]).items()))] for year in years if any(r[column] for r in by_year[year])]
        if rows:
            out += [f"Values of `{column}` as published (note inconsistent spelling/case is left as is):", ""]
            out += md_table(["Dataset year", f"`{column}` values (rows)"], rows)
            out.append("")

    # --- lengths
    lengths = defaultdict(dict)
    for record in records:
        lengths[record["segment_id"]].setdefault(int(record["dataset_year"]), record["length_m"])
    changed = 0
    for by in lengths.values():
        values = [to_number(v) for v in by.values() if v != ""]
        values = [v for v in values if isinstance(v, (int, float))]
        if values and max(values) - min(values) > 1:
            changed += 1
    out += [f"Segments whose published length differs by more than 1 m between years (same `segment_id`, redrawn geometry): **{changed}**.", ""]

    # --- carried forward
    out += [
        "## 10. Identical volumes in consecutive dataset years",
        "",
        "A segment with exactly the same volume in two consecutive datasets may be a value carried",
        "forward rather than a new estimate (or simply the same after rounding). Counts only; use",
        "`segments_wide.csv` to see which segments.",
        "",
    ]
    volume_by = {y: defaultdict(set) for y in years}
    for record in records:
        volume_by[int(record["dataset_year"])][record["segment_id"]].add(record["volume"])
    rows = []
    for earlier, later in zip(years, years[1:]):
        shared = [s for s in volume_by[earlier] if s in volume_by[later]]
        same = sum(1 for s in shared if volume_by[earlier][s] == volume_by[later][s])
        share = f"{100 * same / len(shared):.0f}%" if shared else "–"
        rows.append([f"{earlier} → {later}", len(shared), same, share])
    out += md_table(["Pair of dataset years", "Segments in both", "Identical volume", "Share"], rows)
    out.append("")
    return "\n".join(out)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    discovered = discover_datasets()
    if not discovered:
        raise RuntimeError("Catalog search returned no 'Traffic Volumes for YYYY' datasets.")

    records, infos = [], []
    for year, dataset_ids in discovered.items():
        for dataset_id in dataset_ids:
            year_records, info = load_year(year, dataset_id)
            print(f"{year}  {dataset_id}  {info['rows']:>5} rows")
            records += year_records
            infos.append(info)

    years = sorted({info["dataset_year"] for info in infos})
    write_long_csv(records)
    write_geojson(records)
    write_wide_csv(records, years)
    (OUT_DIR / "QA_report.md").write_text(build_qa_report(records, infos, discovered), encoding="utf-8", newline="\n")
    print(f"Wrote {len(records)} rows for {len(years)} years to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
