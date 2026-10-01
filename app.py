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
import plotly.graph_objects as go

from census_utils import (
    INCOME_BUCKETS,
    MEDIAN_INCOME_TOPCODE,
    estimate_median_from_brackets,
    fetch_block_group_acs,
    fetch_naics_establishment_counts,
    fetch_naics_employment_size_class,
)
from config import get_secret
from geometry_utils import get_westfield_block_groups, get_westfield_boundary
import irs_utils
import places_utils
import tracker_utils

st.set_page_config(page_title="Westfield Giving & Business Dashboard", layout="wide")

st.title("Westfield, NJ — Giving Capacity & Small Business Dashboard")
st.caption(
    "Prospect research for the Rialto Center for Creativity capital campaign — "
    "live performance, cultural conversations and hands-on learning, opening 2029. "
    "Census ACS giving capacity and audience data by block group, IRS giving by ZIP, "
    "and small-business sponsor targeting."
)

tab_map, tab_demo, tab_irs, tab_biz = st.tabs(
    ["Giving Capacity Map", "Demographics", "Charitable Giving by ZIP", "Small Business Targeting"]
)

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
MAP_METRICS = {
    "Households earning $200k+": ("hh_200k_plus", ":,.0f"),
    "Households earning $150k+": ("hh_150k_plus", ":,.0f"),
    "Share of households earning $200k+ (%)": ("pct_hh_200k_plus", ":.0f"),
    "Owner-occupied homes worth $1M+": ("homes_1m_plus", ":,.0f"),
    "Median household income": ("median_household_income", ":$,.0f"),
    "Children under 12 (learners in 2029)": ("kids_under_12", ":,.0f"),
    "Adults 65+ (live-performance audience)": ("adults_65_plus", ":,.0f"),
    "Arts, design & media workers": ("arts_workers", ":,.0f"),
}

