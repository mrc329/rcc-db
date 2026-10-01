"""
Google Places (New) integration for the Small Business Targeting tab.

Finds named businesses around downtown Westfield, maps each to one of the
2-digit NAICS sectors in census_utils.NAICS_SECTORS, and labels each as a
known chain, a likely chain, or likely independent.

Uses the Places API (New) Text Search endpoint — new Google Cloud projects
can't enable the legacy Places API. Enable "Places API (New)" for your key.

Cost notes: every Text Search page is one billable call (Text Search Pro
SKU, given the fields requested). A full search is roughly one call per
query per page (up to 3 pages each), plus one call per distinct business
name for the multi-location chain check. Results are cached for a week
per server process; a Streamlit Cloud app that sleeps and restarts loses
the cache, so export the CSV if you want a stable snapshot.

The franchise/chain classification is a heuristic. Google has no
"franchise" field. Treat the labels as a starting point for human review.
"""

import math
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from census_utils import NAICS_SECTORS
from config import get_secret

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

WESTFIELD_CENTER = (40.6589, -74.3479)  # downtown Westfield, NJ
SEARCH_RADIUS_KM = 3.0

# Rough New Jersey bounding box for the multi-location chain check.
# Slightly over-covers into NY/PA/DE, which is fine for spotting chains.
NJ_RECTANGLE = {
    "rectangle": {
        "low": {"latitude": 38.92, "longitude": -75.57},
        "high": {"latitude": 41.36, "longitude": -73.88},
    }
}

CACHE_TTL = 60 * 60 * 24 * 7  # one week
MAX_PAGES_PER_QUERY = 3  # Text Search caps at 60 results (3 pages of 20)
MAX_WORKERS = 8

SEARCH_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.types",
    "places.primaryType",
    "places.businessStatus",
    "nextPageToken",
])
CHAIN_CHECK_FIELD_MASK = "places.id,places.displayName,places.formattedAddress"

# What to search for. Text Search returns at most 60 results per query,
# so each category is several narrower queries; results are de-duplicated
# by Place ID. `sector` is the fallback NAICS sector when a result's
# Google place types don't map to one (see sector_for_types).
SEARCH_CATEGORIES = {
    "Finance & Insurance": {
        "sector": "52",
        "queries": [
            "bank", "credit union", "financial advisor", "wealth management",
            "investment firm", "insurance agency", "mortgage broker",
        ],
    },
    "Restaurants & Food": {
        "sector": "72",
        "queries": [
            "restaurant", "cafe", "coffee shop", "pizza", "bar", "bakery",
            "ice cream", "deli", "takeout",
        ],
    },
    "Retail": {
        "sector": "44-45",
        "queries": [
            "clothing boutique", "gift shop", "jewelry store", "book store",
            "shoe store", "home decor store", "florist", "toy store",
            "hardware store", "sporting goods store", "wine and liquor store",
            "pharmacy", "grocery store", "pet store", "furniture store",
            "art supply store", "children's clothing store", "optician",
        ],
    },
    "Professional Services": {
        "sector": "54",
        "queries": [
            "accountant", "CPA firm", "law firm", "attorney", "architect",
            "engineering firm", "marketing agency", "graphic design studio",
            "consulting firm", "tax preparation", "photography studio",
            "veterinarian",
        ],
    },
}

# --- Google place type -> NAICS sector -------------------------------------
# Google "Table A" types (https://developers.google.com/maps/documentation/places/web-service/place-types).
# Unknown types are harmless — they simply never match.

