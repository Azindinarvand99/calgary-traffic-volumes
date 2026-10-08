# Calgary Traffic Volumes, all years in one place

> **This is not an official City of Calgary product.** It is an independent,
> unofficial repackaging of open data published by the City of Calgary. The City
> does not endorse, maintain or warrant it. For authoritative figures, use the
> source datasets linked below.

The City of Calgary publishes its traffic volume flow-map data on
[data.calgary.ca](https://data.calgary.ca) as a **separate dataset for each year**, and
the column names change from year to year. This repository downloads every available
year, puts them into one consistent schema, and keeps the result up to date
automatically.

No values are changed. Rows are not dropped, de-duplicated, corrected or interpolated;
anything that looks odd is listed in [`data/QA_report.md`](data/QA_report.md) instead.

## Key findings

A summary of [`data/QA_report.md`](data/QA_report.md), which has the full detail.

- **2020 and 2021 are missing.** The portal has no Traffic Volumes dataset for either
  year, so the series jumps from 2019 to 2022.
- **Coverage changes sharply by year.** Only 151 segments appear in all seven years.

  | Year | 2016 | 2017 | 2018 | 2019 | 2022 | 2023 | 2024 |
  |---|---|---|---|---|---|---|---|
  | Rows published | 1,491 | 1,569 | 1,769 | 943 | 170 | 326 | 334 |

- **Many 2016–2019 values repeat from one year to the next.** Of the segments published
  in both years, the volume is identical for 43% (2016→2017), 38% (2017→2018) and 57%
  (2018→2019). Some of this is rounding to the nearest 1,000, but it is also consistent
  with values being carried forward, so an unchanged value is not evidence of unchanged
  traffic.
- **Provincial roads switch to a different measure from 2022.** From the 2022 dataset
  on, volumes on provincial sections (58–64 rows a year, e.g. Deerfoot Trail, Stoney
  Trail, Highway 22X) are supplied by the Province as Weighted Average Annual Daily
  Traffic (WAADT) rather than the City's weekday estimate. They are flagged in the
  `section` column and should not be compared directly with earlier years.

## What the data is

Each row is one road segment in one year with an estimated weekday traffic volume:
the average number of vehicles passing in 24 hours on an average weekday, adjusted for
seasonal variation, as a two-way total. The City calls this Average Annual Weekday
Traffic (AAWT) in some years and Average Daily Weekday Traffic (ADWT) in others.

Years currently available: **2016, 2017, 2018, 2019, 2022, 2023, 2024**
(there are no 2020 or 2021 datasets on the portal).

## Files

| File | What it is |
|---|---|
| [`data/traffic_volumes_all_years.csv`](data/traffic_volumes_all_years.csv) | Long table: one row per segment per year, no geometry. |
| [`data/traffic_volumes_all_years.geojson`](data/traffic_volumes_all_years.geojson) | The same rows with line geometry, WGS84 (EPSG:4326). |
| [`data/segments_wide.csv`](data/segments_wide.csv) | One row per segment, one volume column per year, for reading trends. |
| [`data/QA_report.md`](data/QA_report.md) | Missing years, duplicates, renamed segments and other oddities. |
| [`scripts/build_traffic_volumes.py`](scripts/build_traffic_volumes.py) | The script that produces all of the above. |

## Column dictionary

### `traffic_volumes_all_years.csv` and the GeoJSON properties

| Column | Type | Description | Source column(s) |
|---|---|---|---|
| `year` | integer | Year of the volume, exactly as published in the row. | `year`, `year_vol` |
| `segment_id` | text | The City's name for the road segment (e.g. `14STSW1`, `DEERFOOT12A`). This is the only identifier the City publishes; it is a label, not a guaranteed-stable key. | `secname`, `segment_name`, `section_name` |
| `volume` | number | Estimated two-way weekday traffic volume, vehicles per 24 hours. | `volume` |
| `length_m` | number | Segment length in metres, as published. | `shape_leng`, `length_m`, `shape_length` |
| `section` | text | `City Section` or `Provincial Section`. Blank before 2022 (not published). | `section` |
| `collection` | text | How the volume was obtained (`Segment`, `Intersection`, `Perm`, `Prov`/`prov`), as published. Blank before 2023. | `collection` |
| `dataset_year` | integer | Year in the title of the source dataset. Equals `year` except where the City's own `year` value disagrees (see QA report). | dataset title |
| `source_dataset_id` | text | Socrata ID of the source dataset; `https://data.calgary.ca/d/<id>` opens it. | – |
| `geometry` | MultiLineString | GeoJSON only. Coordinates exactly as published, longitude/latitude, WGS84. | `the_geom`, `multilinestring` |

The only formatting change: the API returns some volumes as `53000.0` and others as
`53000`; whole numbers are written without the trailing `.0`. In the GeoJSON, `year`,
`volume`, `length_m` and `dataset_year` are JSON numbers rather than strings.

### `segments_wide.csv`

| Column | Description |
|---|---|
| `segment_id` | Segment name, matched **exactly** between years. Renamed segments appear as separate rows. |
| `volume_2016` … `volume_2024` | Volume in that dataset year. Blank means the segment was not published that year (not zero traffic). If a segment has duplicate rows with different volumes in one year, all values are shown separated by `\|` (e.g. `110000\|120000`); a new column is added automatically when a new year is published. |
| `n_years` | Number of dataset years in which the segment appears. |
| `first_year`, `last_year` | First and last dataset year in which the segment appears. |

## Caveats: please read before using

- **Volumes are City estimates, not raw counts.** AAWT/ADWT is modelled from counts
  and seasonally adjusted to represent an average weekday.
- **Volumes are two-way totals**, not per direction.
- **Volumes are generally rounded to the nearest 1,000**, so small year-to-year changes
  are not meaningful. There are exceptions: a few 2016 values are below 1,000, three
  2022 values end in 500, and the 2023 provincial sections are published unrounded.
  All are listed in the QA report.
- **Identical values in consecutive years may be carried forward** rather than freshly
  estimated. Do not read an unchanged value as evidence that traffic did not change.
  The QA report counts how often this happens for each pair of years.
- **Provincial roads use a different measure.** From 2022, volumes on provincial
  sections (Deerfoot Trail, Stoney Trail, Highway 22X, etc.) are supplied by the
  Province as Weighted Average Annual Daily Traffic (WAADT), which includes weekends,
  and is not directly comparable with the City's weekday figure. Use the `section`
  column to separate them.
- **Coverage changes a lot between years** (about 1,500–1,800 segments in 2016–2018,
  943 in 2019, 170 in 2022, about 330 in 2023–2024). Only 151 segments appear in
  every year. A blank is "not published", never "zero".
- **There is no 2020 or 2021 data**, and 2022 onward reflect post-pandemic conditions.
  The City notes that volumes may reflect construction, detours and operating
  conditions in place during the year.
- **`segment_id` is not a stable key.** Some segments were renamed, split or redrawn
  between years, and a handful appear twice in the same year, occasionally with
  different volumes. Nothing has been merged or resolved here; see the QA report.
- **Zero volumes** appear for a few segments in 2018 and 2024 and are passed through
  as published.
- The description of the 2024 dataset on the portal says its volumes are "based on
  2023 data". This is reported as found.

## Sources

All data comes from the City of Calgary's Open Data Portal:

| Year | Source dataset |
|---|---|
| 2016 | [Traffic Volumes for 2016](https://data.calgary.ca/d/6wve-2ets) |
| 2017 | [Traffic Volumes for 2017](https://data.calgary.ca/d/nvuz-qykn) |
| 2018 | [Traffic Volumes for 2018](https://data.calgary.ca/d/wwf6-cpsg) |
| 2019 | [Traffic Volumes for 2019](https://data.calgary.ca/d/qeqv-tb2c) |
| 2022 | [Traffic Volumes for 2022](https://data.calgary.ca/d/57me-rcwr) |
| 2023 | [Traffic Volumes for 2023](https://data.calgary.ca/d/bjag-w7zi) |
| 2024 | [Traffic Volumes for 2024](https://data.calgary.ca/d/cauu-7hnw) |

New years are discovered automatically by searching the portal's catalogue for
datasets titled "Traffic Volumes for YYYY", so this table may lag behind the data;
section 1 of the QA report always lists exactly what was loaded.

## Licence and attribution

Contains information licensed under the Open Government Licence – City of Calgary.

The licence terms are on the City's
[Open Calgary Terms of Use](https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa)
page. If you reuse the files in `data/`, keep the attribution statement above.

That licence covers the data in `data/`. The code in this repository (the script and
workflow) is released under the [MIT License](LICENSE).

This repository is not affiliated with, endorsed by, or an official product of the
City of Calgary.

## How it is kept up to date

A GitHub Actions workflow
([`.github/workflows/update-data.yml`](.github/workflows/update-data.yml)) runs the
script on the 1st of every month and commits the `data/` folder **only if a file
actually changed**. The outputs contain no run timestamps and are sorted
deterministically, so an unchanged source produces no commit. It can also be run by
hand from the repository's Actions tab.

If the City publishes a year with a column name the script has not seen before, the
run fails with a message naming the column rather than guessing; add the name to
`COLUMN_ALIASES` in the script.

## Run it yourself

Requires Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

```bash
python scripts/build_traffic_volumes.py
```

An optional Socrata app token can be supplied in the `SOCRATA_APP_TOKEN` environment
variable; it is not needed for normal use.
