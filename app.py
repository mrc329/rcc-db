"""
Westfield, NJ — Giving Capacity & Small Business Targeting Dashboard

Built for Rialto Center for Creativity capital campaign prospect research.

Two data layers:
  1. Census ACS income + demographics, mapped by block group (GIS layer)
  2. Small business targeting by NAICS sector — Census gives aggregate
     establishment COUNTS only (no names/addresses, by design). For an
     actual named prospect list, upload your own business roster below
     (e.g. Chamber of Commerce membership list) and the app will map
     and filter it alongside the income data.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from census_utils import (
    fetch_block_group_acs,
    fetch_naics_establishment_counts,
    fetch_naics_employment_size_class,
    NAICS_SECTORS,
)
from geometry_utils import get_westfield_block_groups

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
        fig = px.choropleth_mapbox(
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
            mapbox_style="carto-positron",
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
        "some may extend slightly beyond the town line."
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
    st.subheader("Upload Your Own Business Prospect List")
    st.markdown(
        """
        For an actual **named** prospect list, upload a CSV with your own
        business roster (e.g. from the Westfield Chamber of Commerce,
        a membership directory, or a manually compiled list). Required
        columns:

        - `name` — business name
        - `address` — street address
        - `lat`, `lon` — coordinates (geocode addresses first if you don't have these —
          the free Census Geocoder at https://geocoding.geo.census.gov can batch-geocode a CSV)
        - `naics_sector` — one of: """ + ", ".join(NAICS_SECTORS.values())
    )

    uploaded = st.file_uploader("Upload business list (CSV)", type="csv")
    if uploaded:
        biz_df = pd.read_csv(uploaded)
        required_cols = {"name", "address", "lat", "lon", "naics_sector"}
        missing = required_cols - set(biz_df.columns)
        if missing:
            st.error(f"Missing required columns: {missing}")
        else:
            sectors_selected = st.multiselect(
                "Filter by sector", options=biz_df["naics_sector"].unique().tolist(),
                default=biz_df["naics_sector"].unique().tolist(),
            )
            filtered = biz_df[biz_df["naics_sector"].isin(sectors_selected)]

            fig5 = px.scatter_mapbox(
                filtered, lat="lat", lon="lon", hover_name="name",
                hover_data=["address", "naics_sector"],
                color="naics_sector", zoom=12.5,
                center={"lat": 40.6589, "lon": -74.3479},
                mapbox_style="carto-positron", height=550,
            )
            fig5.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
            st.plotly_chart(fig5, use_container_width=True)

            st.download_button(
                "Download filtered prospect list (CSV)",
                data=filtered.to_csv(index=False),
                file_name="westfield_business_prospects_filtered.csv",
                mime="text/csv",
            )
            st.dataframe(filtered)
