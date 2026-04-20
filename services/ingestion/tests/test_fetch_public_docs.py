"""
Tests for fetch_public_docs.py

All HTTP calls are mocked with respx. Azure SDK calls (SearchClient,
AzureOpenAI) are patched with unittest.mock.  No real network or cloud
credentials required.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import respx
from httpx import Response

# ---------------------------------------------------------------------------
# Env-var fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "mock-key")
    monkeypatch.setenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://mock.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "mock-key")
    monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")
    monkeypatch.setenv("EIA_API_KEY", "mock-eia-key")
    monkeypatch.setenv("DD_AGENT_HOST", "localhost")


# ---------------------------------------------------------------------------
# Stubs for heavy optional dependencies
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


def _ensure_stubs():
    _stub_module("ddtrace")
    _stub_module("ddtrace.auto")


_ensure_stubs()

# ---------------------------------------------------------------------------
# Load the module under test
# ---------------------------------------------------------------------------

import importlib.util as ilu

_SCRIPT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_public_docs.py")
)


@pytest.fixture(scope="module")
def fpd():
    """Load fetch_public_docs module with ddtrace stubbed out."""
    spec = ilu.spec_from_file_location("fetch_public_docs", _SCRIPT_PATH)
    mod = ilu.module_from_spec(spec)
    with patch.dict(sys.modules, {"ddtrace": MagicMock(), "ddtrace.auto": MagicMock()}):
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FEMA_DISASTER_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
FEMA_HM_URL = "https://www.fema.gov/api/open/v2/HazardMitigationGrantProgramProjectActivities"
EIA_URL = "https://api.eia.gov/v2/electricity/retail-sales/data"
NBI_URL = "https://bridgeapi.azurewebsites.net/api/bridges"


def _make_disaster_record(num=4000, state="TX", itype="Hurricane", year=2022):
    return {
        "disasterNumber": num,
        "incidentType": itype,
        "declarationTitle": f"{itype.upper()} TEST-{num}",
        "stateCode": state,
        "designatedArea": "Harris (County)",
        "declarationDate": f"{year}-09-01T00:00:00.000Z",
    }


def _make_hm_record(state="TX", ptype="Flood Control", cost="500000"):
    return {
        "state": state,
        "projectType": ptype,
        "federalShareObligated": cost,
        "subgrantee": f"Mock County HM Project ({state})",
    }


def _make_eia_row(state="TX", period="2023", price="10.5", sales="300000"):
    return {
        "stateid": state,
        "period": period,
        "price": price,
        "sales": sales,
        "sectorName": "all sectors",
    }


def _make_bridge(county="201", sd="1", year_built="1965", scour="U"):
    return {
        "COUNTY_CODE_003": county,
        "STRUCTURALLY_DEFICIENT": sd,
        "YEAR_BUILT_027": year_built,
        "SCOUR_CRITICAL_113": scour,
        "LOWEST_RATING": "4",
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_state_fips_has_tx(self, fpd):
        assert "TX" in fpd.STATE_FIPS
        assert fpd.STATE_FIPS["TX"] == "48"

    def test_state_names_has_tx(self, fpd):
        assert fpd.STATE_NAMES["TX"] == "Texas"

    def test_target_states_contains_tx(self, fpd):
        assert "TX" in fpd.TARGET_STATES

    def test_chunk_size(self, fpd):
        assert fpd.CHUNK_SIZE_TOKENS == 512

    def test_overlap(self, fpd):
        assert fpd.CHUNK_OVERLAP_TOKENS == 64

    def test_existing_threshold(self, fpd):
        assert fpd.EXISTING_THRESHOLD == 200


# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_short_text_returns_single_chunk(self, fpd):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        chunks = fpd._chunk_text("Hello world", enc)
        assert len(chunks) == 1
        assert "Hello" in chunks[0]

    def test_long_text_produces_multiple_chunks(self, fpd):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        # Generate text well over 512 tokens
        long_text = " ".join(["infrastructure"] * 600)
        chunks = fpd._chunk_text(long_text, enc)
        assert len(chunks) >= 2

    def test_chunks_overlap(self, fpd):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        long_text = " ".join([f"word{i}" for i in range(700)])
        chunks = fpd._chunk_text(long_text, enc)
        # Verify last token of chunk N appears somewhere in chunk N+1
        if len(chunks) >= 2:
            last_word_of_first = chunks[0].strip().split()[-1]
            assert last_word_of_first in chunks[1]

    def test_empty_text_returns_empty(self, fpd):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        chunks = fpd._chunk_text("", enc)
        assert chunks == []


# ---------------------------------------------------------------------------
# fetch_fema_disaster_profiles
# ---------------------------------------------------------------------------

class TestFetchFemaDisasterProfiles:
    @respx.mock
    def test_returns_one_doc_per_state(self, fpd):
        for state in ["TX", "LA"]:
            respx.get(FEMA_DISASTER_URL).mock(
                return_value=Response(
                    200,
                    json={"DisasterDeclarationsSummaries": [_make_disaster_record(state=state)]},
                )
            )
        docs = fpd.fetch_fema_disaster_profiles(["TX", "LA"])
        assert len(docs) == 2

    @respx.mock
    def test_doc_has_required_schema_fields(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(
                200,
                json={"DisasterDeclarationsSummaries": [_make_disaster_record()]},
            )
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        doc = docs[0]
        assert "id" in doc
        assert "content" in doc
        assert "source" in doc
        assert "document_type" in doc
        assert "domain" in doc

    @respx.mock
    def test_doc_source_label(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(
                200,
                json={"DisasterDeclarationsSummaries": [_make_disaster_record()]},
            )
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert docs[0]["source"] == "OpenFEMA_Disaster_Declarations"

    @respx.mock
    def test_doc_domain_is_disaster(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(
                200,
                json={"DisasterDeclarationsSummaries": [_make_disaster_record()]},
            )
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert docs[0]["domain"] == "disaster"

    @respx.mock
    def test_content_includes_state_name(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(
                200,
                json={"DisasterDeclarationsSummaries": [_make_disaster_record()]},
            )
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert "Texas" in docs[0]["content"]

    @respx.mock
    def test_content_includes_incident_type(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(
                200,
                json={
                    "DisasterDeclarationsSummaries": [
                        _make_disaster_record(itype="Flood")
                    ]
                },
            )
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert "Flood" in docs[0]["content"]

    @respx.mock
    def test_api_error_skips_state_gracefully(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(return_value=Response(500, text="error"))
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert docs == []

    @respx.mock
    def test_empty_records_returns_no_docs(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(200, json={"DisasterDeclarationsSummaries": []})
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert docs == []

    @respx.mock
    def test_id_includes_state_code(self, fpd):
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(
                200,
                json={"DisasterDeclarationsSummaries": [_make_disaster_record()]},
            )
        )
        docs = fpd.fetch_fema_disaster_profiles(["TX"])
        assert "tx" in docs[0]["id"]


# ---------------------------------------------------------------------------
# fetch_fema_hm_projects
# ---------------------------------------------------------------------------

class TestFetchFemaHmProjects:
    @respx.mock
    def test_returns_one_doc_per_state(self, fpd):
        for state in ["TX", "FL"]:
            respx.get(FEMA_HM_URL).mock(
                return_value=Response(
                    200,
                    json={"HazardMitigationGrantProgramProjectActivities": [_make_hm_record(state=state)]},
                )
            )
        docs = fpd.fetch_fema_hm_projects(["TX", "FL"])
        assert len(docs) == 2

    @respx.mock
    def test_doc_source_label(self, fpd):
        respx.get(FEMA_HM_URL).mock(
            return_value=Response(
                200,
                json={"HazardMitigationGrantProgramProjectActivities": [_make_hm_record()]},
            )
        )
        docs = fpd.fetch_fema_hm_projects(["TX"])
        assert docs[0]["source"] == "OpenFEMA_Hazard_Mitigation"

    @respx.mock
    def test_doc_domain_is_disaster(self, fpd):
        respx.get(FEMA_HM_URL).mock(
            return_value=Response(
                200,
                json={"HazardMitigationGrantProgramProjectActivities": [_make_hm_record()]},
            )
        )
        docs = fpd.fetch_fema_hm_projects(["TX"])
        assert docs[0]["domain"] == "disaster"

    @respx.mock
    def test_content_includes_project_type(self, fpd):
        respx.get(FEMA_HM_URL).mock(
            return_value=Response(
                200,
                json={
                    "HazardMitigationGrantProgramProjectActivities": [
                        _make_hm_record(ptype="Wind Retrofit")
                    ]
                },
            )
        )
        docs = fpd.fetch_fema_hm_projects(["TX"])
        assert "Wind Retrofit" in docs[0]["content"]

    @respx.mock
    def test_api_error_skips_state(self, fpd):
        respx.get(FEMA_HM_URL).mock(return_value=Response(503, text="service unavailable"))
        docs = fpd.fetch_fema_hm_projects(["TX"])
        assert docs == []

    @respx.mock
    def test_empty_response_returns_no_docs(self, fpd):
        respx.get(FEMA_HM_URL).mock(
            return_value=Response(
                200, json={"HazardMitigationGrantProgramProjectActivities": []}
            )
        )
        docs = fpd.fetch_fema_hm_projects(["TX"])
        assert docs == []


# ---------------------------------------------------------------------------
# fetch_eia_state_profiles
# ---------------------------------------------------------------------------

class TestFetchEiaStateProfiles:
    def test_returns_empty_when_no_api_key(self, fpd, monkeypatch):
        monkeypatch.delenv("EIA_API_KEY", raising=False)
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert docs == []

    @respx.mock
    def test_returns_one_doc_per_state(self, fpd):
        respx.get(EIA_URL).mock(
            return_value=Response(
                200,
                json={"response": {"data": [_make_eia_row()]}},
            )
        )
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert len(docs) == 1

    @respx.mock
    def test_doc_source_label(self, fpd):
        respx.get(EIA_URL).mock(
            return_value=Response(200, json={"response": {"data": [_make_eia_row()]}})
        )
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert docs[0]["source"] == "EIA_Electricity_Retail"

    @respx.mock
    def test_doc_domain_is_energy(self, fpd):
        respx.get(EIA_URL).mock(
            return_value=Response(200, json={"response": {"data": [_make_eia_row()]}})
        )
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert docs[0]["domain"] == "energy"

    @respx.mock
    def test_content_includes_state_name(self, fpd):
        respx.get(EIA_URL).mock(
            return_value=Response(200, json={"response": {"data": [_make_eia_row()]}})
        )
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert "Texas" in docs[0]["content"]

    @respx.mock
    def test_content_includes_price_data(self, fpd):
        respx.get(EIA_URL).mock(
            return_value=Response(
                200,
                json={"response": {"data": [_make_eia_row(price="12.34")]}},
            )
        )
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert "12.34" in docs[0]["content"]

    @respx.mock
    def test_api_error_skips_state(self, fpd):
        respx.get(EIA_URL).mock(return_value=Response(403, text="forbidden"))
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert docs == []

    @respx.mock
    def test_empty_data_returns_no_docs(self, fpd):
        respx.get(EIA_URL).mock(
            return_value=Response(200, json={"response": {"data": []}})
        )
        docs = fpd.fetch_eia_state_profiles(["TX"])
        assert docs == []


# ---------------------------------------------------------------------------
# fetch_nbi_county_summaries
# ---------------------------------------------------------------------------

class TestFetchNbiCountySummaries:
    @respx.mock
    def test_returns_one_doc_per_state(self, fpd):
        respx.get(NBI_URL).mock(
            return_value=Response(200, json=[_make_bridge()])
        )
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert len(docs) == 1

    @respx.mock
    def test_doc_source_label(self, fpd):
        respx.get(NBI_URL).mock(return_value=Response(200, json=[_make_bridge()]))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert docs[0]["source"] == "FHWA_NBI"

    @respx.mock
    def test_doc_domain_is_transportation(self, fpd):
        respx.get(NBI_URL).mock(return_value=Response(200, json=[_make_bridge()]))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert docs[0]["domain"] == "transportation"

    @respx.mock
    def test_content_includes_state_name(self, fpd):
        respx.get(NBI_URL).mock(return_value=Response(200, json=[_make_bridge()]))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert "Texas" in docs[0]["content"]

    @respx.mock
    def test_structurally_deficient_count_in_content(self, fpd):
        bridges = [
            _make_bridge(county="201", sd="1"),
            _make_bridge(county="201", sd="1"),
            _make_bridge(county="201", sd="0"),
        ]
        respx.get(NBI_URL).mock(return_value=Response(200, json=bridges))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert "2" in docs[0]["content"]  # 2 structurally deficient

    @respx.mock
    def test_skips_unknown_state(self, fpd):
        # "ZZ" is not in STATE_FIPS, should produce no docs
        docs = fpd.fetch_nbi_county_summaries(["ZZ"])
        assert docs == []

    @respx.mock
    def test_api_error_skips_state(self, fpd):
        respx.get(NBI_URL).mock(return_value=Response(500, text="error"))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert docs == []

    @respx.mock
    def test_empty_list_returns_no_docs(self, fpd):
        respx.get(NBI_URL).mock(return_value=Response(200, json=[]))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert docs == []

    @respx.mock
    def test_id_includes_state_code(self, fpd):
        respx.get(NBI_URL).mock(return_value=Response(200, json=[_make_bridge()]))
        docs = fpd.fetch_nbi_county_summaries(["TX"])
        assert "tx" in docs[0]["id"]


# ---------------------------------------------------------------------------
# main() — idempotency check
# ---------------------------------------------------------------------------

class TestMainIdempotency:
    def test_exits_early_when_threshold_met(self, fpd):
        mock_search = MagicMock()
        mock_results = MagicMock()
        mock_results.get_count.return_value = 300  # above threshold of 200
        mock_search.search.return_value = mock_results

        mock_oai = MagicMock()

        # Patch at module namespace — SearchClient and AzureOpenAI are imported
        # at the top of fetch_public_docs, so we patch them there.
        with (
            patch.object(fpd, "SearchClient", return_value=mock_search),
            patch.object(fpd, "AzureOpenAI", return_value=mock_oai),
        ):
            fpd.main()

        # search was called once (idempotency check), but upsert_documents never called
        mock_search.upsert_documents.assert_not_called()

    @respx.mock
    def test_runs_fetchers_when_below_threshold(self, fpd):
        mock_search = MagicMock()
        mock_results = MagicMock()
        mock_results.get_count.return_value = 0  # below threshold
        mock_search.search.return_value = mock_results

        mock_oai = MagicMock()
        mock_oai.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 1536)]
        )

        # Mock all four external APIs
        respx.get(FEMA_DISASTER_URL).mock(
            return_value=Response(200, json={"DisasterDeclarationsSummaries": [_make_disaster_record()]})
        )
        respx.get(FEMA_HM_URL).mock(
            return_value=Response(200, json={"HazardMitigationGrantProgramProjectActivities": [_make_hm_record()]})
        )
        respx.get(EIA_URL).mock(
            return_value=Response(200, json={"response": {"data": [_make_eia_row()]}})
        )
        respx.get(NBI_URL).mock(
            return_value=Response(200, json=[_make_bridge()])
        )

        with (
            patch.object(fpd, "SearchClient", return_value=mock_search),
            patch.object(fpd, "AzureOpenAI", return_value=mock_oai),
        ):
            fpd.main()

        # Upsert should have been called at least once (documents were indexed)
        assert mock_search.upsert_documents.called
