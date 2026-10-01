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

# Tables fetched in separate calls, each allowed to fail on its own: not
# every ACS table is published at block-group level, and one unavailable
# variable makes the API reject the whole request (HTTP 400). A failed
# group leaves its columns empty and is listed in df.attrs["acs_missing"].
ACS_OPTIONAL_GROUPS = {
    # Commute mode (workers 16+) — public transit share is a rough
    # proxy for NYC-commuting professional/finance workers in a town
    # like Westfield on the NJ Transit rail line. This is NOT the same
    # as actual commute-destination data (that lives in a separate
    # Census dataset, OnTheMap/LODES, not pulled here).
    "commute": {
        "B08301_001E": "commute_total_workers",
        "B08301_010E": "commute_public_transit",
    },
    # Length of residence — proxy for civic tenure/rootedness. Geographic
    # mobility tables are generally tract-level and up, so expect this
    # group to come back empty at block-group level.
    "length of residence": {
        "B07003_001E": "mobility_total_pop_1yr",
        "B07003_004E": "mobility_same_house_1yr_ago",
    },
    # Occupation (civilian employed 16+). C24010 is the collapsed table
    # published down to block group; B24010 isn't. _003 is the male
    # management/business/science/arts count and _039 the female one, so
    # they're summed below.
    # Owner-occupied home value (B25075). Lines _025-_027 are $1.0-1.5M,
    # $1.5-2.0M and $2M+ (ACS's top category, so "$2M+" is a floor).
    "home values": {
        "B25075_001E": "owner_homes_total",
        "B25075_025E": "homes_1m_1_5m",
        "B25075_026E": "homes_1_5m_2m",
        "B25075_027E": "homes_2m_plus",
    },
    "occupation": {
        "C24010_001E": "occupation_total_employed",
        "C24010_003E": "occupation_mgmt_male",
        "C24010_039E": "occupation_mgmt_female",
    },
}

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

    missing = {}
    for group, variables in ACS_OPTIONAL_GROUPS.items():
        try:
            resp = requests.get(
                _census_url(f"{year}/acs/acs5", {"get": ",".join(variables), **geo}), timeout=30
            )
            if not resp.ok:
                raise RuntimeError(_census_error(resp))
            gdata = resp.json()
            gdf = pd.DataFrame(gdata[1:], columns=gdata[0])
            _to_numeric_acs(gdf, list(variables))
            df = df.merge(gdf, on=geo_cols, how="left")
        except Exception as e:
            missing[group] = _redact(e)
            for var in variables:
                df[var] = pd.NA

    df = df.rename(columns={HOUSEHOLDS_TOTAL_VAR: "households_total", **ACS_VARS})
    for variables in ACS_OPTIONAL_GROUPS.values():
        df = df.rename(columns=variables)
    df = df.rename(columns=INCOME_BUCKETS)

    df["occupation_mgmt_business_science_arts"] = (
        df["occupation_mgmt_male"] + df["occupation_mgmt_female"]
    )

    df["homes_1m_plus"] = df["homes_1m_1_5m"] + df["homes_1_5m_2m"] + df["homes_2m_plus"]
    df["pct_homes_1m_plus"] = (df["homes_1m_plus"] / df["owner_homes_total"] * 100).where(
        df["owner_homes_total"] > 0
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
