"""
Census API helpers for the Westfield, NJ dashboard.

Geography reference (confirmed via Census QuickFacts / TIGER, Oct 2026):
  State FIPS:        34 (New Jersey)
  County FIPS:       039 (Union County)
  Place FIPS:        79040 (Westfield town)
  Primary ZIP:       07090 (07091 is a PO-Box-only ZIP with no ACS data)

No Census API key is required for low-volume use, but requests are
rate-limited without one. Get a free key at:
https://api.census.gov/data/key_signup.html
Then set it as CENSUS_API_KEY in Streamlit secrets or as an env var.
"""

import re
from urllib.parse import urlencode, quote

import requests
import pandas as pd
import streamlit as st

from config import get_secret

STATE_FIPS = "34"
COUNTY_FIPS = "039"
PLACE_FIPS = "79040"  # Westfield town, NJ
PRIMARY_ZIP = "07090"
# ACS 5-year vintages to try, newest first. The first one the API serves
# wins, so a new release is picked up without a code change. Block group
# GEOIDs are stable across 2020-2029 vintages, so any of these joins to
# the 2020-based TIGER block groups in geometry_utils.
ACS_YEARS = ["2024", "2023", "2022"]

# County Business Patterns vintages to try, newest first. ZIP-level counts
# live in the `cbp` dataset since reference year 2019 (the standalone
# `zbp` dataset ended with 2018). NAICS variable naming changed with the
# 2022 NAICS revision, so both spellings are tried.
CBP_YEARS = ["2023", "2022", "2021"]
CBP_NAICS_VARS = ["NAICS2022", "NAICS2017"]

CACHE_TTL = 60 * 60 * 24  # Census data changes yearly; a day is plenty

# ACS 5-year detailed table variables
ACS_VARS = {
    "B19013_001E": "median_household_income",
    "B01003_001E": "total_population",
    "B01002_001E": "median_age",
    "B02001_002E": "white_alone",
    "B02001_003E": "black_alone",
    "B02001_005E": "asian_alone",
    "B03002_012E": "hispanic_latino",
    "B25003_002E": "owner_occupied",
    "B25003_003E": "renter_occupied",
    "B25077_001E": "median_home_value",
    "B25010_001E": "avg_household_size",
    # Educational attainment (population 25+)
    "B15003_001E": "edu_total_pop_25plus",
    "B15003_022E": "edu_bachelors",
    "B15003_023E": "edu_masters",
    "B15003_024E": "edu_professional",
    "B15003_025E": "edu_doctorate",
}

# Metrics beyond the core request. Each is the sum of the variables in a
# Census table whose official LABEL matches a pattern, rather than a
# hard-coded line number: a wrong line number silently returns some other
# real number, while a label that doesn't match is caught. `expect` is
# how many variables must match (e.g. male + female lines); any other
# count means the table isn't laid out as assumed, and the metric's group
# is reported missing instead of guessed at.
#
# Tables load in separate calls so each can fail on its own: not every
# table is published at block-group level, and one unavailable variable
# makes the API reject the whole request (HTTP 400). A failed group's
# columns are left empty and listed in df.attrs["acs_missing"].
#
# (column, table, label regex, expect, group)
_TOTAL = r"^Estimate!!Total:?$"
ACS_LABEL_METRICS = [
    # Commute mode — public transit share is a rough proxy for NYC rail
    # commuters, NOT commute destination (that's OnTheMap/LODES).
    ("commute_total_workers", "B08301", _TOTAL, 1, "commute"),
    ("commute_public_transit", "B08301",
     r"^Estimate!!Total:?!!Public transportation \(excluding taxicab\):?$", 1, "commute"),
    # Length of residence. Geographic mobility tables are generally
    # tract-level and up, so expect this group to be missing.
    ("mobility_total_pop_1yr", "B07003", _TOTAL, 1, "length of residence"),
    ("mobility_same_house_1yr_ago", "B07003",
     r"^Estimate!!Total:?!!Same house 1 year ago:?$", 1, "length of residence"),
    # Occupation (civilian employed 16+). C24010 is the block-group table;
    # it's split by sex, hence two matching lines.
    ("occupation_total_employed", "C24010", _TOTAL, 1, "occupation"),
    ("occupation_mgmt_business_science_arts", "C24010",
     r"^Estimate!!Total:?!!(Male|Female):?!!Management, business, science, and arts occupations:?$",
     2, "occupation"),
    # Mission: creative community — people working in arts/design/media.
    ("arts_workers", "C24010",
     r"!!Arts, design, entertainment, sports, and media occupations:?$", 2, "arts workers"),
    # Owner-occupied home value. $2M+ is ACS's top category.
    ("owner_homes_total", "B25075", _TOTAL, 1, "home values"),
    ("homes_1m_plus", "B25075",
     r"!!\$1,000,000 to \$1,499,999$|!!\$1,500,000 to \$1,999,999$|!!\$2,000,000 or more$",
     3, "home values"),
    # Mission: hands-on learning.
    ("households_with_kids", "B11005",
     r"^Estimate!!Total:?!!Households with one or more people under 18 years:?$",
     1, "households with children"),
    # Children under 12 today are ~3-15 when the Rialto opens in 2029.
    ("kids_under_12", "B09001",
     r"!!(Under 3 years|3 and 4 years|5 years|6 to 8 years|9 to 11 years)$",
     5, "children under 12"),
    # Mission: live performance — older adults are the core subscriber
    # audience. Male + female lines for each of six age bands.
    ("adults_65_plus", "B01001",
     r"!!(65 and 66 years|67 to 69 years|70 to 74 years|75 to 79 years|80 to 84 years|85 years and over)$",
     12, "adults 65+"),
]

