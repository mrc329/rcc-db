from datetime import date

import pandas as pd

import campaign_utils as cu


def test_default_chart_reaches_goal_with_classic_shape():
    chart = cu.gift_range_chart()
    assert chart["level_total"].sum() == cu.CAMPAIGN_GOAL
    assert chart["gift_amount"].iloc[0] == 1_500_000          # 15% lead gift
    assert chart["gift_amount"].is_monotonic_decreasing
    top6 = chart["level_total"].iloc[:3].sum()                 # 1 + 2 + 3 gifts
    assert top6 == 5_000_000                                   # top gifts carry half the goal
    assert chart["pct_of_goal"].iloc[-1] == 100
    big = chart[chart["gift_amount"] >= 250_000]
    assert (big["prospects_per_gift"] == 4).all()
    assert (chart.loc[chart["gift_amount"] < 250_000, "prospects_per_gift"] == 3).all()
    assert (chart["prospects_needed"] == chart["gifts"] * chart["prospects_per_gift"]).all()


def test_lead_pct_changes_lead_gift():
    assert cu.gift_range_chart(lead_pct=20)["gift_amount"].iloc[0] == 2_000_000
    assert cu.gift_range_chart(lead_pct=10)["gift_amount"].iloc[0] == 1_000_000
    for pct in (10, 12, 15, 18, 20, 25):
        assert cu.gift_range_chart(lead_pct=pct)["level_total"].sum() >= cu.CAMPAIGN_GOAL


def test_recompute_respects_edits():
    edited = pd.DataFrame({"gift_amount": [100_000, 2_000_000], "gifts": [10, 1],
                           "prospects_per_gift": [5, 6]})
    chart = cu.recompute_chart(edited)
    assert list(chart["gift_amount"]) == [2_000_000, 100_000]  # re-sorted
    assert list(chart["prospects_needed"]) == [6, 50]
    assert chart["cumulative_total"].iloc[-1] == 3_000_000


def test_milestone_pace():
    pace = cu.milestone_pace(1_000_000, 800_000, today=date(2026, 10, 1)).set_index("milestone")
    one = pace.loc["One"]
    assert one["gap_committed"] == 2_000_000
    assert one["gap_cash"] == 2_200_000                     # cash target binds
    assert one["months_left"] == 3.0
    assert one["monthly_pace_needed"] == round(2_200_000 / 3.0)
    assert pace.loc["Two", "target_committed"] == 4_500_000  # $3.5M cash + $1M pledges
    assert pd.isna(pace.loc["Three", "gap_cash"])            # no cash-only target


def test_milestone_met_and_past_due():
    pace = cu.milestone_pace(3_200_000, 3_100_000, today=date(2027, 1, 15)).set_index("milestone")
    assert pace.loc["One", "status"] == "met"
    pace = cu.milestone_pace(100_000, 100_000, today=date(2027, 1, 15)).set_index("milestone")
    assert pace.loc["One", "status"] == "past due"
    assert pace.loc["Two", "status"] == "open"
