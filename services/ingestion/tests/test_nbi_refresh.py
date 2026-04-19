"""
Tests for the NBI refresh DAG.

Strategy
--------
The DAG functions (fetch_nbi_data, store_raw_parquet, index_to_search) are
defined *inside* the ``with DAG(...) as dag:`` block, which means they are
not importable as module-level names.  We extract them from the PythonOperator
``python_callable`` attribute after importing the DAG module.

All HTTP traffic is mocked with ``respx``; all Azure SDK calls are patched
with ``unittest.mock``.  No real credentials are required.
"""

import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import respx
from httpx import Response

# ---------------------------------------------------------------------------
# Env-var fixture — must run before the DAG module is imported
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
# Helpers — stub heavy optional imports so the DAG module can be loaded in a
# plain Python environment (no full Airflow/ddtrace install required for unit
# tests, but the test suite is also compatible when they ARE installed).
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs):
    """Insert a minimal stub into sys.modules if the real package is absent."""
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


def _ensure_stubs():
    # ddtrace.auto — just needs to be importable
    _stub_module("ddtrace")
    _stub_module("ddtrace.auto")

    # airflow stubs (only needed when airflow is not installed)
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
# Import the DAG module (reload each test session to pick up env vars)
# ---------------------------------------------------------------------------

DAG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "dags", "nbi_refresh.py"
)
DAG_PATH = os.path.abspath(DAG_PATH)

# We load via importlib.util so we can reference the module object
import importlib.util as ilu

_spec = ilu.spec_from_file_location("nbi_refresh", DAG_PATH)
_nbi_mod = ilu.module_from_spec(_spec)


@pytest.fixture(scope="module")
def nbi_module():
    """Return the loaded nbi_refresh module (loaded once per test module)."""
    with patch("ddtrace.auto", MagicMock()), patch("requests.get", MagicMock()):
        _spec.loader.exec_module(_nbi_mod)
    return _nbi_mod


# ---------------------------------------------------------------------------
# Convenience — pull the callable out of the PythonOperator kwargs dict
# ---------------------------------------------------------------------------

def _get_callable(mod, task_id: str):
    """
    After exec_module the ``with DAG`` block fires and each PythonOperator()
    call is intercepted by our stub, which stores kwargs as a dict.  We walk
    the module globals to find the one matching task_id.
    """
    # The stub PythonOperator records kwargs; operators are stored as plain
    # dicts under names t1/t2/t3 in the module namespace.
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, dict) and obj.get("task_id") == task_id:
            return obj["python_callable"]
    raise KeyError(f"No PythonOperator with task_id={task_id!r} found in module")


# ---------------------------------------------------------------------------
# Fake TaskInstance helper
# ---------------------------------------------------------------------------

class FakeTI:
    def __init__(self):
        self._store = {}

    def xcom_push(self, key, value):
        self._store[key] = value

    def xcom_pull(self, key, task_ids=None):
        return self._store.get(key)


NBI_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services"
    "/National_Bridge_Inventory/FeatureServer/0/query"
)

# ---------------------------------------------------------------------------
# One realistic NBI feature dict
# ---------------------------------------------------------------------------

