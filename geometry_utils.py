"""
GIS boundary helpers, built on `pygris` (which fetches Census TIGER/Line
cartographic boundary files at runtime).

This requires outbound internet access to census.gov at runtime, which
Streamlit Community Cloud has. (It could not be live-tested from the
dev sandbox this app was authored in, since that sandbox's network
allowlist doesn't include census.gov — test this on first deploy.)
"""

import streamlit as st
import geopandas as gpd
from pygris import block_groups, places

from census_utils import STATE_FIPS, COUNTY_FIPS, PLACE_FIPS


@st.cache_data(show_spinner="Loading Westfield boundary...")
def get_westfield_boundary():
    """Fetch the Westfield town place boundary polygon."""
    nj_places = places(state=STATE_FIPS, cache=True, year=2022)
    westfield = nj_places[nj_places["PLACEFP"] == PLACE_FIPS]
    if westfield.empty:
        raise ValueError(
            f"Could not find place FIPS {PLACE_FIPS} in state {STATE_FIPS} "
            "place shapefile — the place boundary layer may have changed."
        )
    return westfield


@st.cache_data(show_spinner="Loading block group boundaries...")
def get_union_county_block_groups():
    """Fetch all block group polygons for Union County, NJ."""
    return block_groups(state=STATE_FIPS, county=COUNTY_FIPS, cache=True, year=2022)


def get_westfield_block_groups():
    """
    Spatially clip Union County block groups down to the ones that
    intersect Westfield's town boundary. A block group is included if
    any part of it overlaps the town — some included block groups may
    extend slightly beyond the town line, since block group boundaries
    don't perfectly align with town boundaries.
    """
    westfield = get_westfield_boundary()
    bgs = get_union_county_block_groups()

    bgs = bgs.to_crs(westfield.crs)
    westfield_bgs = gpd.sjoin(bgs, westfield[["geometry"]], how="inner", predicate="intersects")
    westfield_bgs = westfield_bgs.drop(columns=["index_right"])
    return westfield_bgs