# Household income distribution (B19001) bucket labels
HOUSEHOLDS_TOTAL_VAR = "B19001_001E"
INCOME_BUCKETS = {
    "B19001_002E": "< $10k",
    "B19001_003E": "$10k-15k",
    "B19001_004E": "$15k-20k",
    "B19001_005E": "$20k-25k",
    "B19001_006E": "$25k-30k",
    "B19001_007E": "$30k-35k",
    "B19001_008E": "$35k-40k",
    "B19001_009E": "$40k-45k",
    "B19001_010E": "$45k-50k",
    "B19001_011E": "$50k-60k",
    "B19001_012E": "$60k-75k",
    "B19001_013E": "$75k-100k",
    "B19001_014E": "$100k-125k",
    "B19001_015E": "$125k-150k",
    "B19001_016E": "$150k-200k",
    "B19001_017E": "$200k+",
}

# Lower bound (in dollars) of each INCOME_BUCKETS bracket, same order.
INCOME_BUCKET_FLOORS = [
    0, 10_000, 15_000, 20_000, 25_000, 30_000, 35_000, 40_000, 45_000,
    50_000, 60_000, 75_000, 100_000, 125_000, 150_000, 200_000,
]

# ACS medians are top-coded: a median income of 250,001 means "$250,000
# or more". Values at or above these are flagged, not taken literally.
MEDIAN_INCOME_TOPCODE = 250_001

# 2-digit NAICS sectors relevant to small-business sponsorship targeting
NAICS_SECTORS = {
    "44-45": "Retail Trade",
    "72": "Accommodation & Food Services",
    "81": "Other Services (repair, personal care, etc.)",
    "54": "Professional, Scientific & Technical Services",
    "62": "Health Care & Social Assistance",
    "52": "Finance & Insurance",
    "53": "Real Estate",
    "71": "Arts, Entertainment & Recreation",
}


def _get_api_key():
    return get_secret("CENSUS_API_KEY")


def _census_url(path, params):
    # Census wants literal spaces in geography names encoded as %20 and
    # ':' / '*' / ',' left alone; requests' default '+' encoding is not
    # reliably accepted.
    key = _get_api_key()
    if key:
        params = {**params, "key": key}
    return f"https://api.census.gov/data/{path}?" + urlencode(params, quote_via=quote, safe=":*,")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _redact(message):
    """Strip the API key from an error message before it reaches the page."""
    key = _get_api_key()
    return str(message).replace(key, "***") if key else str(message)


def _census_error(resp):
    # The response body says what's wrong ("error: unknown variable ...");
    # deliberately no URL, which would carry the API key.
    body = " ".join(resp.text.split())[:200]
    return _redact(f"HTTP {resp.status_code}: {body}")


def _to_numeric_acs(df, cols):
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        # ACS annotation codes (-666666666 "not computable", -999999999,
        # -888888888, -222222222 ...) are large negatives; they'd wreck
        # any sum or average, so treat them as missing.
        df.loc[df[col] < -100_000_000, col] = pd.NA


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_table_labels(year, table):
    """{variable: label} for a table's estimate variables, from the API's metadata."""
    resp = requests.get(
        _census_url(f"{year}/acs/acs5/groups/{table}.json", {}), timeout=30
    )
    if not resp.ok:
        raise RuntimeError(f"labels for {table}: {_census_error(resp)}")
    variables = resp.json().get("variables", {})
    return {
        code: meta.get("label", "")
        for code, meta in variables.items()
        if code.endswith("E") and meta.get("label", "").startswith("Estimate!!")
    }


