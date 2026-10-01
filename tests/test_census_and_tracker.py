import pandas as pd
import pytest

import census_utils as cu
import places_utils as pu
import tracker_utils as tu


def _brackets(**counts):
    return pd.Series({label: counts.get(label, 0) for label in cu.INCOME_BUCKETS.values()})


def test_median_from_brackets_interpolates():
    # 100 households: 40 in $75k-100k, 60 in $100k-125k -> median at the
    # 10th of 60 households into $100k-125k = 100k + 10/60 * 25k
    value, label = cu.estimate_median_from_brackets(
        _brackets(**{"$75k-100k": 40, "$100k-125k": 60}))
    assert value == pytest.approx(100_000 + 10 / 60 * 25_000)
    assert label == "$104,167"


def test_median_in_top_bracket_is_labeled_open_ended():
    value, label = cu.estimate_median_from_brackets(_brackets(**{"$50k-60k": 10, "$200k+": 90}))
    assert (value, label) == (200_000, "$200k+")


def test_median_with_no_households():
    assert cu.estimate_median_from_brackets(_brackets()) == (None, "n/a")


def test_acs_sentinels_become_missing_and_high_income_counts(fake_census, monkeypatch):
    import fakes
    real = fakes.fake_census_get

    def with_sentinel(url, **kw):
        resp = real(url, **kw)
        if "acs5" in url and resp.ok:
            header = resp._payload[0]
            resp._payload[1][header.index("B19013_001E")] = "-666666666"
        return resp

    monkeypatch.setattr(cu.requests, "get", with_sentinel)
    cu.fetch_block_group_acs.clear()
    df = cu.fetch_block_group_acs()
    assert df.attrs["acs_year"] == "2023"  # 2024 faked as unpublished
    assert pd.isna(df["median_household_income"].iloc[0])
    assert (df["median_household_income"].dropna() > 0).all()
    assert (df["hh_200k_plus"] == df["$200k+"]).all()
    assert (df["hh_150k_plus"] == df["$150k-200k"] + df["$200k+"]).all()
    assert df["pct_hh_200k_plus"].between(0, 100).all()


def test_rialto_distance():
    df = pd.DataFrame({"lat": [40.6505, 40.6605], "lon": [-74.3450, -74.3450]})
    out = pu.add_rialto_distance(df, 40.6505, -74.3450)
    assert out["meters_to_rialto"].iloc[0] == 0
    assert 1100 < out["meters_to_rialto"].iloc[1] < 1120  # 0.01 deg lat ~ 1.11 km
    assert list(out["walk_to_rialto"]) == [True, False]


def test_locate_rialto(fake_places):
    pu.locate_rialto.clear()
    lat, lon, source = pu.locate_rialto()
    assert (lat, lon) == (40.6503, -74.3447)
    assert "250 E Broad" in source


def test_locate_rialto_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    pu.locate_rialto.clear()
    lat, lon, source = pu.locate_rialto()
    assert (lat, lon) == pu.RIALTO_FALLBACK
    assert source.startswith("approximate")
    pu.locate_rialto.clear()


def test_tracker_merge_attach_and_changes():
    existing = pd.DataFrame([
        {"place_id": "a", "name": "A", "address": "", "status": tu.STATUS_ASKED,
         "notes": "call back", "updated_at": "2026-09-01 10:00 UTC"},
        {"place_id": "b", "name": "B", "address": "", "status": "bogus status",
         "notes": "", "updated_at": "2026-09-01 10:00 UTC"},
    ])
    merged = tu.merge_rows(existing, pd.DataFrame([
        {"place_id": "a", "name": "A", "address": "", "status": tu.STATUS_DONOR, "notes": "$2,500"},
        {"place_id": "c", "name": "C", "address": "", "status": tu.STATUS_DECLINED, "notes": ""},
    ]))
    by_id = merged.set_index("place_id")
    assert by_id.loc["a", "status"] == tu.STATUS_DONOR
    assert by_id.loc["a", "notes"] == "$2,500"
    assert by_id.loc["a", "updated_at"] != "2026-09-01 10:00 UTC"
    assert by_id.loc["b", "status"] == tu.STATUS_NOT_CONTACTED  # unknown status normalized
    assert set(merged["place_id"]) == {"a", "b", "c"}

    biz = pd.DataFrame({"place_id": ["a", "z"], "name": ["A", "Z"], "address": ["", ""]})
    attached = tu.attach_tracker(biz, merged)
    assert list(attached["status"]) == [tu.STATUS_DONOR, tu.STATUS_NOT_CONTACTED]

    edited = attached.copy()
    edited.loc[1, "notes"] = "met owner at street fair"
    changes = tu.changed_rows(attached, edited)
    assert list(changes["place_id"]) == ["z"]


