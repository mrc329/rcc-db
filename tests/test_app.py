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
    # Mobility isn't published at block-group level -> shown as n/a, explained
    by_label = {m.label: m.value for m in at.metric}
    assert by_label["Same Residence vs. 1 Yr Ago"] == "n/a"
    assert by_label["Mgmt/Business/Science/Arts Occupations"] != "n/a"
    assert any("length of residence" in c.value for c in at.caption)
    # Mission-fit metrics (fake lines are 10+i over 4 block groups: sum 46)
    assert by_label["Children under 12"] == f"{5 * 46:,}"
    assert by_label["Adults 65+"].startswith(f"{12 * 46:,} · ")
    assert by_label["Arts, design & media workers"].startswith(f"{2 * 46:,} · ")


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


def test_ranked_block_groups_and_home_values(fake_census, monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    at = _run()
    assert not at.exception
    ranked = next(d.value for d in at.dataframe if "Rank" in d.value.columns)
    assert list(ranked["Rank"]) == list(range(1, len(ranked) + 1))
    assert ranked["HH $200k+"].is_monotonic_decreasing
    assert {"Homes $1M+", "% in Westfield"} <= set(ranked.columns)
    # Fake block groups are drawn fully inside the fake town box
    assert (ranked["% in Westfield"] == 100).all()

    next(s for s in at.selectbox if s.label == "Rank by").set_value("Homes worth $1M+")
    at.run()
    ranked = next(d.value for d in at.dataframe if "Rank" in d.value.columns)
    assert ranked["Homes $1M+"].is_monotonic_decreasing
    assert "Owner-occupied homes worth $1M+" in at.radio[0].options


def test_irs_tab(fake_census, monkeypatch):
    import fakes
    import irs_utils
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    irs = fakes.FakeIRS()
    # census_utils and irs_utils share the requests module, so route by host.
    monkeypatch.setattr(irs_utils.requests, "get", lambda url, **kw: (
        irs.get(url, **kw) if "irs.gov" in url else fakes.fake_census_get(url, **kw)))
    irs_utils.fetch_nj_zip_giving.clear()

    at = _run()
    _button(at, "Load IRS ZIP data").click()
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    by_label = {m.label: m.value for m in at.metric}
    assert by_label["Westfield (07090): returns claiming charity"] == "60%"
    assert by_label["Avg. deduction per claiming return"] == "$15,000"
    assert any("tax year 2022" in c.value for c in at.caption)

    # Stays loaded on rerun without clicking again
    at.run()
    assert "Avg. deduction per claiming return" in [m.label for m in at.metric]
    irs_utils.fetch_nj_zip_giving.clear()


def test_campaign_plan_tab(fake_census, monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    at = _run()
    assert not at.exception
    by_label = {m.label: m.value for m in at.metric}
    assert by_label["Chart total"] == "$10.00M"
    assert by_label["Gifts needed"] == "77"
    assert by_label["Qualified prospects needed"] == "243"
    assert "Westfield-area households earning $200k+" in by_label

    next(n for n in at.number_input if n.label.startswith("Raised to date")).set_value(1_000_000)
    next(n for n in at.number_input if n.label.startswith("Of which cash")).set_value(800_000)
    at.run()
    pace = next(d.value for d in at.dataframe if "monthly_pace_needed" in d.value.columns)
    assert pace.loc[pace["milestone"] == "One", "gap_committed"].iloc[0] == 2_000_000

    next(s for s in at.slider if s.label.startswith("Lead gift")).set_value(20)
    at.run()
    assert {m.label: m.value for m in at.metric}["Gifts needed"] == "35"