_TYPE_SECTOR = {
    # 72 Accommodation & Food Services
    **{t: "72" for t in [
        "restaurant", "cafe", "coffee_shop", "bar", "pub", "wine_bar",
        "bakery", "meal_takeaway", "meal_delivery", "ice_cream_shop",
        "sandwich_shop", "bagel_shop", "donut_shop", "juice_shop",
        "tea_house", "dessert_shop", "dessert_restaurant", "confectionery",
        "cafeteria", "food_court", "diner", "deli", "lodging", "hotel",
        "bed_and_breakfast", "chocolate_shop", "acai_shop", "cat_cafe",
    ]},
    # 52 Finance & Insurance
    **{t: "52" for t in [
        "bank", "atm", "insurance_agency", "finance", "financial_planner",
        "investment_service", "mortgage_lender", "credit_union",
    ]},
    # 54 Professional, Scientific & Technical Services
    **{t: "54" for t in [
        "accounting", "lawyer", "consultant", "tax_preparation_service",
        "marketing_agency", "architect", "engineer", "graphic_designer",
        "photographer", "veterinary_care", "advertising_agency",
    ]},
    # 53 Real Estate
    **{t: "53" for t in ["real_estate_agency", "real_estate_agent", "property_management_company"]},
    # 62 Health Care & Social Assistance
    **{t: "62" for t in [
        "doctor", "dentist", "dental_clinic", "hospital", "physiotherapist",
        "chiropractor", "medical_lab", "medical_clinic", "child_care_agency",
        "psychologist", "general_hospital", "skin_care_clinic",
    ]},
    # 81 Other Services
    **{t: "81" for t in [
        "beauty_salon", "hair_care", "hair_salon", "barber_shop", "nail_salon",
        "spa", "laundry", "dry_cleaner", "car_repair", "car_wash", "tailor",
        "funeral_home", "pet_care", "makeup_artist", "tanning_studio",
    ]},
    # 71 Arts, Entertainment & Recreation
    **{t: "71" for t in [
        "gym", "fitness_center", "art_gallery", "museum", "performing_arts_theater",
        "bowling_alley", "amusement_center", "yoga_studio", "dance_hall",
        "golf_course", "sports_club", "art_studio", "concert_hall",
    ]},
    # 44-45 Retail Trade
    **{t: "44-45" for t in [
        "pharmacy", "drugstore", "florist", "supermarket", "grocery_store",
        "market", "liquor_store", "gas_station", "car_dealer", "shopping_mall",
        "convenience_store",
    ]},
}

# Very broad types that only decide the sector when nothing more specific does.
_GENERIC_TYPE_SECTOR = {"store": "44-45", "food": "72"}


def _specific_sector(place_type):
    if not place_type:
        return None
    if place_type in _TYPE_SECTOR:
        return _TYPE_SECTOR[place_type]
    if place_type.endswith("_restaurant"):
        return "72"
    if place_type.endswith("_store") or place_type.endswith("_shop"):
        return "44-45"
    return None


def sector_for_types(primary_type, types, fallback_sector):
    """
    Map a place's Google types to a NAICS sector code.

    Returns (sector_code, basis) where basis says how it was decided, so
    users can see which assignments rest on the search category alone.
    """
    types = types or []
    for t in [primary_type, *types]:
        code = _specific_sector(t)
        if code:
            return code, "Google place type"
    for t in types:
        if t in _GENERIC_TYPE_SECTOR:
            return _GENERIC_TYPE_SECTOR[t], "Google place type (generic)"
    return fallback_sector, "search category"


# --- Places API calls ------------------------------------------------------

class PlacesAPIError(RuntimeError):
    pass


def _api_key():
    return get_secret("GOOGLE_PLACES_API_KEY")


