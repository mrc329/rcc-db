"""
Contact tracker for business prospects: who's been asked, who gave, who
said no, who not to contact.

Streamlit Community Cloud has no durable disk, so the tracker lives in a
Google Sheet that the app reads and writes through a Google Cloud service
account (see README "Contact tracker setup"). Volunteers can also edit
the Sheet directly.

Without the Sheet configured, the tracker still works for the current
browser session, and can be saved/loaded as a CSV by hand.

Rows are keyed by Google Place ID, so a business keeps its status across
searches even if its name or address changes.
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from config import get_secret

STATUS_NOT_CONTACTED = "not contacted"
STATUS_ASKED = "asked — awaiting reply"
STATUS_DONOR = "donor / pledged"
STATUS_DECLINED = "declined"
STATUS_DO_NOT_CONTACT = "do not contact"
STATUSES = [STATUS_NOT_CONTACTED, STATUS_ASKED, STATUS_DONOR, STATUS_DECLINED, STATUS_DO_NOT_CONTACT]

# Statuses that are still live prospects; the rest drop off the list by default.
OPEN_STATUSES = [STATUS_NOT_CONTACTED, STATUS_ASKED]

COLUMNS = ["place_id", "name", "address", "status", "notes", "updated_at"]
WORKSHEET_NAME = "tracker"


def _empty():
    return pd.DataFrame(columns=COLUMNS)


def _clean(df):
    df = df.copy()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNS].fillna("").astype(str)
    df = df[df["place_id"].str.strip() != ""]
    df.loc[~df["status"].isin(STATUSES), "status"] = STATUS_NOT_CONTACTED
    # One row per business; the latest edit wins if a sheet has duplicates.
    return df.sort_values("updated_at").drop_duplicates("place_id", keep="last").reset_index(drop=True)


class SessionStore:
    """Tracker kept in st.session_state — lost when the browser tab closes."""

    persistent = False
    description = "this browser session only (not saved)"

    def load(self):
        return st.session_state.get("_tracker_df", _empty()).copy()

    def upsert(self, rows):
        st.session_state["_tracker_df"] = merge_rows(self.load(), rows)


class GoogleSheetStore:
    """Tracker kept in a Google Sheet worksheet named WORKSHEET_NAME."""

    persistent = True
    description = "shared Google Sheet"

    def __init__(self, sheet_url, service_account_info):
        import gspread  # imported lazily so the app runs without it configured

        client = gspread.service_account_from_dict(dict(service_account_info))
        spreadsheet = client.open_by_url(sheet_url)
        try:
            self.ws = spreadsheet.worksheet(WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            self.ws = spreadsheet.add_worksheet(WORKSHEET_NAME, rows=1000, cols=len(COLUMNS))
            self.ws.update([COLUMNS], "A1")

    def load(self):
        records = self.ws.get_all_records(expected_headers=COLUMNS, numericise_ignore=["all"])
        return _clean(pd.DataFrame(records)) if records else _empty()

    def upsert(self, rows):
        # Re-read just before writing so edits made by others in the
        # meantime (in the app or directly in the Sheet) aren't clobbered,
        # except for the same business — last save wins there.
        merged = merge_rows(self.load(), rows)
        values = [COLUMNS] + merged[COLUMNS].values.tolist()
        # No clear() first: an upsert never shrinks the table, so this
        # overwrites every old cell, and a failed write can't leave the
        # sheet empty.
        self.ws.update(values, "A1")


def merge_rows(existing, rows):
    """Upsert `rows` into `existing` by place_id, stamping updated_at."""
    rows = rows.copy()
    rows["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    combined = pd.concat([_clean(existing), _clean(rows)], ignore_index=True)
    return combined.drop_duplicates("place_id", keep="last").reset_index(drop=True)


def get_store():
    """
    Return the Google Sheet store if TRACKER_SHEET_URL and
    gcp_service_account secrets are set, else a session-only store.
    Returns (store, error) — error is a message if the Sheet was
    configured but couldn't be opened.
    """
    sheet_url = get_secret("TRACKER_SHEET_URL")
    if not sheet_url:
        return SessionStore(), None
    try:
        account = st.secrets["gcp_service_account"]
    except Exception:
        return SessionStore(), (
            "TRACKER_SHEET_URL is set but the [gcp_service_account] secret is missing."
        )
    try:
        return _cached_sheet_store(sheet_url, tuple(sorted(dict(account).items()))), None
    except Exception as e:
        return SessionStore(), f"Couldn't open the tracker Google Sheet: {e}"


@st.cache_resource(show_spinner=False)
def _cached_sheet_store(sheet_url, account_items):
    return GoogleSheetStore(sheet_url, dict(account_items))


def attach_tracker(businesses, tracker):
    """Left-join tracker status/notes onto a businesses DataFrame by place_id."""
    out = businesses.merge(
        tracker[["place_id", "status", "notes", "updated_at"]], on="place_id", how="left"
    )
    out["status"] = out["status"].fillna(STATUS_NOT_CONTACTED).replace("", STATUS_NOT_CONTACTED)
    out["notes"] = out["notes"].fillna("")
    out["updated_at"] = out["updated_at"].fillna("")
    return out


def changed_rows(before, after):
    """Rows of `after` whose status or notes differ from `before` (same index)."""
    mask = (before["status"] != after["status"]) | (before["notes"] != after["notes"])
    return after.loc[mask, ["place_id", "name", "address", "status", "notes"]]


@st.cache_data(ttl=60, show_spinner=False, hash_funcs={GoogleSheetStore: id, SessionStore: id})
def _load_cached(store):
    return store.load()


def load_tracker(store):
    """Load the tracker; Sheet reads are cached for a minute to spare its API quota."""
    if store.persistent:
        return _load_cached(store)
    return store.load()


load_tracker.clear = _load_cached.clear
