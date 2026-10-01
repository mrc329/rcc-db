import pytest

import places_utils as pu


@pytest.fixture(scope="module")
def chains():
    return pu.load_known_chains()


@pytest.mark.parametrize("name,expected", [
    ("Starbucks", "starbucks"),
    ("Dunkin'", "dunkin"),
    ("CVS Pharmacy", "cvs"),
    ("T.J. Maxx", "tj maxx"),
    ("Häagen-Dazs", "haagen dazs"),
    ("Trader Joe's - Westfield", "trader joes"),
    ("Jane Doe - State Farm Insurance Agent", "state farm"),
    ("ATM (Bank of America)", "bank of america"),
    ("Kumon Math and Reading Center of Westfield", "kumon"),
    ("GAP", "gap"),
    # Exact-only entries must not catch unrelated businesses
    ("Gap Insurance Agency", None),
    ("Michael's Pizza", None),
    ("Jared Smith CPA", None),
    ("Rei Sushi", None),
    ("Chase Wealth Advisors", None),
    ("Rialto Theatre", None),
])
def test_match_known_chain(chains, name, expected):
    assert pu.match_known_chain(name, chains) == expected


@pytest.mark.parametrize("primary,types,expected", [
    ("italian_restaurant", ["restaurant", "food"], ("72", "Google place type")),
    ("bank", ["bank", "atm", "finance"], ("52", "Google place type")),
    ("accounting", ["accounting", "finance"], ("54", "Google place type")),
    ("clothing_store", ["clothing_store", "store"], ("44-45", "Google place type")),
    ("real_estate_agency", [], ("53", "Google place type")),
    (None, ["point_of_interest", "store"], ("44-45", "Google place type (generic)")),
    (None, ["point_of_interest", "establishment"], ("54", "search category")),
])
def test_sector_for_types(primary, types, expected):
    assert pu.sector_for_types(primary, types, "54") == expected


def test_every_mapped_sector_exists_in_naics_sectors():
    from census_utils import NAICS_SECTORS
    used = set(pu._TYPE_SECTOR.values()) | set(pu._GENERIC_TYPE_SECTOR.values())
    used |= {c["sector"] for c in pu.SEARCH_CATEGORIES.values()}
    assert used <= set(NAICS_SECTORS)


def test_fetch_westfield_businesses(fake_places):
    df = pu.fetch_westfield_businesses(tuple(pu.SEARCH_CATEGORIES))
    by_id = df.set_index("place_id")

    # Paging: the second "restaurant" page was fetched
    assert any(c.get("pageToken") == "page2" for c in fake_places.calls)
    # Inside radius kept; outside radius and permanently closed dropped
    assert "p_rist" in by_id.index and "p_gr" in by_id.index
    assert "p_far" not in by_id.index
    assert "p_closed" not in by_id.index
    # Required output columns
    for col in ["name", "address", "lat", "lon", "sector", "place_id"]:
        assert col in df.columns
    assert by_id.loc["p_cpa", "sector"] == "Professional, Scientific & Technical Services"
    assert by_id.loc["p_odd", "sector_basis"] == "search category"
    assert df["place_id"].is_unique


def test_fetch_is_cached(fake_places):
    pu.fetch_westfield_businesses(("Finance & Insurance",))
    n = len(fake_places.calls)
    pu.fetch_westfield_businesses(("Finance & Insurance",))
    assert len(fake_places.calls) == n


def test_classify_businesses(fake_places):
    df = pu.classify_businesses(pu.fetch_westfield_businesses(tuple(pu.SEARCH_CATEGORIES)))
    status = dict(zip(df["name"], df["franchise_status"]))

    assert status["Starbucks"] == pu.LABEL_KNOWN
    assert status["T.J. Maxx"] == pu.LABEL_KNOWN
    assert status["Jane Doe - State Farm Insurance Agent"] == pu.LABEL_KNOWN
    assert status["Tri-County Savings"] == pu.LABEL_MULTI  # 4 locations, 4 towns
    assert status["The Leaf Boutique"] == pu.LABEL_INDEPENDENT  # 2 hits, 1 town
    assert status["Ristorante Mezzaluna"] == pu.LABEL_INDEPENDENT
    assert status["Smith & Rao CPAs"] == pu.LABEL_INDEPENDENT

    note = dict(zip(df["name"], df["classification_note"]))
    assert "generic name" in note["Kitchen Corner Cafe"]
    assert set(df["franchise_status"]) <= set(pu.FRANCHISE_LABELS)

    # Known chains skip the NJ-wide search
    nj_queries = [c["textQuery"] for c in fake_places.calls
                  if c["locationRestriction"]["rectangle"]["high"]["latitude"] > 41]
    assert "Starbucks" not in nj_queries


def test_chain_check_failure_is_labeled_and_not_cached(fake_places):
    fake_places.fail_names = {"Smith & Rao CPAs"}
    df = pu.fetch_westfield_businesses(("Professional Services",))
    out = pu.classify_businesses(df)
    row = out[out["name"] == "Smith & Rao CPAs"].iloc[0]
    assert row["franchise_status"] == pu.LABEL_UNCHECKED
    assert "Quota exceeded" in row["classification_note"]

    fake_places.fail_names = set()
    out = pu.classify_businesses(df)
    assert out.loc[out["name"] == "Smith & Rao CPAs", "franchise_status"].iloc[0] == pu.LABEL_INDEPENDENT


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    with pytest.raises(pu.PlacesAPIError):
        pu.text_search("bank", pu.NJ_RECTANGLE, "places.id")


def test_own_location_always_counts(fake_places):
    # Garwood Grill isn't in the fake NJ-wide results; it should still count itself.
    df = pu.classify_businesses(pu.fetch_westfield_businesses(("Restaurants & Food",)))
    row = df[df["name"] == "Garwood Grill"].iloc[0]
    assert row["nj_same_name_locations"] == 1
    assert row["franchise_status"] == pu.LABEL_INDEPENDENT
