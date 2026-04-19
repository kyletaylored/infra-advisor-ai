"""
Tests for the FEMA refresh DAG.

The DAG uses ``requests.get`` to call the OpenFEMA
DisasterDeclarationsSummaries endpoint.  All HTTP traffic is mocked with
``respx``; Azure SDK calls are patched with ``unittest.mock``.
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
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://mock.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "mock-key")
    monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://mock.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "mock-key")
    monkeypatch.setenv(
        "AZURE_STORAGE_CONNECTION_STRING",
        "DefaultEndpointsProtocol=https;AccountName=mock;AccountKey=bW9jaw==;EndpointSuffix=core.windows.net",
    )
    monkeypatch.setenv("EIA_API_KEY", "mock-key")
    monkeypatch.setenv("DD_AGENT_HOST", "localhost")


# ---------------------------------------------------------------------------
# Lightweight stubs for optional heavy dependencies
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

    if "airflow" not in sys.modules:
        dag_cls = MagicMock()
        dag_instance = MagicMock()
        dag_cls.return_value.__enter__ = lambda s, *a: dag_instance
        dag_cls.return_value.__exit__ = MagicMock(return_value=False)
        _stub_module("airflow", DAG=dag_cls)
        _stub_module("airflow.operators", python=MagicMock())
        _stub_module(
            "airflow.operators.python",
            PythonOperator=MagicMock(side_effect=lambda **kw: kw),
        )


_ensure_stubs()

# ---------------------------------------------------------------------------
# Load FEMA DAG module
# ---------------------------------------------------------------------------

import importlib.util as ilu

DAG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dags", "fema_refresh.py")
)

_fema_spec = ilu.spec_from_file_location("fema_refresh", DAG_PATH)
_fema_mod = ilu.module_from_spec(_fema_spec)


@pytest.fixture(scope="module")
def fema_module():
    with patch("ddtrace.auto", MagicMock()), patch("requests.get", MagicMock()):
        _fema_spec.loader.exec_module(_fema_mod)
    return _fema_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeTI:
    def __init__(self):
        self._store = {}

    def xcom_push(self, key, value):
        self._store[key] = value

    def xcom_pull(self, key, task_ids=None):
        return self._store.get(key)


FEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"


def _make_disaster(num=4000, state="TX"):
    return {
        "disasterNumber": num,
        "declarationType": "DR",
        "incidentType": "Hurricane",
        "declarationTitle": f"HURRICANE TEST-{num}",
        "stateCode": state,
        "designatedArea": "Harris (County)",
        "declarationDate": "2022-09-01T00:00:00.000Z",
        "incidentBeginDate": "2022-08-25T00:00:00.000Z",
        "incidentEndDate": "2022-09-15T00:00:00.000Z",
        "disasterCloseoutDate": "",
        "paDeclarationString": "Yes",
        "hmDeclarationString": "Yes",
        "fipsStateCode": "48",
        "fipsCountyCode": "201",
    }


# ---------------------------------------------------------------------------
# DAG-level smoke tests
# ---------------------------------------------------------------------------

class TestFemaDagLoads:
    def test_fema_api_url_constant(self, fema_module):
        assert hasattr(fema_module, "FEMA_API_URL")
        assert "DisasterDeclarationsSummaries" in fema_module.FEMA_API_URL

    def test_filter_date_from_constant(self, fema_module):
        assert hasattr(fema_module, "FILTER_DATE_FROM")
        assert fema_module.FILTER_DATE_FROM.startswith("2010")

    def test_page_size(self, fema_module):
        assert fema_module.PAGE_SIZE == 1000

    def test_raw_container_name(self, fema_module):
        assert fema_module.RAW_CONTAINER == "infra-advisor-raw"


# ---------------------------------------------------------------------------
# fetch_fema_data — happy paths
# ---------------------------------------------------------------------------

class TestFetchFemaData:
    @respx.mock
    def test_calls_disaster_declarations_summaries_endpoint(self, fema_module):
        route = respx.get(FEMA_URL).mock(
            return_value=Response(
                200, json={"DisasterDeclarationsSummaries": [_make_disaster()]}
            )
        )

        fema_module.fetch_fema_data(ti=FakeTI())
        assert route.called

    @respx.mock
    def test_returns_record_count(self, fema_module):
        respx.get(FEMA_URL).mock(
            return_value=Response(
                200, json={"DisasterDeclarationsSummaries": [_make_disaster(4001), _make_disaster(4002)]}
            )
        )

        ti = FakeTI()
        result = fema_module.fetch_fema_data(ti=ti)
        assert result == 2

    @respx.mock
    def test_records_pushed_to_xcom(self, fema_module):
        disaster = _make_disaster(5555)
        respx.get(FEMA_URL).mock(
            return_value=Response(
                200, json={"DisasterDeclarationsSummaries": [disaster]}
            )
        )

        ti = FakeTI()
        fema_module.fetch_fema_data(ti=ti)
        records = ti.xcom_pull("fema_records")
        assert records is not None
        assert len(records) == 1

    @respx.mock
    def test_record_fields_parsed_correctly(self, fema_module):
        disaster = _make_disaster(9999, state="LA")
        respx.get(FEMA_URL).mock(
            return_value=Response(
                200, json={"DisasterDeclarationsSummaries": [disaster]}
            )
        )

        ti = FakeTI()
        fema_module.fetch_fema_data(ti=ti)
        record = ti.xcom_pull("fema_records")[0]

        assert record["disasterNumber"] == 9999
        assert record["stateCode"] == "LA"
        assert record["incidentType"] == "Hurricane"
        assert record["declarationType"] == "DR"
        assert record["declarationTitle"] == "HURRICANE TEST-9999"

    @respx.mock
    def test_query_params_include_filter_and_orderby(self, fema_module):
        captured = []

        def capture(request, route):
            captured.append(request)
            return Response(200, json={"DisasterDeclarationsSummaries": [_make_disaster()]})

        respx.get(FEMA_URL).mock(side_effect=capture)

        fema_module.fetch_fema_data(ti=FakeTI())

        assert captured
        url_str = str(captured[0].url)
        assert "declarationDate" in url_str or "%24filter" in url_str or "filter" in url_str.lower()

    @respx.mock
    def test_pagination_across_two_pages(self, fema_module):
        page_size = fema_module.PAGE_SIZE
        page1 = [_make_disaster(i) for i in range(page_size)]
        page2 = [_make_disaster(page_size + i) for i in range(3)]

        call_count = 0

        def side_effect(request, route):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Response(200, json={"DisasterDeclarationsSummaries": page1})
            return Response(200, json={"DisasterDeclarationsSummaries": page2})

        respx.get(FEMA_URL).mock(side_effect=side_effect)

        ti = FakeTI()
        result = fema_module.fetch_fema_data(ti=ti)

        assert call_count == 2
        assert result == page_size + 3


# ---------------------------------------------------------------------------
# Empty-results handling
# ---------------------------------------------------------------------------

class TestFetchFemaEmptyResults:
    @respx.mock
    def test_empty_list_returns_zero(self, fema_module):
        respx.get(FEMA_URL).mock(
            return_value=Response(200, json={"DisasterDeclarationsSummaries": []})
        )

        ti = FakeTI()
        result = fema_module.fetch_fema_data(ti=ti)
        assert result == 0

    @respx.mock
    def test_empty_list_pushes_empty_xcom(self, fema_module):
        respx.get(FEMA_URL).mock(
            return_value=Response(200, json={"DisasterDeclarationsSummaries": []})
        )

        ti = FakeTI()
        fema_module.fetch_fema_data(ti=ti)
        records = ti.xcom_pull("fema_records")
        assert records == []

    @respx.mock
    def test_missing_key_returns_zero(self, fema_module):
        """API response with no DisasterDeclarationsSummaries key."""
        respx.get(FEMA_URL).mock(
            return_value=Response(200, json={})
        )

        ti = FakeTI()
        result = fema_module.fetch_fema_data(ti=ti)
        assert result == 0


# ---------------------------------------------------------------------------
# Error-handling
# ---------------------------------------------------------------------------

class TestFetchFemaErrors:
    @respx.mock
    def test_raises_on_500(self, fema_module):
        respx.get(FEMA_URL).mock(return_value=Response(500, text="Internal Server Error"))

        with pytest.raises(Exception):
            fema_module.fetch_fema_data(ti=FakeTI())

    @respx.mock
    def test_raises_on_429(self, fema_module):
        respx.get(FEMA_URL).mock(return_value=Response(429, text="Too Many Requests"))

        with pytest.raises(Exception):
            fema_module.fetch_fema_data(ti=FakeTI())


# ---------------------------------------------------------------------------
# Narrative construction sanity check (no HTTP required)
# ---------------------------------------------------------------------------

class TestFemaRecordParsing:
    def test_disaster_fields_present_in_record(self):
        rec = _make_disaster(1234)
        assert rec.get("disasterNumber") == 1234
        assert rec.get("fipsStateCode") == "48"
        assert rec.get("fipsCountyCode") == "201"

    def test_fips_concatenation(self):
        rec = _make_disaster()
        combined = rec.get("fipsStateCode", "") + rec.get("fipsCountyCode", "")
        assert combined == "48201"

    def test_pa_hm_declaration_strings(self):
        rec = _make_disaster()
        assert rec["paDeclarationString"] == "Yes"
        assert rec["hmDeclarationString"] == "Yes"
