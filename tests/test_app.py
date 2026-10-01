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
    assert "Median HH Income (est.)" in labels
    assert "Households earning $200k+" in labels
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
    assert {"name", "address", "sector", "franchise_status", "status", "notes",
            "meters_to_rialto", "place_id"} <= set(table.columns)
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


def test_contact_status_hides_handled_businesses(fake_census, fake_places):
    import pandas as pd
    import tracker_utils as tu

    at = AppTest.from_file(APP, default_timeout=60)
    at.secrets["GOOGLE_PLACES_API_KEY"] = "test-key"
    at.session_state["_tracker_df"] = pd.DataFrame([
        {"place_id": "p_rist", "name": "Ristorante Mezzaluna", "address": "",
         "status": tu.STATUS_DONOR, "notes": "gave $5k in 2025", "updated_at": "2026-09-01"},
        {"place_id": "p_cpa", "name": "Smith & Rao CPAs", "address": "",
         "status": tu.STATUS_ASKED, "notes": "", "updated_at": "2026-09-02"},
    ])
    at.run()
    _button(at, "Search Google Places").click()
    at.run()
    assert not at.exception, at.exception

    table = at.dataframe[-1].value if at.dataframe else None
    editor = at.get("arrow_data_frame")[-1].value if table is None else table
    names = set(editor["name"])
    assert "Ristorante Mezzaluna" not in names  # donor hidden by default
    assert "Smith & Rao CPAs" in names           # asked = still open
    assert list(editor["meters_to_rialto"]) == sorted(editor["meters_to_rialto"])

    contact = next(m for m in at.multiselect if m.label == "Filter by contact status")
    contact.set_value([tu.STATUS_DONOR])
    at.run()
    editor = at.dataframe[-1].value
    assert list(editor["name"]) == ["Ristorante Mezzaluna"]
    assert editor["notes"].iloc[0] == "gave $5k in 2025"


def test_walkable_filter(fake_census, fake_places):
    at = _run({"GOOGLE_PLACES_API_KEY": "test-key"})
    _button(at, "Search Google Places").click()
    at.run()
    next(c for c in at.checkbox if c.label.startswith("Within walking distance")).check()
    at.run()
    editor = at.dataframe[-1].value
    assert len(editor) > 0
    assert (editor["meters_to_rialto"] <= pu.WALKING_DISTANCE_M).all()
    assert not any("Rialto location is approximate" in w.value for w in at.warning)