with tab_map:
    st.subheader("Giving Capacity by Block Group")
    metric_label = st.radio(
        "Color block groups by", list(MAP_METRICS), horizontal=True,
        help="Counts of high-income households are the better major-gift signal: "
             "ACS caps median income at $250,000, so in Westfield many block "
             "groups hit the cap and look identical on a median map.",
    )
    metric_col, metric_fmt = MAP_METRICS[metric_label]
    if merged is not None:
        fig = px.choropleth_map(
            merged,
            geojson=merged.geometry.__geo_interface__,
            locations=merged.index,
            color=metric_col,
            hover_name="NAME",
            hover_data={
                "hh_200k_plus": ":,.0f",
                "pct_hh_200k_plus": ":.0f",
                "homes_1m_plus": ":,.0f",
                "households_total": ":,.0f",
                "median_household_income": ":$,.0f",
                "total_population": True,
            },
            map_style="carto-positron",
            center={"lat": 40.6589, "lon": -74.3479},  # Westfield, NJ
            zoom=12.5,
            opacity=0.65,
            color_continuous_scale="Greens",
            labels={
                metric_col: metric_label,
                "hh_200k_plus": "Households $200k+",
                "pct_hh_200k_plus": "% households $200k+",
                "homes_1m_plus": "Homes $1M+",
                "households_total": "Total households",
                "median_household_income": "Median HH income",
                "total_population": "Population",
            },
        )
        fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0}, height=600)
        st.plotly_chart(fig, width="stretch")

        n_capped = int((merged["median_household_income"] >= MEDIAN_INCOME_TOPCODE).sum())
        if metric_col == "median_household_income" and n_capped:
            st.caption(
                f"{n_capped} of {len(merged)} block groups are at the ACS cap "
                "($250,000+), so their true medians are higher and indistinguishable here."
            )
    else:
        st.info("Map unavailable — see warning above.")

    if merged is not None:
        st.subheader("Block Groups Ranked for Outreach")
        st.caption(
            "Where to canvass or mail first. Counts are ACS estimates with real margins "
            "of error at this scale — treat close ranks as ties. \"% in Westfield\" is the "
            "share of the block group's land inside the town line; low values mean most "
            "of its households are in a neighboring town."
        )
        rank_options = {
            "Households earning $200k+": "hh_200k_plus",
            "Homes worth $1M+": "homes_1m_plus",
            "Share of households earning $200k+": "pct_hh_200k_plus",
            "Children under 12 (class outreach)": "kids_under_12",
            "Adults 65+ (audience outreach)": "adults_65_plus",
        }
        rank_by = st.selectbox("Rank by", list(rank_options))
        ranked = (
            merged.drop(columns="geometry")
            .sort_values(rank_options[rank_by], ascending=False, na_position="last")
            .reset_index(drop=True)
        )
        ranked.insert(0, "rank", range(1, len(ranked) + 1))
        ranked["median_income_display"] = [
            "n/a" if pd.isna(v) else ("$250k+ (capped)" if v >= MEDIAN_INCOME_TOPCODE else f"${v:,.0f}")
            for v in ranked["median_household_income"]
        ]
        rank_cols = {
            "rank": "Rank",
            "NAME": "Block group",
            "pct_area_in_westfield": "% in Westfield",
            "households_total": "Households",
            "hh_200k_plus": "HH $200k+",
            "pct_hh_200k_plus": "% HH $200k+",
            "homes_1m_plus": "Homes $1M+",
            "pct_homes_1m_plus": "% owner homes $1M+",
            "kids_under_12": "Kids <12",
            "adults_65_plus": "Adults 65+",
            "arts_workers": "Arts workers",
            "median_income_display": "Median HH income",
            "GEOID": "GEOID",
        }
        ranked_view = ranked[[c for c in rank_cols if c in ranked.columns]].rename(columns=rank_cols)
        st.dataframe(
            ranked_view, hide_index=True, width="stretch",
            column_config={
                "% HH $200k+": st.column_config.NumberColumn(format="%.0f%%"),
                "% owner homes $1M+": st.column_config.NumberColumn(format="%.0f%%"),
                "% in Westfield": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )
        st.download_button(
            "Download ranked block groups (CSV)", data=ranked_view.to_csv(index=False),
            file_name="westfield_block_groups_ranked.csv", mime="text/csv",
        )

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
        st.plotly_chart(fig2, width="stretch")

    st.subheader("Demographic Summary")
    if merged is not None:
        _, median_label = estimate_median_from_brackets(merged[list(INCOME_BUCKETS.values())].sum())
        hh_total = merged["households_total"].sum()
        hh_200k = merged["hh_200k_plus"].sum()

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Population", f"{merged['total_population'].sum():,.0f}")
        col2.metric("Households", f"{hh_total:,.0f}")
        col3.metric("Households earning $200k+", f"{hh_200k:,.0f}",
                    help=f"{hh_200k / hh_total * 100:.0f}% of households" if hh_total else None)
        col4.metric("Median HH Income (est.)", median_label,
                    help="Interpolated from the combined income-bracket counts of all "
                         "block groups shown. If it reads $200k+, the median is in the "
                         "top bracket and ACS doesn't publish anything finer.")

        col5, col6, col7 = st.columns(3)
        col5.metric("Median Age (typical block group)", f"{merged['median_age'].median():.1f}",
                    help="Median of block-group medians — a rough guide, not a town-wide median.")
        col6.metric("Median Home Value (typical block group)",
                    f"${merged['median_home_value'].median():,.0f}",
                    help="Median of block-group medians. ACS caps home values at $2,000,000+.")
        col7.metric("Avg Household Size",
                    f"{merged['avg_household_size'].mean():.2f}",
                    help="Simple average across block groups.")

        race_totals = merged[["white_alone", "black_alone", "asian_alone", "hispanic_latino"]].sum()
        fig3 = px.pie(values=race_totals.values, names=race_totals.index,
                       title="Race / Ethnicity Breakdown (not mutually exclusive categories)")
        st.plotly_chart(fig3, width="stretch")

    if merged is not None:
        st.subheader("Professional / Prospect-Research Indicators")

        def pct(numerator, denominator):
            """Percent from summed counts, or None if the data didn't load."""
            num, den = merged[numerator].sum(min_count=1), merged[denominator].sum(min_count=1)
            if pd.isna(num) or pd.isna(den) or den == 0:
                return None
            return num / den * 100

        def fmt(value):
            return "n/a" if value is None else f"{value:.0f}%"

        merged["edu_ba_plus"] = (merged["edu_bachelors"] + merged["edu_masters"]
                                 + merged["edu_professional"] + merged["edu_doctorate"])
        edu_pct = pct("edu_ba_plus", "edu_total_pop_25plus")
        transit_pct = pct("commute_public_transit", "commute_total_workers")
        stayed_pct = pct("mobility_same_house_1yr_ago", "mobility_total_pop_1yr")
        mgmt_pct = pct("occupation_mgmt_business_science_arts", "occupation_total_employed")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bachelor's+ (25+)", fmt(edu_pct))
        c2.metric("Commute via Public Transit", fmt(transit_pct),
                   help="Rough proxy for NYC rail-commuting professionals, not actual commute destination")
        c3.metric("Same Residence vs. 1 Yr Ago", fmt(stayed_pct),
                   help="Proxy for civic tenure/rootedness")
        c4.metric("Mgmt/Business/Science/Arts Occupations", fmt(mgmt_pct))

        acs_missing = acs_df.attrs.get("acs_missing", {})
        if acs_missing:
            st.caption(
                "n/a = the Census API doesn't publish that table at block-group level "
                "(or it failed to load): "
                + "; ".join(f"**{group}** — {reason}" for group, reason in acs_missing.items())
            )

    if merged is not None:
        st.subheader("Mission Fit: Audiences & Learners")
        st.caption(
            "Who the Rialto's three program areas would serve, from the same block groups. "
            "ACS data describes roughly 2020–2024; by the 2029 opening, today's under-12s "
            "will be about 3–15 — the core age range for youth classes."
        )

        def count(col):
            total = merged[col].sum(min_count=1)
            return "n/a" if pd.isna(total) else f"{total:,.0f}"

        def with_share(num, den):
            """'1,234 · 18%' — count plus its share of `den` (no delta arrow)."""
            n, d = merged[num].sum(min_count=1), merged[den].sum(min_count=1)
            if pd.isna(n):
                return "n/a"
            if pd.isna(d) or d == 0:
                return f"{n:,.0f}"
            return f"{n:,.0f} · {n / d * 100:.0f}%"

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Children under 12", count("kids_under_12"),
                  help="Hands-on learning: youth class prospects (ages ~3–15 in 2029).")
        k2.metric("Households with children",
                  with_share("households_with_kids", "households_total"),
                  help="Count · share of all households. Parents of students are often "
                       "a program's first donors.")
        k3.metric("Adults 65+", with_share("adults_65_plus", "total_population"),
                  help="Count · share of population. Live performance: older adults are the "
                       "core subscriber audience, and have time for daytime programs and "
                       "volunteering.")
        k4.metric("Arts, design & media workers",
                  with_share("arts_workers", "occupation_total_employed"),
                  help="Count · share of employed residents. Creative community: potential "
                       "teaching artists, collaborators, and peer advocates.")
        mission_missing = {
            g: r for g, r in acs_df.attrs.get("acs_missing", {}).items()
            if g in ("children under 12", "households with children", "adults 65+", "arts workers")
        }
        if mission_missing:
            st.caption("n/a: " + "; ".join(f"**{g}** — {r}" for g, r in mission_missing.items()))

    with st.expander("Raw block group data"):
        st.dataframe(acs_df)

