# Solar PV Data Pipeline – NREL System 1283

A Google Colab notebook that downloads, checks, and prepares solar PV data from the NREL PVDAQ open dataset (System 1283, Research Support Facility II, Golden, Colorado).

## Data Source

- **Dataset:** PVDAQ via the [OEDI Data Lake](https://oedi-data-lake.s3.amazonaws.com/) (public AWS S3, no login needed)
- **System:** 1283 – 408 kW rooftop array, SunPower modules, 2 SMA central inverters
- **Period analysed:** 2013–2014 (15-second readings)
- **Docs:** https://github.com/openEDI/documentation/blob/main/pvdaq.md

## How to Run

1. Open `Solar_Project_Data_Main.ipynb` in [Google Colab](https://colab.research.google.com/).
2. Run the cells **in order, top to bottom** (`Runtime → Run all`).
3. Requires internet access. Downloads total ~125 MB per two years of data.
4. Packages used: `requests`, `pandas`, `pyarrow`, `awscli` (installed by the notebook where needed).

> Note: the notebook uses `google.colab.files` for downloads, so it is designed for Colab.

## Notebook Sections

| # | Section | What it does |
|---|---------|--------------|
| 1 | Import Libraries & Select System | Sets up the S3 base URL and system ID (1283) |
| 2 | Discover Years and Months | Lists available years/months in the data lake |
| 3 | Download & Inspect Sample | Downloads one month and previews the columns |
| 4 | AWS Access & Historical Download | Uses AWS CLI to download full 2013–2015 data |
| 5 | Preserve & Export Raw Data | Zips the raw files for backup |
| 6 | Check Data Quality | Checks rows, duplicates, timestamps, gaps, sentinel values, and missing dates |
| 7 | Alternative Period (2013–2014) | 2015 had problems, so 2013–2014 is used instead |
| 8 | Duplicate-File Investigation | Finds repeated downloads using SHA-256 hashes |
| 9 | Verified Quality Report | Produces the checked 2013–2014 report and missing-dates list |
| 10 | Sensor-Metadata Verification | Downloads system/sensor metadata and compares power sensors |
| 11 | Timestamp & Measurement Conventions | Checks timezone/interval info in the metadata |
| 12 | Standardize Variables & Add Flags | Renames columns, converts units to kW, adds quality flags |
| 13 | Folder Setup & Final Packaging | Builds the project folders and zips everything for submission |

## Output Folder Structure

```
solar_data/
├── data/
│   ├── raw/system_1283/        # original downloaded CSVs
│   └── processed/system_1283/  # standardized CSVs + manifest
├── metadata/                   # system and sensor metadata
├── reports/                    # quality and sensor-verification reports
├── scripts/
└── docs/
```

## Key Findings

- 694 of 730 days in 2013–2014 have data files; 36 days are missing (mostly July 2014).
- Readings are every 15 seconds.
- `ac_power__1137` is in watts; it is converted to kW (÷1000) during processing.
- Metered power matches the sum of the two meters to within ~0.3%.
- Inverter 1 reads zero while meter 1 generates, so it is flagged as a suspected sensor issue.
- The value `-7999` is flagged as a suspected sentinel and kept, not deleted.
- The timestamp timezone and DST convention are **unverified**.

## Notes

- Raw data is never modified; cleaning is done through added flag columns.
- Daily energy totals are not calculated at this stage.
- 
