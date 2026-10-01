"""
Offline stand-ins for census.gov and the Google Places API, returning
responses in the same shape the real services do. Used because the dev
sandbox can't reach either service; the deployed app is the live test.
"""

import json
from urllib.parse import urlparse, parse_qs

import geopandas as gpd
from shapely.geometry import box

CENTER_LAT, CENTER_LON = 40.6589, -74.3479


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


# --- Census -----------------------------------------------------------------

BLOCK_GROUPS = [  # (tract, block group) — fake but well-formed
    ("030100", "1"), ("030100", "2"), ("030200", "1"), ("030200", "2"),
]


def fake_census_get(url, timeout=None, **kwargs):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    path = parsed.path

    if "/acs/acs5" in path:
        if "/2024/" in path:  # simulate the newest vintage not being live yet
            return FakeResponse(404, text="unknown dataset")
        get_vars = qs["get"][0].split(",")
        header = get_vars + ["state", "county", "tract", "block group"]
        rows = [header]
        for i, (tract, bg) in enumerate(BLOCK_GROUPS):
            vals = []
            for v in get_vars:
                if v == "NAME":
                    vals.append(f"Block Group {bg}; Census Tract {tract}; Union County; New Jersey")
                elif v == "B19013_001E":
                    vals.append(str(180000 + i * 25000))
                elif v == "B25010_001E":
                    vals.append("2.9")
                elif v == "B01002_001E":
                    vals.append("41.2")
                else:
                    vals.append(str(100 + i * 10))
            rows.append(vals + ["34", "039", tract, bg])
        return FakeResponse(200, rows)

    if path.endswith("/cbp"):
        if "/2023/" in path:
            return FakeResponse(404, text="unknown dataset")
        if "NAICS2022" in qs:
            return FakeResponse(400, text="error: unknown predicate variable: 'NAICS2022'")
        if "zip code:07090" in qs["for"][0]:
            return FakeResponse(200, [["ESTAB", "NAICS2017", "zip code"], ["42", qs["NAICS2017"][0], "07090"]])
        return FakeResponse(200, [
            ["ESTAB", "EMPSZES", "EMPSZES_LABEL", "NAICS2017", "state", "county"],
            ["900", "001", "All establishments", qs["NAICS2017"][0], "34", "039"],
            ["500", "212", "Establishments with 1 to 4 employees", qs["NAICS2017"][0], "34", "039"],
            ["400", "220", "Establishments with 5 to 9 employees", qs["NAICS2017"][0], "34", "039"],
        ])

    return FakeResponse(404, text="not faked")


def fake_pygris_places(state=None, cache=None, year=None, **kwargs):
    d = 0.02
    return gpd.GeoDataFrame(
        {"PLACEFP": ["79040", "26070"], "NAME": ["Westfield", "Garwood"]},
        geometry=[
            box(CENTER_LON - d, CENTER_LAT - d, CENTER_LON + d, CENTER_LAT + d),
            box(CENTER_LON + d, CENTER_LAT - d, CENTER_LON + 2 * d, CENTER_LAT + d),
        ],
        crs="EPSG:4269",
    )


