import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import census_utils  # noqa: E402
import geometry_utils  # noqa: E402
import places_utils  # noqa: E402
import fakes  # noqa: E402


@pytest.fixture
def fake_places(monkeypatch):
    fake = fakes.FakePlaces()
    monkeypatch.setattr(places_utils.requests, "Session", lambda: fakes.FakeSession(fake))
    monkeypatch.setattr(places_utils.requests, "post", fake.post)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    places_utils.clear_places_cache()
    yield fake
    places_utils.clear_places_cache()


@pytest.fixture
def fake_census(monkeypatch):
    monkeypatch.setattr(census_utils.requests, "get", fakes.fake_census_get)
    monkeypatch.setattr(geometry_utils, "places", fakes.fake_pygris_places)
    monkeypatch.setattr(geometry_utils, "block_groups", fakes.fake_pygris_block_groups)
    for fn in (census_utils.fetch_block_group_acs,
               census_utils.fetch_naics_establishment_counts,
               census_utils.fetch_naics_employment_size_class,
               geometry_utils.get_westfield_boundary,
               geometry_utils.get_union_county_block_groups):
        fn.clear()
