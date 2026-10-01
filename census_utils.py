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

import os
import requests
import pandas as pd

STATE_FIPS = "34"
COUNTY_FIPS = "039"
PLACE_FIPS = "79040"  # Westfield town, NJ
PRIMARY_ZIP = "07090"
ACS_YEAR = "2022"  # most recent 5-year ACS vintage at time of writing; bump as newer vintages release

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
    try:
        import streamlit as st
        if "CENSUS_API_KEY" in st.secrets:
            return st.secrets["CENSUS_API_KEY"]
    except Exception:
        pass
    return os.environ.get("CENSUS_API_KEY", "")


def fetch_block_group_acs():
    """
    Pull ACS 5-year estimates for every block group in Union County,
    NJ. We pull at the county level (block groups don't align to place
    boundaries) and spatially filter to Westfield afterward using
    geometry.py.

    Returns a DataFrame keyed by GEOID (state+county+tract+block group).
    """
    varlist = ",".join(["NAME"] + list(ACS_VARS.keys()) + list(INCOME_BUCKETS.keys()))
    url = (
        f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
        f"?get={varlist}"
        f"&for=block%20group:*"
        f"&in=state:{STATE_FIPS}%20county:{COUNTY_FIPS}%20tract:*"
    )
    key = _get_api_key()
    if key:
        url += f"&key={key}"

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data[1:], columns=data[0])

    numeric_cols = list(ACS_VARS.keys()) + list(INCOME_BUCKETS.keys())
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.rename(columns=ACS_VARS)
    df = df.rename(columns=INCOME_BUCKETS)

    df["GEOID"] = (
        df["state"] + df["county"] + df["tract"] + df["block group"]
    )
    return df


def fetch_naics_establishment_counts():
    """
    Pull County Business Patterns / ZIP Code Business Patterns (ZBP)
    establishment counts for Westfield's primary ZIP (07090), broken
    out by 2-digit NAICS sector.

    IMPORTANT: this returns AGGREGATE COUNTS ONLY (e.g. "14 retail
    establishments"). The Census Bureau does not release individual
    business names or addresses in this dataset — that's by design
    (disclosure avoidance). For a named prospect list, see the
    "Upload your own business list" section of the app.
    """
    key = _get_api_key()
    rows = []
    for code, label in NAICS_SECTORS.items():
        url = (
            f"https://api.census.gov/data/{ACS_YEAR}/zbp"
            f"?get=ESTAB,NAICS2017_LABEL"
            f"&for=zipcode:{PRIMARY_ZIP}"
            f"&NAICS2017={code}"
        )
        if key:
            url += f"&key={key}"
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if len(data) > 1:
                rows.append({
                    "naics_code": code,
                    "sector": label,
                    "establishments": int(data[1][0]) if data[1][0] not in (None, "") else 0,
                })
            else:
                rows.append({"naics_code": code, "sector": label, "establishments": 0})
        except Exception as e:
            rows.append({"naics_code": code, "sector": label, "establishments": None, "error": str(e)})

    return pd.DataFrame(rows)


def fetch_naics_employment_size_class():
    """
    Pull County Business Patterns establishment counts by employment
    size class (1-4 employees, 5-9, 10-19, 20-49, etc.), per NAICS
    sector. Useful for distinguishing a thriving 15-person local firm
    from a 2-person storefront as a sponsorship prospect.

    IMPORTANT: employment size class breakdowns aren't reliably exposed
    at the ZIP level (zbp) — this queries county-level CBP (`cbp`
    dataset) for Union County instead, so results reflect all of Union
    County, not just Westfield. Treat as sector context, not a
    Westfield-specific figure.
    """
    key = _get_api_key()
    rows = []
    for code, label in NAICS_SECTORS.items():
        url = (
            f"https://api.census.gov/data/{ACS_YEAR}/cbp"
            f"?get=ESTAB,EMPSZES_LABEL"
            f"&for=county:{COUNTY_FIPS}"
            f"&in=state:{STATE_FIPS}"
            f"&NAICS2017={code}"
        )
        if key:
            url += f"&key={key}"
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            header, body = data[0], data[1:]
            for row in body:
                record = dict(zip(header, row))
                rows.append({
                    "naics_code": code,
                    "sector": label,
                    "employment_size_class": record.get("EMPSZES_LABEL"),
                    "establishments": int(record["ESTAB"]) if record.get("ESTAB") not in (None, "") else 0,
                })
        except Exception as e:
            rows.append({"naics_code": code, "sector": label, "employment_size_class": None,
                         "establishments": None, "error": str(e)})

    return pd.DataFrame(rows)