def text_search(query, location, field_mask, max_pages=1, restrict=True,
                session=None, api_key=None):
    """
    Run a Places (New) Text Search and return the raw place dicts.

    `location` is a locationRestriction (rectangle) when restrict=True,
    else a locationBias (circle or rectangle). Pass api_key when calling
    from worker threads so they don't touch st.secrets.
    """
    key = api_key or _api_key()
    if not key:
        raise PlacesAPIError("GOOGLE_PLACES_API_KEY is not set.")
    http = session or requests
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": field_mask,
    }
    body = {
        "textQuery": query,
        "pageSize": 20,
        "locationRestriction" if restrict else "locationBias": location,
    }
    places = []
    for _ in range(max_pages):
        resp = http.post(TEXT_SEARCH_URL, json=body, headers=headers, timeout=20)
        if not resp.ok:
            try:
                message = resp.json()["error"]["message"]
            except Exception:
                message = resp.text[:300]
            raise PlacesAPIError(f"Places API HTTP {resp.status_code} for {query!r}: {message}")
        data = resp.json()
        places.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
        body = {**body, "pageToken": token}
    return places


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bounding_rectangle(center, radius_km):
    # Text Search only restricts to rectangles; results are trimmed to the
    # circle afterward.
    lat, lon = center
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return {
        "rectangle": {
            "low": {"latitude": lat - dlat, "longitude": lon - dlon},
            "high": {"latitude": lat + dlat, "longitude": lon + dlon},
        }
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_westfield_businesses(categories, center=WESTFIELD_CENTER, radius_km=SEARCH_RADIUS_KM):
    """
    Search Places for businesses within radius_km of center, for the given
    SEARCH_CATEGORIES keys (a tuple, so it's hashable for caching).

    Returns one row per Place ID with name, address, lat/lon, sector, etc.
    Permanently closed places are dropped.
    """
    rect = _bounding_rectangle(center, radius_km)
    key = _api_key()
    jobs = [
        (category, query)
        for category in categories
        for query in SEARCH_CATEGORIES[category]["queries"]
    ]

    with requests.Session() as session, ThreadPoolExecutor(MAX_WORKERS) as pool:
        results = list(pool.map(
            lambda job: text_search(job[1], rect, SEARCH_FIELD_MASK,
                                    max_pages=MAX_PAGES_PER_QUERY, session=session,
                                    api_key=key),
            jobs,
        ))

    rows = {}
    for (category, query), places in zip(jobs, results):
        for p in places:
            pid = p.get("id")
            if not pid or pid in rows:
                continue  # first category/query to find a place keeps it
            if p.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            loc = p.get("location") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if lat is None or lon is None:
                continue
            dist = haversine_km(center[0], center[1], lat, lon)
            if dist > radius_km:
                continue
            code, basis = sector_for_types(
                p.get("primaryType"), p.get("types"), SEARCH_CATEGORIES[category]["sector"]
            )
            rows[pid] = {
                "name": (p.get("displayName") or {}).get("text", ""),
                "address": p.get("formattedAddress", ""),
                "lat": lat,
                "lon": lon,
                "naics_code": code,
                "sector": NAICS_SECTORS.get(code, code),
                "sector_basis": basis,
                "google_primary_type": p.get("primaryType", ""),
                "search_category": category,
                "search_query": query,
                "distance_km": round(dist, 2),
                "business_status": p.get("businessStatus", ""),
                "place_id": pid,
            }

    df = pd.DataFrame(list(rows.values()))
    if df.empty:
        return pd.DataFrame(columns=[
            "name", "address", "lat", "lon", "naics_code", "sector", "sector_basis",
            "google_primary_type", "search_category", "search_query", "distance_km",
            "business_status", "place_id",
        ])
    return df.sort_values(["sector", "name"]).reset_index(drop=True)


# --- Chain / franchise heuristic -------------------------------------------

LABEL_KNOWN = "known chain"
LABEL_MULTI = "likely chain — multiple locations"
LABEL_INDEPENDENT = "likely independent"
LABEL_UNCHECKED = "unverified — chain check failed"
FRANCHISE_LABELS = [LABEL_KNOWN, LABEL_MULTI, LABEL_INDEPENDENT, LABEL_UNCHECKED]

# Same-name locations needed (including this one) across at least
# MIN_TOWNS distinct towns to call something a likely chain. Two
# locations is often just a local owner with a second shop.
MIN_LOCATIONS = 3
MIN_TOWNS = 2

KNOWN_CHAINS_PATH = Path(__file__).with_name("known_chains.txt")

# Words that make a same-name match unreliable on their own: "Pizza
# Palace" in five towns is usually five unrelated owners.
_GENERIC_WORDS = {
    "pizza", "pizzeria", "deli", "bagel", "bagels", "cafe", "coffee", "grill",
    "kitchen", "restaurant", "diner", "bakery", "bar", "tavern", "china",
    "chinese", "garden", "house", "palace", "express", "sushi", "thai",
    "nails", "nail", "salon", "spa", "cleaners", "dry", "liquor", "liquors",
    "wine", "wines", "market", "food", "foods", "pharmacy", "dental",
    "family", "dentistry", "law", "office", "offices", "group", "associates",
    "insurance", "agency", "realty", "tax", "services", "center", "studio",
    "shop", "store", "boutique", "florist", "flowers", "main", "street", "st",
    "and", "the", "of", "a", "llc", "inc", "co", "pc", "new", "jersey", "nj",
    "pizza", "italian", "mexican", "indian", "taqueria", "hair", "barber",
    "brothers", "bros", "sons", "cuisine", "bistro", "eatery", "ristorante",
    "trattoria", "pub", "corner", "village", "town", "best", "golden",
}


def normalize_name(name):
    """Lowercase, strip accents/apostrophes/periods, '&' -> 'and', collapse spaces."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[.'’`]", "", s)  # "T.J. Maxx" -> "tj maxx", "Kohl's" -> "kohls"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def base_name(name):
    """Drop a trailing location qualifier: 'Brand - Westfield' -> 'Brand'."""
    s = re.split(r"\s[-–—|]\s|\s\(", name or "", maxsplit=1)[0]
    return normalize_name(s)


def load_known_chains(path=KNOWN_CHAINS_PATH):
    """Parse known_chains.txt into a list of (normalized_name, exact_only)."""
    chains = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, flag = line.partition("|")
        norm = normalize_name(name)
        if norm:
            chains.append((norm, flag.strip().lower() == "exact"))
    return chains


def match_known_chain(name, chains):
    """Return the matching chain entry for a business name, or None."""
    full, base = normalize_name(name), base_name(name)
    padded = f" {full} "
    for chain, exact_only in chains:
        if full == chain or base == chain:
            return chain
        if exact_only:
            continue
        if full.startswith(chain + " ") or base.startswith(chain + " "):
            return chain
        # Multi-word brands also match mid-name, e.g. "Jane Doe - State Farm
        # Insurance Agent" or "ATM (Bank of America)".
        if " " in chain and f" {chain} " in padded:
            return chain
    return None


def is_generic_name(name):
    tokens = base_name(name).split()
    return bool(tokens) and all(t in _GENERIC_WORDS or t.isdigit() for t in tokens)


def _town_from_address(address):
    # "123 Elm St, Westfield, NJ 07090, USA" -> "westfield"
    parts = [p.strip() for p in (address or "").split(",")]
    if len(parts) >= 3:
        return parts[-3].lower()
    return (address or "").lower()


def count_same_name_locations(name, session=None, api_key=None):
    """
    Search NJ for `name` and collect results whose base name matches exactly.
    Returns (place_ids, towns) as sets.
    """
    target = base_name(name)
    places = text_search(name, NJ_RECTANGLE, CHAIN_CHECK_FIELD_MASK, max_pages=1,
                         session=session, api_key=api_key)
    ids, towns = set(), set()
    for p in places:
        if base_name((p.get("displayName") or {}).get("text", "")) == target:
            ids.add(p.get("id"))
            towns.add(_town_from_address(p.get("formattedAddress")))
    return ids, towns


# name -> (fetched_at, counts). Only successful lookups are stored, so a
# transient API error is retried on the next run instead of sticking for
# a week. Lives as long as the server process, like st.cache_data.
_chain_check_cache = {}
_chain_check_lock = threading.Lock()


def _multi_location_counts(names):
    """Run the NJ same-name check for each name. Returns {name: (counts, error)}."""
    now = time.time()
    results, pending = {}, []
    with _chain_check_lock:
        for name in names:
            hit = _chain_check_cache.get(name)
            if hit and now - hit[0] < CACHE_TTL:
                results[name] = (hit[1], None)
            else:
                pending.append(name)
    if not pending:
        return results

    key = _api_key()

    def check(name):
        try:
            return name, count_same_name_locations(name, session=session, api_key=key), None
        except Exception as e:
            return name, None, str(e)

    with requests.Session() as session, ThreadPoolExecutor(MAX_WORKERS) as pool:
        for name, counts, err in pool.map(check, pending):
            results[name] = (counts, err)
            if err is None:
                with _chain_check_lock:
                    _chain_check_cache[name] = (now, counts)
    return results


def clear_places_cache():
    """Forget cached Places results so the next search hits the API again."""
    fetch_westfield_businesses.clear()
    with _chain_check_lock:
        _chain_check_cache.clear()


def classify_businesses(df, run_multi_location_check=True):
    """
    Add franchise_status and supporting columns to a businesses DataFrame.

    franchise_status is one of FRANCHISE_LABELS:
      - "known chain": name matches known_chains.txt
      - "likely chain — multiple locations": not on the list, but the
        same name appears at MIN_LOCATIONS+ NJ locations in MIN_TOWNS+ towns
      - "likely independent": neither signal fired
      - "unverified — chain check failed": the multi-location search errored
    """
    out = df.copy()
    chains = load_known_chains()

    # The businesses themselves always count as locations, even if the
    # NJ-wide search doesn't happen to return them.
    own_locations = {}
    for name, pid, address in zip(out["name"], out["place_id"], out["address"]):
        ids, towns = own_locations.setdefault(name, (set(), set()))
        ids.add(pid)
        towns.add(_town_from_address(address))
    out["chain_match"] = [match_known_chain(n, chains) or "" for n in out["name"]]

    to_check = []
    if run_multi_location_check:
        to_check = sorted({n for n, c in zip(out["name"], out["chain_match"]) if not c})
    results = _multi_location_counts(tuple(to_check)) if to_check else {}

    statuses, loc_counts, town_counts, notes = [], [], [], []
    for name, chain in zip(out["name"], out["chain_match"]):
        generic = is_generic_name(name)
        if chain:
            statuses.append(LABEL_KNOWN)
            loc_counts.append(None)
            town_counts.append(None)
            notes.append(f"matches known-chain list entry '{chain}'")
            continue
        if not run_multi_location_check:
            statuses.append(LABEL_INDEPENDENT)
            loc_counts.append(None)
            town_counts.append(None)
            notes.append("not on known-chain list; multi-location check not run")
            continue
        counts, err = results.get(name, (None, "not checked"))
        if err:
            statuses.append(LABEL_UNCHECKED)
            loc_counts.append(None)
            town_counts.append(None)
            notes.append(f"multi-location search failed: {err}")
            continue
        ids, towns = (a | b for a, b in zip(counts, own_locations[name]))
        n_locs, n_towns, towns = len(ids), len(towns), sorted(towns)
        loc_counts.append(n_locs)
        town_counts.append(n_towns)
        if n_locs >= MIN_LOCATIONS and n_towns >= MIN_TOWNS:
            statuses.append(LABEL_MULTI)
            note = f"same name at {n_locs} NJ locations: {', '.join(towns[:6])}"
            if generic:
                note += " — generic name, may be unrelated businesses"
            notes.append(note)
        else:
            statuses.append(LABEL_INDEPENDENT)
            notes.append(f"same name found at {n_locs} NJ location(s)")

    out["franchise_status"] = statuses
    out["nj_same_name_locations"] = pd.array(loc_counts, dtype="Int64")
    out["nj_same_name_towns"] = pd.array(town_counts, dtype="Int64")
    out["classification_note"] = notes
    return out