def fake_pygris_block_groups(state=None, county=None, cache=None, year=None, **kwargs):
    d = 0.02
    geoms, geoids = [], []
    for i, (tract, bg) in enumerate(BLOCK_GROUPS):
        x0 = CENTER_LON - d + (i % 2) * d
        y0 = CENTER_LAT - d + (i // 2) * d
        geoms.append(box(x0, y0, x0 + d, y0 + d))
        geoids.append(f"34039{tract}{bg}")
    # One block group far away that must be clipped out
    geoms.append(box(-74.0, 40.9, -73.99, 40.91))
    geoids.append("340399999991")
    return gpd.GeoDataFrame({"GEOID": geoids}, geometry=geoms, crs="EPSG:4269")


# --- Google Places ----------------------------------------------------------

def _place(pid, name, lat, lon, address, primary_type, types, status="OPERATIONAL"):
    return {
        "id": pid,
        "displayName": {"text": name, "languageCode": "en"},
        "formattedAddress": address,
        "location": {"latitude": lat, "longitude": lon},
        "primaryType": primary_type,
        "types": types,
        "businessStatus": status,
    }


LOCAL_PLACES = {
    "bank": [
        _place("p_chase", "Chase Bank", 40.6590, -74.3470, "100 Elm St, Westfield, NJ 07090, USA",
               "bank", ["bank", "atm", "finance", "point_of_interest", "establishment"]),
        _place("p_tri", "Tri-County Savings", 40.6580, -74.3490, "5 Quimby St, Westfield, NJ 07090, USA",
               "bank", ["bank", "finance", "point_of_interest", "establishment"]),
    ],
    "insurance agency": [
        _place("p_sf", "Jane Doe - State Farm Insurance Agent", 40.6600, -74.3460,
               "200 North Ave W, Westfield, NJ 07090, USA", "insurance_agency",
               ["insurance_agency", "point_of_interest", "establishment"]),
    ],
    "restaurant": [
        _place("p_rist", "Ristorante Mezzaluna", 40.6585, -74.3485, "12 Elm St, Westfield, NJ 07090, USA",
               "italian_restaurant", ["italian_restaurant", "restaurant", "food", "point_of_interest"]),
        _place("p_gr", "Garwood Grill", 40.6595, -74.3270, "300 South Ave, Garwood, NJ 07027, USA",
               "american_restaurant", ["american_restaurant", "restaurant", "food"]),
        _place("p_far", "Far Away Diner", 40.75, -74.20, "1 Main St, Newark, NJ 07102, USA",
               "diner", ["diner", "restaurant", "food"]),
        _place("p_closed", "Gone Bistro", 40.6588, -74.3475, "9 Elm St, Westfield, NJ 07090, USA",
               "restaurant", ["restaurant", "food"], status="CLOSED_PERMANENTLY"),
    ],
    "coffee shop": [
        _place("p_sbux", "Starbucks", 40.6591, -74.3481, "50 Elm St, Westfield, NJ 07090, USA",
               "coffee_shop", ["coffee_shop", "cafe", "food", "store"]),
        _place("p_kc", "Kitchen Corner Cafe", 40.6575, -74.3500, "70 Central Ave, Westfield, NJ 07090, USA",
               "cafe", ["cafe", "food"]),
    ],
    "clothing boutique": [
        _place("p_bout", "The Leaf Boutique", 40.6592, -74.3478, "60 Elm St, Westfield, NJ 07090, USA",
               "clothing_store", ["clothing_store", "store", "point_of_interest"]),
        _place("p_tj", "T.J. Maxx", 40.6560, -74.3420, "400 South Ave, Westfield, NJ 07090, USA",
               "department_store", ["department_store", "clothing_store", "store"]),
    ],
    "accountant": [
        _place("p_cpa", "Smith & Rao CPAs", 40.6597, -74.3490, "1 Prospect St, Westfield, NJ 07090, USA",
               "accounting", ["accounting", "finance", "point_of_interest"]),
        _place("p_odd", "Mystery Holdings", 40.6599, -74.3492, "2 Prospect St, Westfield, NJ 07090, USA",
               None, ["point_of_interest", "establishment"]),
    ],
}

LOCAL_PLACES["Rialto Theatre, 250 E Broad St, Westfield, NJ 07090"] = [
    _place("p_rialto", "Rialto Theatre", 40.6503, -74.3447, "250 E Broad St, Westfield, NJ 07090, USA",
           "performing_arts_theater", ["performing_arts_theater"]),
]

# Same-name hits across NJ for the multi-location check.
NJ_SAME_NAME = {
    "Tri-County Savings": ["Westfield", "Cranford", "Summit", "Clark"],  # regional mini-chain
    "Ristorante Mezzaluna": ["Westfield"],
    "Kitchen Corner Cafe": ["Westfield", "Toms River", "Paramus"],  # generic name
    "The Leaf Boutique": ["Westfield", "Westfield"],  # one town only
    "Smith & Rao CPAs": ["Westfield"],
    "Mystery Holdings": ["Westfield"],
}


class FakePlaces:
    """Fake for requests.post / Session.post against places:searchText."""

    def __init__(self, fail_names=()):
        self.calls = []
        self.fail_names = set(fail_names)

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        assert headers["X-Goog-Api-Key"], "missing API key header"
        assert headers["X-Goog-FieldMask"], "missing field mask"
        query = json["textQuery"]
        rect = json["locationRestriction"]["rectangle"]
        is_nj_wide = rect["high"]["latitude"] - rect["low"]["latitude"] > 1

        if is_nj_wide:
            if query in self.fail_names:
                return FakeResponse(429, {"error": {"code": 429, "message": "Quota exceeded"}})
            towns = NJ_SAME_NAME.get(query, [])
            places = [
                _place(f"{query}-{i}", query, 40.0, -74.5, f"{i} Main St, {t}, NJ 07000, USA", "x", [])
                for i, t in enumerate(towns)
            ]
            # A similar-but-different name must not count as a match
            places.append(_place("other", query + " Express", 40.1, -74.6,
                                 "9 Other Rd, Edison, NJ 08817, USA", "x", []))
            return FakeResponse(200, {"places": places})

        # Westfield-area search: two pages for "restaurant" to exercise paging
        results = LOCAL_PLACES.get(query, [])
        if query == "restaurant":
            if json.get("pageToken") == "page2":
                return FakeResponse(200, {"places": results[2:]})
            return FakeResponse(200, {"places": results[:2], "nextPageToken": "page2"})
        return FakeResponse(200, {"places": results})


class FakeSession:
    def __init__(self, fake):
        self.post = fake.post

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