# ---- Tab: Charitable giving by ZIP (IRS) ----
with tab_irs:
    st.subheader("Charitable Deductions Claimed, by ZIP Code")
    st.warning(
        "**Read these as a floor, not a measure of total giving.** IRS figures only "
        "include returns that itemized deductions — since the 2018 tax law change most "
        "filers don't, so this undercounts giving and skews toward higher-income "
        "households. ZIP 07090 isn't exactly the town line, and the data runs a few "
        "years behind."
    )
    if st.button("Load IRS ZIP data",
                 help="Downloads a national IRS file once (tens of MB) and keeps "
                      "New Jersey; cached afterward.") or st.session_state.get("irs_loaded"):
        st.session_state["irs_loaded"] = True
        try:
            with st.spinner("Downloading IRS ZIP-code data (one-time)..."):
                irs_df = irs_utils.fetch_nj_zip_giving()
        except Exception as e:
            st.error(f"Couldn't load IRS data: {e}")
            irs_df = None

        if irs_df is not None:
            all_zips = sorted(irs_df["zip"])
            default_zips = [z for z in irs_utils.COMPARISON_ZIPS if z in set(all_zips)]
            chosen = st.multiselect(
                "ZIP codes to compare", all_zips, default=default_zips,
                format_func=lambda z: f"{z} {irs_utils.COMPARISON_ZIPS.get(z, '')}".strip(),
            )
            cmp = irs_df[irs_df["zip"].isin(chosen)].copy()
            cmp["label"] = (cmp["zip"] + " " + cmp["town"]).str.strip()
            cmp["is_home"] = cmp["zip"] == irs_utils.HOME_ZIP

            home = irs_df[irs_df["zip"] == irs_utils.HOME_ZIP]
            if not home.empty:
                h = home.iloc[0]
                k1, k2, k3 = st.columns(3)
                k1.metric("Westfield (07090): returns claiming charity",
                          f"{h['pct_returns_claiming_charity']:.0f}%",
                          help=f"{h['returns_claiming_charity']:,.0f} of {h['returns']:,.0f} returns")
                k2.metric("Avg. deduction per claiming return",
                          f"${h['avg_gift_per_claiming_return']:,.0f}")
                k3.metric("Total charitable deductions claimed",
                          f"${h['charity_total'] / 1e6:,.1f}M")

            if not cmp.empty:
                metric_choice = st.radio(
                    "Compare", ["Avg. deduction per claiming return",
                                "% of returns claiming charity",
                                "Charity as % of AGI (all returns)"],
                    horizontal=True,
                )
                col = {"Avg. deduction per claiming return": "avg_gift_per_claiming_return",
                       "% of returns claiming charity": "pct_returns_claiming_charity",
                       "Charity as % of AGI (all returns)": "charity_pct_of_agi"}[metric_choice]
                fig_irs = px.bar(
                    cmp.sort_values(col, ascending=False), x="label", y=col, color="is_home",
                    color_discrete_map={True: "#1b7f3b", False: "#9bb5a2"},
                    labels={"label": "", col: metric_choice, "is_home": "Westfield"},
                )
                fig_irs.update_layout(showlegend=False, xaxis_tickangle=-30)
                st.plotly_chart(fig_irs, width="stretch")

                table = cmp.sort_values(col, ascending=False)[[
                    "zip", "town", "returns", "returns_claiming_charity",
                    "pct_returns_claiming_charity", "avg_gift_per_claiming_return",
                    "charity_total", "avg_agi_per_return", "charity_pct_of_agi",
                ]]
                st.dataframe(
                    table, hide_index=True, width="stretch",
                    column_config={
                        "pct_returns_claiming_charity": st.column_config.NumberColumn(
                            "% claiming", format="%.0f%%"),
                        "avg_gift_per_claiming_return": st.column_config.NumberColumn(
                            "Avg. deduction", format="dollar"),
                        "charity_total": st.column_config.NumberColumn(
                            "Total deductions", format="dollar"),
                        "avg_agi_per_return": st.column_config.NumberColumn(
                            "Avg. AGI / return", format="dollar"),
                        "charity_pct_of_agi": st.column_config.NumberColumn(
                            "Charity % of AGI", format="%.2f%%"),
                    },
                )
            st.caption(
                f"Source: IRS SOI ZIP Code data, tax year {irs_df.attrs.get('tax_year', '?')} "
                f"({irs_df.attrs.get('source', '')}). Amounts are as reported by the IRS "
                "(rounded; small ZIPs may be suppressed)."
            )
    else:
        st.info("Click **Load IRS ZIP data** to fetch it (one-time download, then cached).")

