"""
IRS Statistics of Income (SOI) ZIP-code data: charitable contributions
claimed on individual tax returns, by ZIP.

Source: https://www.irs.gov/statistics/soi-tax-stats-individual-income-tax-statistics-zip-code-data-soi

This is the closest free public signal of actual giving behavior, with
real limits the app spells out next to the numbers:
  - Only returns that ITEMIZED deductions report charitable gifts. Since
    the 2018 standard-deduction increase most filers don't itemize, so
    these figures understate giving and lean toward high-income filers.
  - It's ZIP-level (07090 isn't exactly the town line) and lags ~2-3
    years behind the current date.
  - ZIPs with few returns are suppressed or rounded by the IRS.

Files are national CSVs. The no-AGI-breakdown file (one row per ZIP) is
tried first because it's much smaller; the full file (one row per ZIP
per income bracket) is the fallback and gets summed across brackets.
Either way only New Jersey rows are kept, while streaming, and the
result is cached on disk because the source data only changes yearly.
"""

import csv
import io

import pandas as pd
import requests
import streamlit as st

# Tax years to try, newest first (two-digit, as in the IRS file names).
TAX_YEARS = ["23", "22", "21"]
FILE_PATTERNS = [
    "https://www.irs.gov/pub/irs-soi/{yy}zpallnoagi.csv",
    "https://www.irs.gov/pub/irs-soi/{yy}zpallagi.csv",
]
STATE = "NJ"

# IRS column codes (amounts are in thousands of dollars).
COLUMNS = {
    "N1": "returns",
    "A00100": "agi_thousands",
    "N19700": "returns_claiming_charity",
    "A19700": "charity_thousands",
}

# ZIPs shown for comparison by default: Westfield and its neighbors and
# peer towns. Names are for display only.
COMPARISON_ZIPS = {
    "07090": "Westfield",
    "07016": "Cranford",
    "07076": "Scotch Plains",
    "07023": "Fanwood",
    "07092": "Mountainside",
    "07027": "Garwood",
    "07066": "Clark",
    "07081": "Springfield",
    "07901": "Summit",
    "07974": "New Providence",
    "07922": "Berkeley Heights",
    "07078": "Short Hills",
    "07041": "Millburn",
    "07069": "Watchung",
}
HOME_ZIP = "07090"


class IRSDataError(RuntimeError):
    pass


def _parse_nj_rows(lines):
    """
    Parse an SOI ZIP CSV from an iterable of text lines, keeping only
    New Jersey ZIP rows. Column names are matched case-insensitively
    (the IRS has used both ZIPCODE and zipcode across years).
    """
    reader = csv.reader(lines)
    header = [h.strip() for h in next(reader)]
    upper = [h.upper() for h in header]
    try:
        i_state = upper.index("STATE")
        i_zip = upper.index("ZIPCODE")
        i_cols = {code: upper.index(code) for code in COLUMNS}
    except ValueError as e:
        raise IRSDataError(f"Unexpected IRS file layout ({e}); columns: {header[:12]}...")

    rows = []
    for fields in reader:
        if len(fields) <= max(i_state, i_zip, *i_cols.values()):
            continue
        if fields[i_state].strip() != STATE:
            continue
        zipcode = fields[i_zip].strip().zfill(5)
        # 00000 is the state total and 99999 "other ZIPs" in these files.
        if zipcode in ("00000", "99999"):
            continue
        row = {"zip": zipcode}
        for code, idx in i_cols.items():
            row[COLUMNS[code]] = pd.to_numeric(fields[idx].strip() or None, errors="coerce")
        rows.append(row)
    if not rows:
        raise IRSDataError("No New Jersey rows found in the IRS file.")
    # The full file has one row per income bracket; sum them per ZIP.
    return pd.DataFrame(rows).groupby("zip", as_index=False).sum(min_count=1)


def _download_nj(url):
    resp = requests.get(url, stream=True, timeout=120)
    if resp.status_code == 404:
        return None
    if not resp.ok:
        raise IRSDataError(f"HTTP {resp.status_code} from {url}")
    resp.encoding = resp.encoding or "utf-8"
    lines = (line for line in resp.iter_lines(decode_unicode=True) if line)
    try:
        return _parse_nj_rows(lines)
    finally:
        resp.close()


@st.cache_data(persist="disk", show_spinner=False)
def fetch_nj_zip_giving():
    """
    Return one row per NJ ZIP with returns, AGI and charitable-deduction
    totals plus derived per-return figures. df.attrs["tax_year"] holds
    the tax year (e.g. "2022") and df.attrs["source"] the file URL.
    """
    errors = []
    for yy in TAX_YEARS:
        for pattern in FILE_PATTERNS:
            url = pattern.format(yy=yy)
            try:
                df = _download_nj(url)
            except (requests.RequestException, IRSDataError) as e:
                errors.append(f"{url}: {e}")
                continue
            if df is None:
                errors.append(f"{url}: not published")
                continue
            df = add_derived_columns(df)
            df.attrs["tax_year"] = f"20{yy}"
            df.attrs["source"] = url
            return df
    raise IRSDataError("Couldn't load IRS ZIP data — " + "; ".join(errors[-3:]))


def add_derived_columns(df):
    out = df.copy()
    out["town"] = out["zip"].map(COMPARISON_ZIPS).fillna("")
    claim = out["returns_claiming_charity"]
    out["pct_returns_claiming_charity"] = (claim / out["returns"] * 100).where(out["returns"] > 0)
    out["avg_gift_per_claiming_return"] = (out["charity_thousands"] * 1000 / claim).where(claim > 0)
    out["charity_total"] = out["charity_thousands"] * 1000
    out["avg_agi_per_return"] = (out["agi_thousands"] * 1000 / out["returns"]).where(out["returns"] > 0)
    out["charity_pct_of_agi"] = (
        out["charity_thousands"] / out["agi_thousands"] * 100
    ).where(out["agi_thousands"] > 0)
    return out


def parse_csv_text(text):
    """Parse a whole SOI CSV given as a string (used by tests)."""
    return add_derived_columns(_parse_nj_rows(io.StringIO(text)))