def _make_feature(struct_num="48000001000A000"):
    return {
        "attributes": {
            "STRUCTURE_NUMBER_008": struct_num,
            "FACILITY_CARRIED_007": "IH 10",
            "LOCATION_009": "HARRIS COUNTY",
            "COUNTY_CODE_003": "201",
            "STATE_CODE_001": "48",
            "ADT_029": 75000,
            "YEAR_ADT_030": 2022,
            "DECK_COND_058": "7",
            "SUPERSTRUCTURE_COND_059": "7",
            "SUBSTRUCTURE_COND_060": "6",
            "STRUCTURALLY_DEFICIENT": "0",
            "SUFFICIENCY_RATING": 82.5,
            "INSPECT_DATE_090": "0922",
            "YEAR_BUILT_027": 1965,
            "LAT_016": 29.7604,
            "LONG_017": -95.3698,
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNbiDagLoads:
    def test_module_has_nbi_arcgis_url(self, nbi_module):
        assert hasattr(nbi_module, "NBI_ARCGIS_URL")
        assert "arcgis" in nbi_module.NBI_ARCGIS_URL

    def test_nbi_fields_constant_contains_expected_fields(self, nbi_module):
        required = [
            "STRUCTURE_NUMBER_008",
            "ADT_029",
            "DECK_COND_058",
            "SUPERSTRUCTURE_COND_059",
            "SUBSTRUCTURE_COND_060",
            "SUFFICIENCY_RATING",
        ]
        for field in required:
            assert field in nbi_module.NBI_FIELDS, f"Missing field: {field}"

    def test_state_code_is_texas(self, nbi_module):
        assert nbi_module.STATE_CODE_TX == "48"

    def test_page_size_defined(self, nbi_module):
        assert nbi_module.PAGE_SIZE == 2000

    def test_condition_labels_present(self, nbi_module):
        labels = nbi_module.CONDITION_LABELS
        assert labels["9"] == "excellent"
        assert labels["0"] == "failed"
        assert labels["4"] == "poor"


class TestFetchNbiData:
    """fetch_nbi_data — single-page happy path."""

    @respx.mock
    def test_calls_correct_arcgis_url(self, nbi_module):
        feature = _make_feature()
        route = respx.get(NBI_URL).mock(
            return_value=Response(200, json={"features": [feature]})
        )

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        fn(ti=ti)

        assert route.called, "fetch_nbi_data did not call the ArcGIS URL"

    @respx.mock
    def test_returns_record_count(self, nbi_module):
        feature = _make_feature()
        respx.get(NBI_URL).mock(
            return_value=Response(200, json={"features": [feature]})
        )

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        result = fn(ti=ti)

        assert result == 1

    @respx.mock
    def test_records_pushed_to_xcom(self, nbi_module):
        feature = _make_feature()
        respx.get(NBI_URL).mock(
            return_value=Response(200, json={"features": [feature]})
        )

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        fn(ti=ti)

        records = ti.xcom_pull("nbi_records")
        assert records is not None
        assert len(records) == 1

    @respx.mock
    def test_record_has_correct_nbi_field_names(self, nbi_module):
        feature = _make_feature()
        respx.get(NBI_URL).mock(
            return_value=Response(200, json={"features": [feature]})
        )

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        fn(ti=ti)

        record = ti.xcom_pull("nbi_records")[0]
        assert "STRUCTURE_NUMBER_008" in record
        assert "ADT_029" in record
        assert "DECK_COND_058" in record
        assert "SUFFICIENCY_RATING" in record

    @respx.mock
    def test_record_values_match_fixture(self, nbi_module):
        feature = _make_feature("48TEST001")
        respx.get(NBI_URL).mock(
            return_value=Response(200, json={"features": [feature]})
        )

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        fn(ti=ti)

        record = ti.xcom_pull("nbi_records")[0]
        assert record["STRUCTURE_NUMBER_008"] == "48TEST001"
        assert record["ADT_029"] == 75000

    @respx.mock
    def test_query_includes_state_code_filter(self, nbi_module):
        """The where clause must reference STATE_CODE_001='48'."""
        feature = _make_feature()
        captured_requests = []

        def capture(request, route):
            captured_requests.append(request)
            return Response(200, json={"features": [feature]})

        respx.get(NBI_URL).mock(side_effect=capture)

        fn = nbi_module.fetch_nbi_data
        fn(ti=FakeTI())

        assert captured_requests, "No request was made"
        url_str = str(captured_requests[0].url)
        assert "48" in url_str or "STATE_CODE_001" in url_str


class TestFetchNbiDataPagination:
    """Verify that fetch_nbi_data follows pagination until results < PAGE_SIZE."""

    @respx.mock
    def test_two_pages_fetched(self, nbi_module):
        page_size = nbi_module.PAGE_SIZE  # 2000
        page1_features = [_make_feature(f"S{i:05d}") for i in range(page_size)]
        page2_features = [_make_feature(f"S{page_size + i:05d}") for i in range(5)]

        call_count = 0

        def side_effect(request, route):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Response(200, json={"features": page1_features})
            return Response(200, json={"features": page2_features})

        respx.get(NBI_URL).mock(side_effect=side_effect)

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        result = fn(ti=ti)

        assert call_count == 2, f"Expected 2 pages fetched, got {call_count}"
        assert result == page_size + 5

    @respx.mock
    def test_three_pages_stop_at_partial_last_page(self, nbi_module):
        page_size = nbi_module.PAGE_SIZE
        full_page = [_make_feature(f"A{i:05d}") for i in range(page_size)]
        partial_page = [_make_feature("ALAST")]

        call_count = 0

        def side_effect(request, route):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return Response(200, json={"features": full_page})
            return Response(200, json={"features": partial_page})

        respx.get(NBI_URL).mock(side_effect=side_effect)

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        result = fn(ti=ti)

        assert call_count == 3
        assert result == page_size * 2 + 1

    @respx.mock
    def test_empty_first_page_returns_zero(self, nbi_module):
        respx.get(NBI_URL).mock(
            return_value=Response(200, json={"features": []})
        )

        fn = nbi_module.fetch_nbi_data
        ti = FakeTI()
        result = fn(ti=ti)

        assert result == 0
        assert ti.xcom_pull("nbi_records") == []


class TestFetchNbiDataErrorHandling:
    """fetch_nbi_data — server error paths."""

    @respx.mock
    def test_raises_on_500(self, nbi_module):
        respx.get(NBI_URL).mock(return_value=Response(500, text="Internal Server Error"))

        fn = nbi_module.fetch_nbi_data
        with pytest.raises(Exception):
            fn(ti=FakeTI())

    @respx.mock
    def test_raises_on_503(self, nbi_module):
        respx.get(NBI_URL).mock(return_value=Response(503, text="Service Unavailable"))

        fn = nbi_module.fetch_nbi_data
        with pytest.raises(Exception):
            fn(ti=FakeTI())

    @respx.mock
    def test_raises_on_404(self, nbi_module):
        respx.get(NBI_URL).mock(return_value=Response(404, text="Not Found"))

        fn = nbi_module.fetch_nbi_data
        with pytest.raises(Exception):
            fn(ti=FakeTI())


class TestNbiConstants:
    """Smoke-check constants and helpers accessible at module level."""

    def test_raw_container_name(self, nbi_module):
        assert nbi_module.RAW_CONTAINER == "infra-advisor-raw"

    def test_chunk_size(self, nbi_module):
        assert nbi_module.CHUNK_SIZE == 500

    def test_all_nbi_field_names_in_fields_string(self, nbi_module):
        expected = [
            "STRUCTURE_NUMBER_008",
            "FACILITY_CARRIED_007",
            "LOCATION_009",
            "COUNTY_CODE_003",
            "STATE_CODE_001",
            "ADT_029",
            "YEAR_ADT_030",
            "DECK_COND_058",
            "SUPERSTRUCTURE_COND_059",
            "SUBSTRUCTURE_COND_060",
            "STRUCTURALLY_DEFICIENT",
            "SUFFICIENCY_RATING",
            "INSPECT_DATE_090",
            "YEAR_BUILT_027",
            "LAT_016",
            "LONG_017",
        ]
        for f in expected:
            assert f in nbi_module.NBI_FIELDS

    def test_condition_labels_complete(self, nbi_module):
        labels = nbi_module.CONDITION_LABELS
        for code in map(str, range(10)):
            assert code in labels, f"Condition code '{code}' missing from CONDITION_LABELS"