# ---- Tab 3: Small business targeting ----
with tab_biz:
    st.subheader("NAICS Sector Establishment Counts (Census — aggregate only)")
    st.caption(
        "⚠️ Census County/ZIP Business Patterns gives establishment COUNTS by "
        "sector, not business names or addresses (disclosure avoidance). "
        "Use this to understand sector composition, not as a contact list."
    )
    naics_df = fetch_naics_establishment_counts()
    st.dataframe(naics_df, width="stretch")

    fig4 = px.bar(naics_df.dropna(subset=["establishments"]),
                   x="sector", y="establishments",
                   title=f"Establishments by Sector — ZIP {naics_df.attrs.get('zip', '07090')}")
    fig4.update_layout(xaxis_tickangle=-30)
    st.plotly_chart(fig4, width="stretch")

    st.divider()
    st.subheader("Establishment Size by Sector (Union County-wide)")
    st.caption(
        "⚠️ Not Westfield-specific — employment size class isn't reliably exposed "
        "at the ZIP level, so this reflects all of Union County. Useful for "
        "understanding sector composition (e.g. are retail businesses mostly "
        "solo operations or do they employ staff), not for precise local counts."
    )
    size_df = fetch_naics_employment_size_class()
    st.dataframe(size_df, width="stretch")

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

    # Distance to the Rialto: walkable businesses get the strongest pitch
    # (theater audiences become their customers).
    rialto_lat, rialto_lon, rialto_source = places_utils.locate_rialto()
    biz_df = places_utils.add_rialto_distance(biz_df, rialto_lat, rialto_lon)
    if rialto_source.startswith("approximate"):
        st.warning(f"Rialto location is {rialto_source}; distances are rough.")

    # Contact tracker
    store, store_error = tracker_utils.get_store()
    if store_error:
        st.error(store_error + " Falling back to a session-only tracker.")
    try:
        tracker = tracker_utils.load_tracker(store)
    except Exception as e:
        st.error(f"Couldn't read the contact tracker: {e}")
        tracker = tracker_utils.SessionStore().load()
    biz_df = tracker_utils.attach_tracker(biz_df, tracker)

    st.warning(
        "**Chain / independent labels are a heuristic, not a fact.** "
        "\"Known chain\" = name matches `known_chains.txt` (many franchises are still "
        "locally owned). \"Likely chain — multiple locations\" = the same name appears "
        f"at {places_utils.MIN_LOCATIONS}+ NJ locations in {places_utils.MIN_TOWNS}+ towns, "
        "which can also catch small local multi-shop owners or unrelated businesses with "
        "generic names. Check the note column before relying on a label."
    )

    f1, f2, f3 = st.columns([2, 2, 2])
    sector_options = sorted(biz_df["sector"].unique())
    sectors_selected = f1.multiselect("Filter by sector", sector_options, default=sector_options)
    status_options = [s for s in places_utils.FRANCHISE_LABELS
                      if s in set(biz_df["franchise_status"])]
    statuses_selected = f2.multiselect("Filter by chain / independent", status_options,
                                       default=status_options)
    contact_selected = f3.multiselect(
        "Filter by contact status", tracker_utils.STATUSES, default=tracker_utils.OPEN_STATUSES,
        help="Donors, declines and do-not-contacts are hidden by default.",
    )
    g1, g2 = st.columns(2)
    boundary_known = biz_df["in_westfield_boundary"].notna().all()
    only_in_town = g1.checkbox("Inside Westfield town line only", value=False,
                               disabled=not boundary_known,
                               help=None if boundary_known else
                               "Town boundary unavailable (needs census.gov access).")
    only_walkable = g2.checkbox(
        f"Within walking distance of the Rialto (~{places_utils.WALKING_DISTANCE_M} m)",
        value=False,
        help="Straight-line distance, so actual walks are a bit longer.",
    )

    filtered = biz_df[
        biz_df["sector"].isin(sectors_selected)
        & biz_df["franchise_status"].isin(statuses_selected)
        & biz_df["status"].isin(contact_selected)
    ]
    if only_in_town:
        filtered = filtered[filtered["in_westfield_boundary"] == True]  # noqa: E712
    if only_walkable:
        filtered = filtered[filtered["walk_to_rialto"]]
    filtered = filtered.sort_values("meters_to_rialto").reset_index(drop=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Businesses shown", len(filtered))
    m2.metric("Likely independent",
              int((filtered["franchise_status"] == places_utils.LABEL_INDEPENDENT).sum()))
    m3.metric("Walkable to the Rialto", int(filtered["walk_to_rialto"].sum()))
    m4.metric("Hidden by contact status",
              int((~biz_df["status"].isin(contact_selected)).sum()))

    if not filtered.empty:
        fig5 = px.scatter_map(
            filtered, lat="lat", lon="lon", hover_name="name",
            hover_data={"address": True, "sector": True, "franchise_status": True,
                        "status": True, "meters_to_rialto": True,
                        "lat": False, "lon": False},
            color="franchise_status",
            category_orders={"franchise_status": places_utils.FRANCHISE_LABELS},
            zoom=13.5, center={"lat": rialto_lat, "lon": rialto_lon},
            map_style="carto-positron", height=550,
        )
        fig5.add_trace(go.Scattermap(
            lat=[rialto_lat], lon=[rialto_lon], mode="markers+text",
            marker={"size": 16, "color": "black"}, text=["Rialto"],
            textposition="top right", name="Rialto", hoverinfo="name",
        ))
        fig5.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
        st.plotly_chart(fig5, width="stretch")

    st.markdown(
        f"**Contact tracker** — saved to: *{store.description}*. "
        "Edit **status** and **notes** in the table, then click **Save changes**. "
        "Save before changing filters, or unsaved edits are lost."
    )
    table_cols = [
        "name", "status", "notes", "address", "meters_to_rialto", "sector",
        "franchise_status", "classification_note", "in_westfield_boundary",
        "updated_at", "place_id",
    ]
    view = filtered[table_cols]
    edited = st.data_editor(
        view,
        column_config={
            "status": st.column_config.SelectboxColumn(
                "status", options=tracker_utils.STATUSES, required=True),
            "notes": st.column_config.TextColumn("notes", width="medium"),
            "meters_to_rialto": st.column_config.NumberColumn("m to Rialto", format="%d"),
        },
        disabled=[c for c in table_cols if c not in ("status", "notes")],
        hide_index=True,
        width="stretch",
        # A new key whenever the visible rows change, so pending edits
        # can't land on the wrong business after a filter change.
        key="tracker_editor_" + str(hash(tuple(view["place_id"]))),
    )
    changes = tracker_utils.changed_rows(view, edited)
    if st.button(f"Save changes ({len(changes)})", disabled=changes.empty, type="primary"):
        try:
            store.upsert(changes)
            tracker_utils.load_tracker.clear()
            st.success(f"Saved {len(changes)} change(s).")
            st.rerun()
        except Exception as e:
            st.error(f"Save failed — nothing was written: {e}")

    if not store.persistent:
        with st.expander("Tracker not shared — load or save it as a CSV"):
            st.caption(
                "Without the Google Sheet configured (see README), the tracker lives "
                "only in this browser session. Download it before closing, and upload "
                "it next time."
            )
            st.download_button(
                "Download tracker (CSV)", data=tracker_utils.SessionStore().load().to_csv(index=False),
                file_name="rialto_contact_tracker.csv", mime="text/csv",
            )
            uploaded = st.file_uploader("Upload a tracker CSV", type="csv")
            if uploaded is not None and st.button("Load uploaded tracker"):
                store.upsert(pd.read_csv(uploaded, dtype=str))
                st.rerun()

    export_cols = [
        "name", "address", "lat", "lon", "sector", "naics_code", "franchise_status",
        "classification_note", "status", "notes", "meters_to_rialto", "walk_to_rialto",
        "place_id", "sector_basis", "google_primary_type", "in_westfield_boundary",
        "distance_km", "nj_same_name_locations", "nj_same_name_towns", "chain_match",
        "search_category",
    ]
    st.download_button(
        "Download filtered list (CSV)",
        data=filtered[export_cols].to_csv(index=False),
        file_name="westfield_business_prospects_filtered.csv",
        mime="text/csv",
    )
