import pytest

import irs_utils as iu


def test_parse_full_file_sums_brackets_and_keeps_only_nj_zips():
    import fakes
    df = iu.parse_csv_text(fakes.IRS_ALLAGI).set_index("zip")
    assert set(df.index) == {"07090", "07016"}  # state total, 99999 and NY dropped
    w = df.loc["07090"]
    assert w["returns"] == 10000
    assert w["returns_claiming_charity"] == 6000
    assert w["charity_total"] == 90_000_000
    assert w["pct_returns_claiming_charity"] == pytest.approx(60.0)
    assert w["avg_gift_per_claiming_return"] == pytest.approx(15_000)
    assert w["charity_pct_of_agi"] == pytest.approx(90_000 / 4_600_000 * 100)
    assert w["town"] == "Westfield"


def test_parse_uppercase_no_agi_layout():
    text = "STATEFIPS,STATE,ZIPCODE,N1,A00100,N19700,A19700\n34,NJ,7090,10,1000,2,30\n"
    df = iu.parse_csv_text(text)
    assert df.iloc[0]["zip"] == "07090"  # leading zero restored
    assert df.iloc[0]["avg_gift_per_claiming_return"] == pytest.approx(15_000)


def test_unexpected_layout_is_a_clear_error():
    with pytest.raises(iu.IRSDataError, match="Unexpected IRS file layout"):
        iu.parse_csv_text("STATE,ZIPCODE,N1\nNJ,07090,5\n")


def test_fetch_falls_back_across_files_and_years(monkeypatch):
    import fakes
    fake = fakes.FakeIRS()
    monkeypatch.setattr(iu.requests, "get", fake.get)
    iu.fetch_nj_zip_giving.clear()
    df = iu.fetch_nj_zip_giving()
    assert df.attrs["tax_year"] == "2022"
    assert df.attrs["source"].endswith("22zpallagi.csv")
    assert fake.urls[:3] == [
        "https://www.irs.gov/pub/irs-soi/23zpallnoagi.csv",
        "https://www.irs.gov/pub/irs-soi/23zpallagi.csv",
        "https://www.irs.gov/pub/irs-soi/22zpallnoagi.csv",
    ]
    iu.fetch_nj_zip_giving.clear()


def test_fetch_reports_failure(monkeypatch):
    import fakes
    monkeypatch.setattr(iu.requests, "get", lambda url, **kw: fakes.FakeStreamResponse(404))
    iu.fetch_nj_zip_giving.clear()
    with pytest.raises(iu.IRSDataError, match="not published"):
        iu.fetch_nj_zip_giving()
