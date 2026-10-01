"""
Capital campaign planning: the Rialto's milestones, a gift range chart,
and pace toward each milestone.

The gift range chart is the standard fundraising rule of thumb, not a
prediction: a lead gift of ~10-20% of the goal, each lower level raising
about as much as the lead gift through more, smaller gifts, and 3-4
qualified prospects per gift needed. The app lets users edit it.
"""

import math
from datetime import date

import pandas as pd

CAMPAIGN_GOAL = 10_000_000

# From the campaign's "Campaign Milestones" slide. Milestone Two reads
# "$3.5M cash / $1M pledges"; it's taken here as $3.5M cash PLUS $1M
# pledged ($4.5M committed). Milestones Three and Four are cash and
# pledges combined.
MILESTONES = [
    {"milestone": "One", "due": date(2026, 12, 31), "cash": 3_000_000, "committed": 3_000_000,
     "note": "Cash. Creates urgency and social proof; satisfies the M&T requirement."},
    {"milestone": "Two", "due": date(2027, 7, 31), "cash": 3_500_000, "committed": 4_500_000,
     "note": "$3.5M cash + $1M pledges. Unlocks the CAFE match; public celebration."},
    {"milestone": "Three", "due": date(2028, 7, 31), "cash": None, "committed": 7_500_000,
     "note": "Cash + pledges. Public celebration of CAFE / groundbreaking widens outreach."},
    {"milestone": "Four", "due": date(2029, 7, 31), "cash": None, "committed": 10_000_000,
     "note": "Cash + pledges. Upgrades, second gifts, naming rights; \"we're almost there\"."},
]

# Gift levels below the lead gift. Each level is allotted roughly the
# lead gift's total, which is the classic chart shape.
STANDARD_LEVELS = [1_000_000, 500_000, 250_000, 100_000, 50_000, 25_000, 10_000, 5_000]


def _round_lead(amount):
    """Round the lead gift to a fundraiser-friendly figure."""
    step = 250_000 if amount >= 1_000_000 else 50_000 if amount >= 100_000 else 5_000
    return max(step, round(amount / step) * step)


def gift_range_chart(goal=CAMPAIGN_GOAL, lead_pct=15, prospects_top=4, prospects_rest=3,
                     top_threshold=250_000):
    """
    Build a gift range chart reaching `goal`.

    Returns a DataFrame with gift_amount, gifts, level_total,
    cumulative_total, pct_of_goal, prospects_per_gift, prospects_needed.
    Levels at or above `top_threshold` use `prospects_top` prospects per
    gift (bigger asks close less often); the rest use `prospects_rest`.
    """
    lead = _round_lead(goal * lead_pct / 100)
    share = lead  # each level aims to raise about the lead gift's amount
    rows, cumulative = [], 0
    amounts = [lead] + [a for a in STANDARD_LEVELS if a < lead]
    for amount in amounts:
        remaining = goal - cumulative
        if remaining <= 0:
            break
        is_last = amount == amounts[-1]
        target = remaining if is_last else min(share, remaining)
        gifts = max(1, math.ceil(target / amount)) if amount == lead else max(1, round(target / amount))
        if cumulative + gifts * amount > goal:
            gifts = max(1, math.ceil(remaining / amount))
        rows.append({"gift_amount": amount, "gifts": gifts})
        cumulative += gifts * amount
    return recompute_chart(pd.DataFrame(rows), goal, prospects_top, prospects_rest, top_threshold)


def recompute_chart(chart, goal=CAMPAIGN_GOAL, prospects_top=4, prospects_rest=3,
                    top_threshold=250_000):
    """Fill derived columns for a (possibly user-edited) chart."""
    out = chart[["gift_amount", "gifts"]].copy()
    out = out.dropna().astype({"gift_amount": "int64", "gifts": "int64"})
    out = out.sort_values("gift_amount", ascending=False).reset_index(drop=True)
    out["level_total"] = out["gift_amount"] * out["gifts"]
    out["cumulative_total"] = out["level_total"].cumsum()
    out["pct_of_goal"] = (out["cumulative_total"] / goal * 100).round(0)
    if "prospects_per_gift" in chart.columns:
        ratios = chart.set_index("gift_amount")["prospects_per_gift"]
        out["prospects_per_gift"] = out["gift_amount"].map(ratios)
    else:
        out["prospects_per_gift"] = pd.NA
    default = out["gift_amount"].map(lambda a: prospects_top if a >= top_threshold else prospects_rest)
    out["prospects_per_gift"] = out["prospects_per_gift"].fillna(default).astype("int64")
    out["prospects_needed"] = out["gifts"] * out["prospects_per_gift"]
    return out


def milestone_pace(raised_committed, raised_cash, today=None):
    """
    For each milestone: amount still needed (committed, and cash where the
    milestone has a cash target), months left, and monthly pace required.
    """
    today = today or date.today()
    rows = []
    for m in MILESTONES:
        months_left = (m["due"].year - today.year) * 12 + (m["due"].month - today.month)
        months_left += (m["due"].day - today.day) / 30.0
        gap_committed = max(0, m["committed"] - raised_committed)
        gap_cash = max(0, m["cash"] - raised_cash) if m["cash"] is not None else None
        binding = max(gap_committed, gap_cash or 0)
        rows.append({
            "milestone": m["milestone"],
            "due": m["due"],
            "target_committed": m["committed"],
            "target_cash": m["cash"],
            "gap_committed": gap_committed,
            "gap_cash": gap_cash,
            "months_left": round(months_left, 1),
            "monthly_pace_needed": (binding / months_left) if months_left > 0 else None,
            "status": "met" if binding == 0 else ("past due" if months_left <= 0 else "open"),
            "note": m["note"],
        })
    out = pd.DataFrame(rows)
    # Whole dollars; nullable ints keep "no cash target" as blank, not 0.0
    for col in ("target_cash", "gap_cash", "monthly_pace_needed"):
        out[col] = out[col].round(0).astype("Int64")
    return out