def resolve_label_metric(labels, pattern, expect):
    """Variables whose label matches `pattern`; raises unless exactly `expect` match."""
    regex = re.compile(pattern)
    codes = sorted(code for code, label in labels.items() if regex.search(label))
    if len(codes) != expect:
        raise RuntimeError(
            f"expected {expect} variable(s) matching {pattern!r}, found {len(codes)}"
        )
    return codes


def _add_label_metrics(df, year, geo, geo_cols):
    """
    Add every ACS_LABEL_METRICS column to df. Returns (df, missing) where
    missing is {group: reason} for groups that couldn't be loaded.
    """
    by_group = {}
    for column, table, pattern, expect, group in ACS_LABEL_METRICS:
        by_group.setdefault(group, []).append((column, table, pattern, expect))

    missing = {}
    for group, specs in by_group.items():
        try:
            resolved = {}
            for column, table, pattern, expect in specs:
                labels = fetch_table_labels(year, table)
                resolved[column] = resolve_label_metric(labels, pattern, expect)
            codes = sorted({c for cs in resolved.values() for c in cs})
            resp = requests.get(
                _census_url(f"{year}/acs/acs5", {"get": ",".join(codes), **geo}), timeout=30
            )
            if not resp.ok:
                raise RuntimeError(_census_error(resp))
            data = resp.json()
            gdf = pd.DataFrame(data[1:], columns=data[0])
            _to_numeric_acs(gdf, codes)
            for column, cs in resolved.items():
                # min_count: a row where every input is missing stays missing
                gdf[column] = gdf[cs].sum(axis=1, min_count=len(cs))
            df = df.merge(gdf[geo_cols + list(resolved)], on=geo_cols, how="left")
        except Exception as e:
            missing[group] = _redact(e)
            for column, *_ in specs:
                df[column] = float("nan")
    return df, missing


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_block_group_acs():
    """
    Pull ACS 5-year estimates for every block group in Union County,
    NJ. We pull at the county level (block groups don't align to place
    boundaries) and spatially filter to Westfield afterward using
    geometry.py.

    Returns a DataFrame keyed by GEOID (state+county+tract+block group),
    with the ACS vintage used in df.attrs["acs_year"] and any optional
    table groups that couldn't be loaded in df.attrs["acs_missing"]
    ({group: reason}).
    """
    geo = {"for": "block group:*", "in": f"state:{STATE_FIPS} county:{COUNTY_FIPS} tract:*"}
    geo_cols = ["state", "county", "tract", "block group"]
    core_vars = [HOUSEHOLDS_TOTAL_VAR] + list(ACS_VARS.keys()) + list(INCOME_BUCKETS.keys())

    errors = []
    for year in ACS_YEARS:
        try:
            resp = requests.get(
                _census_url(f"{year}/acs/acs5", {"get": ",".join(["NAME"] + core_vars), **geo}),
                timeout=30,
            )
        except requests.RequestException as e:
            raise RuntimeError(_redact(f"Census API request failed: {e}")) from None
        if resp.status_code == 404:  # vintage not published (yet)
            errors.append(f"{year}: HTTP 404")
            continue
        if not resp.ok:
            raise RuntimeError(f"Census API rejected the ACS {year} request — {_census_error(resp)}")
        data = resp.json()
        break
    else:
        raise RuntimeError("No ACS 5-year vintage available: " + "; ".join(errors))
    df = pd.DataFrame(data[1:], columns=data[0])
    _to_numeric_acs(df, core_vars)

    df = df.rename(columns={HOUSEHOLDS_TOTAL_VAR: "households_total", **ACS_VARS})
    df = df.rename(columns=INCOME_BUCKETS)

    df, missing = _add_label_metrics(df, year, geo, geo_cols)

    df["pct_homes_1m_plus"] = (df["homes_1m_plus"] / df["owner_homes_total"] * 100).where(
        df["owner_homes_total"] > 0
    )
    df["pct_hh_with_kids"] = (df["households_with_kids"] / df["households_total"] * 100).where(
        df["households_total"] > 0
    )
    df["pct_adults_65_plus"] = (df["adults_65_plus"] / df["total_population"] * 100).where(
        df["total_population"] > 0
    )
    df["pct_arts_workers"] = (df["arts_workers"] / df["occupation_total_employed"] * 100).where(
        df["occupation_total_employed"] > 0
    )

    # High-capacity household counts — for major-gift prospecting these
    # say more than a median, which is top-coded at $250k anyway.
    df["hh_200k_plus"] = df["$200k+"]
    df["hh_150k_plus"] = df["$150k-200k"] + df["$200k+"]
    df["pct_hh_200k_plus"] = (df["hh_200k_plus"] / df["households_total"] * 100).where(
        df["households_total"] > 0
    )

    df["GEOID"] = (
        df["state"] + df["county"] + df["tract"] + df["block group"]
    )
    df.attrs["acs_year"] = year
    df.attrs["acs_missing"] = missing
    return df


