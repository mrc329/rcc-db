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
    # Commute mode (workers 16+) — public transit share is a rough
    # proxy for NYC-commuting professional/finance workers in a town
    # like Westfield on the NJ Transit rail line. This is NOT the same
    # as actual commute-destination data (that lives in a separate
    # Census dataset, OnTheMap/LODES, not pulled here).
    "B08301_001E": "commute_total_workers",
    "B08301_010E": "commute_public_transit",
    # Length of residence — proxy for civic tenure/rootedness
    "B07003_001E": "mobility_total_pop_1yr",
    "B07003_004E": "mobility_same_house_1yr_ago",
    # Occupation category (civilian employed population 16+)
    "B24010_001E": "occupation_total_employed",
    "B24010_003E": "occupation_mgmt_business_science_arts",
}

# Household income distribution (B19001) bucket labels
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
def fetch_block_group_acs():
    """
    Pull ACS 5-year estimates for every block group in Union County,
    NJ. We pull at the county level (block groups don't align to place
    boundaries) and spatially filter to Westfield afterward using
    geometry.py.

    Returns a DataFrame keyed by GEOID (state+county+tract+block group),
    with the ACS vintage used in df.attrs["acs_year"].
    """
    varlist = ",".join(["NAME"] + list(ACS_VARS.keys()) + list(INCOME_BUCKETS.keys()))
    params = {
        "get": varlist,
        "for": "block group:*",
        "in": f"state:{STATE_FIPS} county:{COUNTY_FIPS} tract:*",
    }
    errors = []
    for year in ACS_YEARS:
        resp = requests.get(_census_url(f"{year}/acs/acs5", params), timeout=30)
        if resp.status_code == 404:  # vintage not published (yet)
            errors.append(f"{year}: HTTP 404")
            continue
        resp.raise_for_status()
        data = resp.json()
        break
    else:
        raise RuntimeError("No ACS 5-year vintage available: " + "; ".join(errors))
    df = pd.DataFrame(data[1:], columns=data[0])
    df.attrs["acs_year"] = year

    numeric_cols = list(ACS_VARS.keys()) + list(INCOME_BUCKETS.keys())
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.rename(columns=ACS_VARS)
    df = df.rename(columns=INCOME_BUCKETS)

    df["GEOID"] = (
        df["state"] + df["county"] + df["tract"] + df["block group"]
    )
    return df


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
                errors.append(f"{year}/{naics_var}: {e}")
                continue
            if resp.status_code == 204:
                return year, []
            if not resp.ok:
                errors.append(f"{year}/{naics_var}: HTTP {resp.status_code}")
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
