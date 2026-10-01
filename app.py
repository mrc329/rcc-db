"""
Westfield, NJ — Giving Capacity & Small Business Targeting Dashboard

Built for Rialto Center for Creativity capital campaign prospect research.

Two data layers:
  1. Census ACS income + demographics, mapped by block group (GIS layer)
  2. Small business targeting by NAICS sector — Census gives aggregate
     establishment COUNTS only (no names/addresses, by design). Named
     businesses come from the Google Places API, each mapped to a NAICS
     sector and tagged with a heuristic chain/independent label.

Secrets (Streamlit Cloud: app Settings -> Secrets; locally:
.streamlit/secrets.toml — see secrets.toml.example):
  CENSUS_API_KEY, GOOGLE_PLACES_API_KEY
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from census_utils import (
    fetch_block_group_acs,
    fetch_naics_establishment_counts,
    fetch_naics_employment_size_class,
)
from config import get_secret
from geometry_utils import get_westfield_block_groups, get_westfield_boundary
import places_utils

st.set_page_config(page_title="Westfield Giving & Business Dashboard", layout="wide")

st.title("Westfield, NJ — Giving Capacity & Small Business Dashboard")
st.caption(
    "Census ACS income/demographics by block group, plus small-business "
    "targeting by NAICS sector — built for Rialto capital campaign prospect research."
)

tab_map, tab_demo, tab_biz = st.tabs(["Income Map", "Demographics", "Small Business Targeting"])

# ---- Load data (cached) ----
with st.spinner("Loading Census data..."):
    try:
        acs_df = fetch_block_group_acs()
    except Exception as e:
        st.error(f"Could not load ACS data: {e}")
        st.stop()

try:
    westfield_bgs = get_westfield_block_groups()
    westfield_bgs["GEOID"] = westfield_bgs["GEOID"].astype(str)
    merged = westfield_bgs.merge(acs_df, on="GEOID", how="left")
except Exception as e:
    st.warning(
        f"Could not load GIS boundaries ({e}). This requires live internet "
        "access to census.gov TIGER files — confirm this works once deployed."
    )
    merged = None

# ---- Tab 1: Income map ----
with tab_map:
    st.subheader("Median Household Income by Block Group")
    if merged is not None:
        fig = px.choropleth_map(
            merged,
            geojson=merged.geometry.__geo_interface__,
            locations=merged.index,
            color="median_household_income",
            hover_name="NAME",
            hover_data={
                "median_household_income": ":$,.0f",
                "total_population": True,
                "median_age": True,
            },
            map_style="carto-positron",
            center={"lat": 40.6589, "lon": -74.3479},  # Westfield, NJ
            zoom=12.5,
            opacity=0.65,
            color_continuous_scale="Greens",
            labels={"median_household_income": "Median HH Income"},
        )
        fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0}, height=600)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Map unavailable — see warning above.")

    st.caption(
        "Block groups shown are those intersecting Westfield's town boundary; "
        "some may extend slightly beyond the town line. "
        f"Source: ACS 5-year estimates, {acs_df.attrs.get('acs_year', '?')} vintage."
    )

# ---- Tab 2: Demographics ----
with tab_demo:
    st.subheader("Income Distribution (Westfield-area block groups, aggregated)")
    income_cols = [c for c in acs_df.columns if c.startswith("$") or c.startswith("<")]
    if merged is not None and income_cols:
        income_totals = merged[income_cols].sum().reset_index()
        income_totals.columns = ["bracket", "households"]
        fig2 = px.bar(income_totals, x="bracket", y="households",
                       title="Household Income Distribution")
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Demographic Summary")
    if merged is not None:
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Population", f"{merged['total_population'].sum():,.0f}")
        col2.metric("Median Age (avg across BGs)", f"{merged['median_age'].mean():.1f}")
        col3.metric("Median HH Income (avg across BGs)",
                     f"${merged['median_household_income'].mean():,.0f}")
        col4.metric("Median Home Value (avg across BGs)",
                     f"${merged['median_home_value'].mean():,.0f}")
        col5.metric("Avg Household Size", f"{merged['avg_household_size'].mean():.2f}")

        race_totals = merged[["white_alone", "black_alone", "asian_alone", "hispanic_latino"]].sum()
        fig3 = px.pie(values=race_totals.values, names=race_totals.index,
                       title="Race / Ethnicity Breakdown (not mutually exclusive categories)")
        st.plotly_chart(fig3, use_container_width=True)

    if merged is not None:
        st.subheader("Professional / Prospect-Research Indicators")
        edu_pct = (
            (merged["edu_bachelors"] + merged["edu_masters"]
             + merged["edu_professional"] + merged["edu_doctorate"]).sum()
            / merged["edu_total_pop_25plus"].sum() * 100
        )
        transit_pct = merged["commute_public_transit"].sum() / merged["commute_total_workers"].sum() * 100
        stayed_pct = merged["mobility_same_house_1yr_ago"].sum() / merged["mobility_total_pop_1yr"].sum() * 100
        mgmt_pct = merged["occupation_mgmt_business_science_arts"].sum() / merged["occupation_total_employed"].sum() * 100

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bachelor's+ (25+)", f"{edu_pct:.0f}%")
        c2.metric("Commute via Public Transit", f"{transit_pct:.0f}%",
                   help="Rough proxy for NYC rail-commuting professionals, not actual commute destination")
        c3.metric("Same Residence vs. 1 Yr Ago", f"{stayed_pct:.0f}%",
                   help="Proxy for civic tenure/rootedness")
        c4.metric("Mgmt/Business/Science/Arts Occupations", f"{mgmt_pct:.0f}%")

    with st.expander("Raw block group data"):
        st.dataframe(acs_df)

# ---- Tab 3: Small business targeting ----
with tab_biz:
    st.subheader("NAICS Sector Establishment Counts (Census — aggregate only)")
    st.caption(
        "⚠️ Census County/ZIP Business Patterns gives establishment COUNTS by "
        "sector, not business names or addresses (disclosure avoidance). "
        "Use this to understand sector composition, not as a contact list."
    )
    naics_df = fetch_naics_establishment_counts()
    st.dataframe(naics_df, use_container_width=True)

    fig4 = px.bar(naics_df.dropna(subset=["establishments"]),
                   x="sector", y="establishments",
                   title=f"Establishments by Sector — ZIP {naics_df.attrs.get('zip', '07090')}")
    fig4.update_layout(xaxis_tickangle=-30)
    st.plotly_chart(fig4, use_container_width=True)

    st.divider()
    st.subheader("Establishment Size by Sector (Union County-wide)")
    st.caption(
        "⚠️ Not Westfield-specific — employment size class isn't reliably exposed "
        "at the ZIP level, so this reflects all of Union County. Useful for "
        "understanding sector composition (e.g. are retail businesses mostly "
        "solo operations or do they employ staff), not for precise local counts."
    )
    size_df = fetch_naics_employment_size_class()
    st.dataframe(size_df, use_container_width=True)

    st.divider()
    st.subheader("Named Businesses (Google Places)")
    st.caption(
        f"Businesses within {places_utils.SEARCH_RADIUS_KM:g} km of downtown Westfield, "
        "found via Google Places text searches, each mapped to the closest NAICS "
        "sector from its Google place type. The radius reaches into neighboring "
        "towns — use the boundary filter below to keep only Westfield proper."
    )

    if not get_secret("GOOGLE_PLACES_API_KEY"):
        st.info(
            "Add `GOOGLE_PLACES_API_KEY` to this app's secrets to enable the "
            "business search (see `.streamlit/secrets.toml.example`)."
        )
        st.stop()

    categories = st.multiselect(
        "Business categories to search",
        options=list(places_utils.SEARCH_CATEGORIES),
        default=list(places_utils.SEARCH_CATEGORIES),
    )
    run_chain_check = st.checkbox(
        "Run multi-location chain check",
        value=True,
        help="For each business not on the known-chain list, searches New Jersey "
             "for other locations with the same name (one API call per name).",
    )
    col_a, col_b = st.columns([1, 1])
    if col_a.button("Search Google Places", type="primary", disabled=not categories):
        st.session_state["places_query"] = (tuple(categories), run_chain_check)
    if col_b.button("Clear cached results",
                    help="Results are cached for a week; this forces fresh API calls."):
        places_utils.clear_places_cache()
        st.session_state.pop("places_query", None)

    if "places_query" not in st.session_state:
        st.info("Pick categories and click **Search Google Places**. "
                "Results are cached, so repeat searches don't re-bill.")
        st.stop()

    query_categories, query_chain_check = st.session_state["places_query"]
    try:
        with st.spinner("Searching Google Places..."):
            biz_df = places_utils.fetch_westfield_businesses(query_categories)
        with st.spinner(f"Classifying {len(biz_df)} businesses (chain check)..."):
            biz_df = places_utils.classify_businesses(
                biz_df, run_multi_location_check=query_chain_check
            )
    except places_utils.PlacesAPIError as e:
        st.error(str(e))
        st.stop()

    if biz_df.empty:
        st.warning("Google Places returned no businesses for those categories.")
        st.stop()

    # Flag businesses inside the actual town boundary (the search circle
    # spills into Garwood, Cranford, Scotch Plains, Mountainside).
    try:
        import geopandas as gpd
        boundary = get_westfield_boundary().to_crs("EPSG:4326").geometry.union_all()
        points = gpd.points_from_xy(biz_df["lon"], biz_df["lat"], crs="EPSG:4326")
        biz_df["in_westfield_boundary"] = points.within(boundary)
    except Exception:
        biz_df["in_westfield_boundary"] = pd.NA

    st.warning(
        "**Chain / independent labels are a heuristic, not a fact.** "
        "\"Known chain\" = name matches `known_chains.txt` (many franchises are still "
        "locally owned). \"Likely chain — multiple locations\" = the same name appears "
        f"at {places_utils.MIN_LOCATIONS}+ NJ locations in {places_utils.MIN_TOWNS}+ towns, "
        "which can also catch small local multi-shop owners or unrelated businesses with "
        "generic names. Check the note column before relying on a label."
    )

    f1, f2, f3 = st.columns([2, 2, 1])
    sector_options = sorted(biz_df["sector"].unique())
    sectors_selected = f1.multiselect("Filter by sector", sector_options, default=sector_options)
    status_options = [s for s in places_utils.FRANCHISE_LABELS
                      if s in set(biz_df["franchise_status"])]
    statuses_selected = f2.multiselect("Filter by chain / independent", status_options,
                                       default=status_options)
    boundary_known = biz_df["in_westfield_boundary"].notna().all()
    only_in_town = f3.checkbox("Inside Westfield town line only", value=False,
                               disabled=not boundary_known,
                               help=None if boundary_known else
                               "Town boundary unavailable (needs census.gov access).")

    filtered = biz_df[
        biz_df["sector"].isin(sectors_selected)
        & biz_df["franchise_status"].isin(statuses_selected)
    ]
    if only_in_town:
        filtered = filtered[filtered["in_westfield_boundary"] == True]  # noqa: E712

    m1, m2, m3 = st.columns(3)
    m1.metric("Businesses shown", len(filtered))
    m2.metric("Likely independent",
              int((filtered["franchise_status"] == places_utils.LABEL_INDEPENDENT).sum()))
    m3.metric("Chains (known + likely)",
              int(filtered["franchise_status"].isin(
                  [places_utils.LABEL_KNOWN, places_utils.LABEL_MULTI]).sum()))

    if not filtered.empty:
        fig5 = px.scatter_map(
            filtered, lat="lat", lon="lon", hover_name="name",
            hover_data={"address": True, "sector": True, "franchise_status": True,
                        "lat": False, "lon": False},
            color="franchise_status",
            category_orders={"franchise_status": places_utils.FRANCHISE_LABELS},
            zoom=13, center={"lat": 40.6589, "lon": -74.3479},
            map_style="carto-positron", height=550,
        )
        fig5.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
        st.plotly_chart(fig5, use_container_width=True)

    export_cols = [
        "name", "address", "lat", "lon", "sector", "naics_code", "franchise_status",
        "classification_note", "place_id", "sector_basis", "google_primary_type",
        "in_westfield_boundary", "distance_km", "nj_same_name_locations",
        "nj_same_name_towns", "chain_match", "search_category",
    ]
    export_df = filtered[export_cols]
    st.download_button(
        "Download filtered list (CSV)",
        data=export_df.to_csv(index=False),
        file_name="westfield_business_prospects_filtered.csv",
        mime="text/csv",
    )
    st.dataframe(export_df, use_container_width=True, hide_index=True)
