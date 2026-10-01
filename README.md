# Westfield, NJ — Giving Capacity & Small Business Dashboard

Streamlit dashboard for Rialto Center for Creativity capital campaign
prospect research:

- **Giving Capacity Map / Demographics** — Census ACS 5-year estimates by
  block group for Westfield (state 34, county 039, place 79040), mapped
  against TIGER boundaries. Defaults to the count of households earning
  $200k+ (ACS top-codes median income at $250k, so a median map flattens
  out in Westfield). The town-wide median is interpolated from combined
  income brackets rather than averaged across block-group medians. Also
  maps owner-occupied homes worth $1M+, and ranks block groups for
  canvassing/mailing (with the share of each inside the town line).
- **Charitable Giving by ZIP** — IRS Statistics of Income ZIP data:
  share of returns claiming charitable deductions, average deduction, and
  charity as a share of income, Westfield vs. neighboring ZIPs. Itemizers
  only, so it's a floor on giving, and it lags 2-3 years. Downloaded from
  irs.gov on first use (button), then cached on disk.
- **Small Business Targeting** — Census County Business Patterns sector
  counts, plus a named business list from the Google Places API, mapped
  to NAICS sectors and labeled *known chain* / *likely chain — multiple
  locations* / *likely independent*. Sorted by straight-line distance to
  the Rialto, with a walking-distance filter. A **contact tracker** marks
  each business not contacted / asked / donor / declined / do not
  contact, with notes; handled businesses drop off the list by default.
  Export the filtered list as CSV.

## Chain / independent labels are a heuristic

Google has no franchise field. Labels come from:

1. **`known_chains.txt`** — a plain-text list of brands; edit it to add
   or remove names (format notes at the top of the file).
2. **Multi-location check** — for names not on the list, a Places search
   across New Jersey; the same name at 3+ locations in 2+ towns is
   flagged *likely chain*. This can catch local owners with a few shops,
   and unrelated businesses sharing a generic name (flagged in the note
   column).

Many franchises are locally owned, so *known chain* doesn't mean "no
local decision-maker". Check the `classification_note` column.

## Secrets

The app reads `CENSUS_API_KEY` and `GOOGLE_PLACES_API_KEY` from Streamlit
secrets (falling back to environment variables). See
[`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) for
where to get each key. For local runs, copy it to `.streamlit/secrets.toml`
(git-ignored) and fill in real values.

## Contact tracker setup (Google Sheet)

Streamlit Cloud has no permanent disk, so the tracker is stored in a
Google Sheet. Without this setup it still works, but only for the current
browser session (with a CSV save/load fallback).

1. In Google Cloud Console (the same project as the Places key is fine):
   **APIs & Services → Library** → enable **Google Sheets API**.
2. **IAM & Admin → Service Accounts → Create service account** (name it
   e.g. `rcc-tracker`; no roles needed). Open it → **Keys → Add key →
   JSON**. A key file downloads.
3. Create a Google Sheet, click **Share**, and add the service account's
   `client_email` (from the JSON) as an **Editor**.
4. In the app's secrets, add `TRACKER_SHEET_URL` (the Sheet's URL) and a
   `[gcp_service_account]` section with the fields from the JSON file —
   the template in `.streamlit/secrets.toml.example` shows the layout.

The app creates a `tracker` tab in the Sheet, keyed by Google Place ID.
Volunteers can edit the Sheet directly, too. If two people save the
*same* business at nearly the same moment, the later save wins.

Anyone who can open the app can edit the tracker, so if notes will hold
anything sensitive (gift amounts, personal details), set the app to
private in Streamlit Cloud's sharing settings.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Tests

```bash
pip install pytest
pytest
```

The tests replace census.gov, TIGER and Google Places with offline fakes
(`tests/fakes.py`), so they check app logic, not live API responses.

## Deploy to Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app** → **Deploy a public app from GitHub** (wording may vary).
3. Repository: `mrc329/rcc-db`, branch: the branch holding this code,
   main file path: `app.py`.
4. Open **Advanced settings** before deploying:
   - Python version: 3.11 or 3.12.
   - **Secrets** box — paste, with your real values:
     ```toml
     CENSUS_API_KEY = "your-census-key"
     GOOGLE_PLACES_API_KEY = "your-google-places-key"
     ```
     plus, optionally, the tracker's `TRACKER_SHEET_URL` and
     `[gcp_service_account]` section (see above).
5. **Deploy**. Later changes to secrets: app's **⋮ menu → Settings →
   Secrets**; the app restarts with the new values.
