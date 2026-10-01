# Westfield, NJ — Giving Capacity & Small Business Dashboard

Streamlit dashboard for Rialto Center for Creativity capital campaign
prospect research:

- **Income Map / Demographics** — Census ACS 5-year estimates by block
  group for Westfield (state 34, county 039, place 79040), mapped against
  TIGER boundaries.
- **Small Business Targeting** — Census County Business Patterns sector
  counts, plus a named business list from the Google Places API, mapped
  to NAICS sectors and labeled *known chain* / *likely chain — multiple
  locations* / *likely independent*. Filter by sector and chain status;
  export the filtered list as CSV.

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
5. **Deploy**. Later changes to secrets: app's **⋮ menu → Settings →
   Secrets**; the app restarts with the new values.