def estimate_median_from_brackets(bracket_counts):
    """
    Estimate a median household income from summed B19001 bracket counts
    (a Series indexed by INCOME_BUCKETS labels), by linear interpolation
    within the bracket holding the middle household.

    This is the right way to get an area-wide median from several block
    groups; averaging block-group medians is not. Returns (value, label):
    if the median lands in the open-ended $200k+ bracket, value is 200000
    and label is "$200k+".
    """
    counts = [float(bracket_counts.get(label, 0) or 0) for label in INCOME_BUCKETS.values()]
    total = sum(counts)
    if total <= 0:
        return None, "n/a"
    half = total / 2
    running = 0.0
    floors = INCOME_BUCKET_FLOORS
    for i, count in enumerate(counts):
        if running + count >= half and count > 0:
            if i == len(counts) - 1:
                return floors[i], "$200k+"
            width = floors[i + 1] - floors[i]
            value = floors[i] + (half - running) / count * width
            return value, f"${value:,.0f}"
        running += count
    return None, "n/a"


def _cbp_query(get_vars, geo_params, naics_code):
    """
    Query County Business Patterns, trying each vintage and NAICS variable
    spelling until one is served. Returns (year, list-of-row-dicts).
    An HTTP 204 means the API understood the query but has no data for
    that geography/sector (common for small ZIPs), which yields [].
    """
    errors = []
    for year in CBP_YEARS:
        for naics_var in CBP_NAICS_VARS:
            params = {"get": get_vars, **geo_params, naics_var: naics_code}
            try:
                resp = requests.get(_census_url(f"{year}/cbp", params), timeout=20)
            except requests.RequestException as e:
                errors.append(_redact(f"{year}/{naics_var}: {e}"))
                continue
            if resp.status_code == 204:
                return year, []
            if not resp.ok:
                errors.append(f"{year}/{naics_var}: {_census_error(resp)}")
                continue
            data = resp.json()
            return year, [dict(zip(data[0], row)) for row in data[1:]]
    raise RuntimeError("; ".join(errors[-3:]))


def _to_int(value):
    return int(value) if value not in (None, "") else 0


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_naics_establishment_counts():
    """
    Pull County Business Patterns establishment counts for Westfield's
    primary ZIP (07090), broken out by 2-digit NAICS sector.

    IMPORTANT: this returns AGGREGATE COUNTS ONLY (e.g. "14 retail
    establishments"). The Census Bureau does not release individual
    business names or addresses in this dataset — that's by design
    (disclosure avoidance). Named businesses come from Google Places
    (places_utils.py).
    """
    rows = []
    for code, label in NAICS_SECTORS.items():
        try:
            year, records = _cbp_query("ESTAB", {"for": f"zip code:{PRIMARY_ZIP}"}, code)
            rows.append({
                "naics_code": code,
                "sector": label,
                # If the API ever returns per-size-class rows here, the
                # all-establishments total is the largest of them.
                "establishments": max((_to_int(r.get("ESTAB")) for r in records), default=0),
                "cbp_year": year,
            })
        except Exception as e:
            rows.append({"naics_code": code, "sector": label, "establishments": None, "error": str(e)})

    return pd.DataFrame(rows)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_naics_employment_size_class():
    """
    Pull County Business Patterns establishment counts by employment
    size class (1-4 employees, 5-9, 10-19, 20-49, etc.), per NAICS
    sector. Useful for distinguishing a thriving 15-person local firm
    from a 2-person storefront as a sponsorship prospect.

    IMPORTANT: employment size class breakdowns aren't reliably exposed
    at the ZIP level — this queries county-level CBP for Union County
    instead, so results reflect all of Union County, not just Westfield.
    Treat as sector context, not a Westfield-specific figure.
    """
    rows = []
    for code, label in NAICS_SECTORS.items():
        try:
            year, records = _cbp_query(
                "ESTAB,EMPSZES,EMPSZES_LABEL",
                {"for": f"county:{COUNTY_FIPS}", "in": f"state:{STATE_FIPS}"},
                code,
            )
            for record in records:
                if record.get("EMPSZES") == "001":  # "All establishments" total row
                    continue
                rows.append({
                    "naics_code": code,
                    "sector": label,
                    "employment_size_class": record.get("EMPSZES_LABEL"),
                    "establishments": _to_int(record.get("ESTAB")),
                    "cbp_year": year,
                })
        except Exception as e:
            rows.append({"naics_code": code, "sector": label, "employment_size_class": None,
                         "establishments": None, "error": str(e)})

    return pd.DataFrame(rows)
