"""
End-to-end run of app.py through Streamlit's AppTest harness, with
census.gov, TIGER (pygris) and Google Places replaced by offline fakes.
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

import places_utils as pu

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _run(secrets=None):
    at = AppTest.from_file(APP, default_timeout=60)
    for k, v in (secrets or {}).items():
        at.secrets[k] = v
    return at.run()


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_app_without_places_key(fake_census, monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    at = _run()
    assert not at.exception
    assert not at.error
    assert any("GOOGLE_PLACES_API_KEY" in i.value for i in at.info)
    # Census-driven metrics rendered (5 demographic + 4 indicator metrics)
    labels = [m.label for m in at.metric]
    assert "Median HH Income (avg across BGs)" in labels
    assert "Bachelor's+ (25+)" in labels


def test_app_full_flow(fake_census, fake_places):
    at = _run({"GOOGLE_PLACES_API_KEY": "test-key", "CENSUS_API_KEY": "census-key"})
    assert not at.exception
    assert not at.warning or all("Could not load GIS" not in w.value for w in at.warning)

    _button(at, "Search Google Places").click()
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]

    table = at.dataframe[-1].value
    assert {"name", "address", "lat", "lon", "sector", "franchise_status", "place_id"} <= set(table.columns)
    assert "Starbucks" in set(table["name"])
    # Boundary flag computed from the (fake) town polygon
    in_town = dict(zip(table["name"], table["in_westfield_boundary"]))
    assert not in_town["Garwood Grill"] and in_town["Starbucks"]

    # Filter: independents only, inside the town line
    status_filter = next(m for m in at.multiselect if m.label == "Filter by chain / independent")
    status_filter.set_value([pu.LABEL_INDEPENDENT])
    next(c for c in at.checkbox if c.label == "Inside Westfield town line only").check()
    at.run()
    table = at.dataframe[-1].value
    assert set(table["franchise_status"]) == {pu.LABEL_INDEPENDENT}
    assert "Garwood Grill" not in set(table["name"])
    assert "Starbucks" not in set(table["name"])

    # Sector filter narrows further
    sector_filter = next(m for m in at.multiselect if m.label == "Filter by sector")
    sector_filter.set_value(["Accommodation & Food Services"])
    at.run()
    table = at.dataframe[-1].value
    assert set(table["sector"]) == {"Accommodation & Food Services"}

    # Re-running the page doesn't re-hit the API (cached)
    n = len(fake_places.calls)
    at.run()
    assert len(fake_places.calls) == n


def test_known_chain_filter(fake_census, fake_places):
    at = _run({"GOOGLE_PLACES_API_KEY": "test-key"})
    _button(at, "Search Google Places").click()
    at.run()
    status_filter = next(m for m in at.multiselect if m.label == "Filter by chain / independent")
    status_filter.set_value([pu.LABEL_KNOWN])
    at.run()
    table = at.dataframe[-1].value
    assert set(table["franchise_status"]) == {pu.LABEL_KNOWN}
    assert {"Starbucks", "T.J. Maxx", "Chase Bank"} <= set(table["name"])
