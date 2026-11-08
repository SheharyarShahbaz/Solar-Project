"""Build the daily modelling table for PVDAQ system 1283 (2013-2014).

Reads the raw 15-second CSV files, skips identical duplicate copies, and
writes one row per calendar day with daily energy, irradiation, weather,
coverage and quality flags. Raw files are never modified.

Usage (from the repo root or anywhere):
    python src/build_daily.py --raw "D:/PSDS Project (Solar)/solar_data/data/raw/system_1283" \
                              --out "D:/PSDS Project (Solar)/solar_data/data/processed"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- settings
SYSTEM_ID = 1283
YEARS = (2013, 2014)
INTERVAL_S = 15                       # nominal sampling interval
READINGS_PER_DAY = 86400 // INTERVAL_S  # 5760
HOURS_PER_READING = INTERVAL_S / 3600
NAMEPLATE_KW = 408.24                 # system metadata "power"
LONGITUDE = -105.1711                 # system metadata, Golden CO
STANDARD_MERIDIAN = -105.0            # Mountain Standard Time (UTC-7)
SENTINEL = -7999
DAYLIGHT_W_M2 = 20                    # irradiance above this counts as daylight
IRRADIANCE_PLAUSIBLE_MAX = 1500       # W/m2, above this is flagged

# usable-day rule (documented in the README; change here if the team decides otherwise)
MIN_COVERAGE = 0.95

COLUMNS = {
    "power_kw": "ac_power_metered_kw__1040",
    "poa_w_m2": "poa_irradiance__1055",
    "ambient_c": "ambient_temp__1053",
    "module_c": "module_temp__1056",
    "wind_m_s": "wind_speed__1051",
}
REQUIRED = ("power_kw", "poa_w_m2", "ambient_c")


# ---------------------------------------------------------------- helpers
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_unique_files(raw: Path) -> tuple[dict[pd.Timestamp, Path], pd.DataFrame]:
    """One file per date. Identical copies are skipped; different copies stop the run."""
    chosen: dict[pd.Timestamp, tuple[Path, str]] = {}
    log = []
    for path in sorted(raw.rglob(f"system_{SYSTEM_ID}__date_*.csv")):
        try:
            date = pd.to_datetime(path.stem.split("__date_")[1], format="%Y_%m_%d")
        except (IndexError, ValueError):
            continue
        if date.year not in YEARS:
            continue
        digest = sha256(path)
        if date in chosen:
            if chosen[date][1] != digest:
                raise ValueError(f"Different contents for the same date:\n{chosen[date][0]}\n{path}")
            log.append({"date": date.date(), "path": str(path), "status": "identical copy skipped"})
            continue
        chosen[date] = (path, digest)
        log.append({"date": date.date(), "path": str(path), "status": "used"})
    return {d: p for d, (p, _) in chosen.items()}, pd.DataFrame(log)


def solar_noon_mst_hours(date: pd.Timestamp) -> float:
    """Solar noon in Mountain Standard Time (NOAA equation-of-time approximation)."""
    gamma = 2 * math.pi / 365 * (date.dayofyear - 1)
    eot_min = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    return 12 + (4 * (STANDARD_MERIDIAN - LONGITUDE) - eot_min) / 60


def summarise_day(date: pd.Timestamp, path: Path) -> dict:
    row: dict = {"date": date.date(), "file_present": True, "source_file": str(path)}
    df = pd.read_csv(path)

    absent = [key for key, col in COLUMNS.items() if col not in df.columns]
    row["absent_columns"] = ", ".join(COLUMNS[k] for k in absent)

    times = pd.to_datetime(df["measured_on"], errors="coerce")
    keep = times.notna() & ~times.duplicated() & (times.dt.normalize() == date)
    df, times = df.loc[keep].reset_index(drop=True), times[keep].reset_index(drop=True)
    row["readings"] = len(df)
    row["max_gap_min"] = (times.sort_values().diff().dt.total_seconds().max() or 0) / 60

    data = {}
    for key, col in COLUMNS.items():
        if col in df.columns:
            values = pd.to_numeric(df[col], errors="coerce")
            row[f"{key}_sentinels"] = int((values == SENTINEL).sum())
            data[key] = values.mask(values == SENTINEL)
        else:
            row[f"{key}_sentinels"] = 0
            data[key] = pd.Series(np.nan, index=df.index)

    power, poa = data["power_kw"], data["poa_w_m2"]
    daylight = poa > DAYLIGHT_W_M2

    # coverage = valid readings / nominal readings in a full day
    row["coverage_power"] = power.notna().sum() / READINGS_PER_DAY
    row["coverage_poa"] = poa.notna().sum() / READINGS_PER_DAY

    # energy: power (kW) x interval (h), summed. Negative night readings are the
    # site drawing power, so generation uses positive values only.
    row["energy_kwh"] = power.clip(lower=0).sum(min_count=1) * HOURS_PER_READING
    row["night_draw_kwh"] = power.clip(upper=0).sum(min_count=1) * HOURS_PER_READING
    row["peak_power_kw"] = power.max()
    row["readings_over_nameplate"] = int((power > NAMEPLATE_KW).sum())

    row["poa_irradiation_kwh_m2"] = poa.clip(lower=0).sum(min_count=1) * HOURS_PER_READING / 1000
    row["peak_poa_w_m2"] = poa.max()
    row["readings_poa_implausible"] = int((poa > IRRADIANCE_PLAUSIBLE_MAX).sum())
    row["daylight_hours"] = daylight.sum() * HOURS_PER_READING

    row["ambient_mean_c"] = data["ambient_c"].mean()
    row["ambient_daylight_mean_c"] = data["ambient_c"][daylight].mean()
    row["ambient_max_c"] = data["ambient_c"].max()
    row["module_daylight_mean_c"] = data["module_c"][daylight].mean()
    row["wind_daylight_mean_m_s"] = data["wind_m_s"][daylight].mean()

    # performance ratio: energy / (irradiation x nameplate)
    if row["poa_irradiation_kwh_m2"] and row["poa_irradiation_kwh_m2"] > 0:
        row["performance_ratio"] = row["energy_kwh"] / (row["poa_irradiation_kwh_m2"] * NAMEPLATE_KW)

    # timezone evidence: irradiance-weighted centre of the day vs solar noon (MST)
    weights = poa.clip(lower=0).fillna(0)
    if weights.sum() > 0:
        hours = times.dt.hour + times.dt.minute / 60 + times.dt.second / 3600
        centroid = float((hours * weights).sum() / weights.sum())
        row["poa_centroid_hour"] = centroid
        row["centroid_minus_solar_noon_min"] = (centroid - solar_noon_mst_hours(date)) * 60

    daylight_sentinels = sum(int(((pd.to_numeric(df[COLUMNS[k]], errors="coerce") == SENTINEL)
                                  & daylight).sum()) for k in REQUIRED if COLUMNS[k] in df.columns)
    row["daylight_sentinels"] = daylight_sentinels
    return row


def add_flags(daily: pd.DataFrame) -> pd.DataFrame:
    reasons = []
    for r in daily.itertuples():
        why = []
        if not r.file_present:
            why.append("no file")
        else:
            if isinstance(r.absent_columns, str) and any(COLUMNS[k] in r.absent_columns for k in REQUIRED):
                why.append("required column absent")
            if r.coverage_power < MIN_COVERAGE:
                why.append(f"power coverage < {MIN_COVERAGE:.0%}")
            if r.coverage_poa < MIN_COVERAGE:
                why.append(f"irradiance coverage < {MIN_COVERAGE:.0%}")
            if r.daylight_sentinels > 0:
                why.append("sentinel during daylight")
        reasons.append("; ".join(why))
    daily["exclude_reason"] = reasons
    daily["usable"] = daily["exclude_reason"] == ""
    daily["flag_over_nameplate"] = daily["readings_over_nameplate"].fillna(0) > 0
    daily["flag_poa_implausible"] = daily["readings_poa_implausible"].fillna(0) > 0
    return daily


def timezone_summary(daily: pd.DataFrame) -> pd.DataFrame:
    """Use the sunniest 20% of usable days in each month, because clouds in the
    afternoon (common in Colorado summers) pull the irradiance centre earlier."""
    usable = daily[daily["usable"]].copy()
    month = pd.to_datetime(usable["date"]).dt.to_period("M").astype(str)
    sunny = usable.groupby(month)["poa_irradiation_kwh_m2"].rank(pct=True) >= 0.8
    clear = usable[sunny]
    month = month[sunny]
    return (clear.groupby(month)["centroid_minus_solar_noon_min"]
            .agg(["median", "count"]).round(1).rename_axis("month"))


# ---------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", required=True, type=Path, help="folder holding the raw system_1283 CSVs")
    parser.add_argument("--out", required=True, type=Path, help="folder for the daily outputs")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    files, file_log = find_unique_files(args.raw)
    print(f"Unique daily files: {len(files)}  (identical copies skipped: "
          f"{(file_log['status'] != 'used').sum()})")

    rows = []
    for i, (date, path) in enumerate(sorted(files.items()), 1):
        rows.append(summarise_day(date, path))
        if i % 100 == 0:
            print(f"  processed {i}/{len(files)}")

    calendar = pd.date_range(f"{YEARS[0]}-01-01", f"{YEARS[-1]}-12-31").date
    daily = pd.DataFrame(rows).set_index("date").reindex(calendar).rename_axis("date").reset_index()
    daily["file_present"] = daily["file_present"].fillna(False).astype(bool)
    daily = add_flags(daily)

    daily_path = args.out / f"daily_system_{SYSTEM_ID}_{YEARS[0]}_{YEARS[-1]}.csv"
    daily.to_csv(daily_path, index=False)
    file_log.to_csv(args.out / "daily_build_file_log.csv", index=False)

    tz = timezone_summary(daily)
    tz.to_csv(args.out / "timezone_check_by_month.csv")

    summary = {
        "calendar_days": len(daily),
        "days_with_file": int(daily["file_present"].sum()),
        "usable_days": int(daily["usable"].sum()),
        "excluded_by_reason": daily.loc[~daily["usable"], "exclude_reason"].value_counts().to_dict(),
        "sentinel_readings": {k: int(daily[f"{k}_sentinels"].sum()) for k in COLUMNS},
        "days_over_nameplate": int(daily["flag_over_nameplate"].sum()),
        "days_poa_implausible": int(daily["flag_poa_implausible"].sum()),
        "usable_energy_kwh_total": round(float(daily.loc[daily["usable"], "energy_kwh"].sum()), 1),
        "usable_energy_kwh_daily_median": round(float(daily.loc[daily["usable"], "energy_kwh"].median()), 1),
    }
    (args.out / "daily_build_summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print("\nIrradiance centre minus solar noon (MST), minutes, by month:")
    print(tz.to_string())
    print("\nAbout 0 all year  -> timestamps are standard time (MST, no DST).")
    print("About +60 Apr-Oct -> timestamps follow daylight saving (MDT in summer).")
    print(f"\nSaved: {daily_path}")


if __name__ == "__main__":
    main()