def test_session_store_roundtrip():
    from streamlit.testing.v1 import AppTest

    def script():
        import pandas as pd
        import streamlit as st
        import tracker_utils as tu
        store = tu.SessionStore()
        store.upsert(pd.DataFrame([{"place_id": "x", "name": "X", "address": "",
                                    "status": tu.STATUS_DO_NOT_CONTACT, "notes": "owner asked"}]))
        st.write(store.load().iloc[0]["status"])

    at = AppTest.from_function(script).run()
    assert not at.exception
    assert at.markdown[0].value == tu.STATUS_DO_NOT_CONTACT


def test_get_store_without_sheet_is_session(monkeypatch):
    monkeypatch.delenv("TRACKER_SHEET_URL", raising=False)
    store, err = tu.get_store()
    assert isinstance(store, tu.SessionStore) and err is None


def test_tables_missing_at_block_group_dont_break_the_load(fake_census):
    cu.fetch_block_group_acs.clear()
    df = cu.fetch_block_group_acs()
    missing = df.attrs["acs_missing"]
    assert set(missing) == {"length of residence"}
    assert "unknown variable" in missing["length of residence"]
    assert df["mobility_same_house_1yr_ago"].isna().all()
    assert df["commute_public_transit"].notna().all()


def test_label_matched_metrics_pick_the_right_lines(fake_census):
    # Fakes give every matched line 10+i and every table total 1000+100i,
    # so each metric's value reveals how many lines were summed.
    cu.fetch_block_group_acs.clear()
    df = cu.fetch_block_group_acs()
    line = df.index.map(lambda i: 10 + i).to_series(index=df.index)
    assert (df["occupation_mgmt_business_science_arts"] == 2 * line).all()  # male + female
    assert (df["arts_workers"] == 2 * line).all()
    assert (df["homes_1m_plus"] == 3 * line).all()           # not the $750k-999k decoy
    assert (df["households_with_kids"] == line).all()        # not the family-households sub-line
    assert (df["kids_under_12"] == 5 * line).all()           # not 12-14 or 15-17
    assert (df["adults_65_plus"] == 12 * line).all()         # not 62-64
    assert (df["commute_public_transit"] == line).all()      # not the Bus sub-line
    assert (df["owner_homes_total"] == 1000 + 100 * df.index).all()
    assert df["pct_homes_1m_plus"].between(0, 100).all()


def test_unexpected_table_layout_reports_missing_not_wrong(fake_census, monkeypatch):
    import fakes
    labels = dict(fakes.ACS_TABLE_LABELS["B25075"])
    del labels["B25075_027E"]  # e.g. a vintage without the $2M+ line
    monkeypatch.setitem(fakes.ACS_TABLE_LABELS, "B25075", labels)
    cu.fetch_block_group_acs.clear()
    cu.fetch_table_labels.clear()
    df = cu.fetch_block_group_acs()
    assert "home values" in df.attrs["acs_missing"]
    assert "expected 3" in df.attrs["acs_missing"]["home values"]
    assert df["homes_1m_plus"].isna().all()
    assert df["arts_workers"].notna().all()  # other groups unaffected
    cu.fetch_block_group_acs.clear()
    cu.fetch_table_labels.clear()


def test_resolve_label_metric():
    labels = {"X_001E": "Estimate!!Total:", "X_002E": "Estimate!!Total:!!A", "X_003E": "Estimate!!Total:!!AB"}
    assert cu.resolve_label_metric(labels, r"!!A$", 1) == ["X_002E"]
    with pytest.raises(RuntimeError, match="expected 2"):
        cu.resolve_label_metric(labels, r"!!A$", 2)


def test_api_key_never_appears_in_errors(fake_census, monkeypatch):
    import fakes
    monkeypatch.setenv("CENSUS_API_KEY", "SECRETKEY123")
    monkeypatch.setattr(cu.requests, "get",
                        lambda url, **kw: fakes.FakeResponse(400, text="error: bad request"))
    cu.fetch_block_group_acs.clear()
    with pytest.raises(RuntimeError) as exc:
        cu.fetch_block_group_acs()
    assert "SECRETKEY123" not in str(exc.value)
    assert "error: bad request" in str(exc.value)

    def boom(url, **kw):
        raise cu.requests.ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(cu.requests, "get", boom)
    cu.fetch_block_group_acs.clear()
    with pytest.raises(RuntimeError) as exc:
        cu.fetch_block_group_acs()
    assert "SECRETKEY123" not in str(exc.value)
    cu.fetch_naics_establishment_counts.clear()
    counts = cu.fetch_naics_establishment_counts()
    assert not counts["error"].str.contains("SECRETKEY123").any()
    cu.fetch_block_group_acs.clear()
    cu.fetch_naics_establishment_counts.clear()
