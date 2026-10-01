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


# Label metadata for the label-matched tables, in the API's format. Each
# includes look-alike lines the patterns must NOT pick up.
_T = "Estimate!!Total:"
ACS_TABLE_LABELS = {
    "B08301": {
        "B08301_001E": _T,
        "B08301_010E": _T + "!!Public transportation (excluding taxicab):",
        "B08301_011E": _T + "!!Public transportation (excluding taxicab):!!Bus",
    },
    "B07003": {
        "B07003_001E": _T,
        "B07003_004E": _T + "!!Same house 1 year ago",
        "B07003_005E": _T + "!!Male:!!Same house 1 year ago",
    },
    "C24010": {
        "C24010_001E": _T,
        "C24010_002E": _T + "!!Male:",
        "C24010_003E": _T + "!!Male:!!Management, business, science, and arts occupations:",
        "C24010_016E": _T + "!!Male:!!Management, business, science, and arts occupations:"
                            "!!Education, legal, community service, arts, and media occupations:"
                            "!!Arts, design, entertainment, sports, and media occupations",
        "C24010_038E": _T + "!!Female:",
        "C24010_039E": _T + "!!Female:!!Management, business, science, and arts occupations:",
        "C24010_052E": _T + "!!Female:!!Management, business, science, and arts occupations:"
                            "!!Education, legal, community service, arts, and media occupations:"
                            "!!Arts, design, entertainment, sports, and media occupations",
    },
    "B25075": {
        "B25075_001E": _T,
        "B25075_024E": _T + "!!$750,000 to $999,999",
        "B25075_025E": _T + "!!$1,000,000 to $1,499,999",
        "B25075_026E": _T + "!!$1,500,000 to $1,999,999",
        "B25075_027E": _T + "!!$2,000,000 or more",
    },
    "B11005": {
        "B11005_001E": _T,
        "B11005_002E": _T + "!!Households with one or more people under 18 years:",
        "B11005_003E": _T + "!!Households with one or more people under 18 years:!!Family households:",
    },
    "B09001": {
        "B09001_001E": _T,
        "B09001_002E": _T + "!!In households:",
        "B09001_003E": _T + "!!In households:!!Under 3 years",
        "B09001_004E": _T + "!!In households:!!3 and 4 years",
        "B09001_005E": _T + "!!In households:!!5 years",
        "B09001_006E": _T + "!!In households:!!6 to 8 years",
        "B09001_007E": _T + "!!In households:!!9 to 11 years",
        "B09001_008E": _T + "!!In households:!!12 to 14 years",
        "B09001_009E": _T + "!!In households:!!15 to 17 years",
    },
    "B01001": {
        "B01001_001E": _T,
        **{f"B01001_{n:03d}E": _T + f"!!{sex}:!!{band}"
           for sex, start in (("Male", 19), ("Female", 43))
           for n, band in zip(range(start, start + 7), [
               "62 to 64 years", "65 and 66 years", "67 to 69 years", "70 to 74 years",
               "75 to 79 years", "80 to 84 years", "85 years and over"])},
    },
}


def fake_census_get(url, timeout=None, **kwargs):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    path = parsed.path

    if "/acs/acs5" in path:
        if "/2024/" in path:  # simulate the newest vintage not being live yet
            return FakeResponse(404, text="unknown dataset")
        if "/groups/" in path:
            table = path.rsplit("/", 1)[-1].removesuffix(".json")
            labels = ACS_TABLE_LABELS.get(table)
            if labels is None:
                return FakeResponse(404, text="unknown group")
            variables = {code: {"label": label} for code, label in labels.items()}
            variables[f"{table}_001EA"] = {"label": "Annotation of Estimate!!Total:"}
            variables[f"{table}_001M"] = {"label": "Margin of Error!!Total:"}
            return FakeResponse(200, {"variables": variables})
        get_vars = qs["get"][0].split(",")
        # Mimic the real API: tables not published at block-group level
        # make it reject the whole request.
        not_at_bg = [v for v in get_vars if v.startswith(("B07003", "B24010"))]
        if not_at_bg and qs["for"][0].startswith("block group"):
            return FakeResponse(400, text=f"error: unknown variable '{not_at_bg[0]}'")
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
                elif v.endswith("_001E") and v[:6] in ACS_TABLE_LABELS:
                    vals.append(str(1000 + i * 100))  # table totals
                elif v in ACS_TABLE_LABELS.get(v[:6], {}):
                    vals.append(str(10 + i))  # each matched line
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


# --- IRS SOI ZIP files --------------------------------------------------------

# Full file layout: one row per ZIP per AGI bracket (agi_stub 1-6), lowercase
# 'zipcode' as in recent years. Amounts in thousands.
IRS_ALLAGI = """STATEFIPS,STATE,zipcode,agi_stub,N1,mars1,A00100,N19700,A19700
34,NJ,00000,1,100000,1,5000000,10000,90000
34,NJ,07090,5,4000,10,600000,1500,9000
34,NJ,07090,6,6000,20,4000000,4500,81000
34,NJ,07016,5,5000,10,700000,1200,6000
34,NJ,07016,6,3000,20,1500000,1800,24000
34,NJ,99999,6,10,1,100,1,10
36,NY,10001,6,9999,1,9999999,9999,99999
"""


class FakeStreamResponse(FakeResponse):
    def __init__(self, status_code, text=""):
        super().__init__(status_code, text=text)
        self.encoding = "utf-8"

    def iter_lines(self, decode_unicode=False):
        return iter(self.text.splitlines())

    def close(self):
        pass


class FakeIRS:
    """23zpallnoagi -> 404, 23zpallagi -> 404, 22zpallnoagi -> 404, 22zpallagi -> data."""

    def __init__(self):
        self.urls = []

    def get(self, url, stream=False, timeout=None):
        self.urls.append(url)
        if url.endswith("22zpallagi.csv"):
            return FakeStreamResponse(200, IRS_ALLAGI)
        return FakeStreamResponse(404, "")
