"""
Tests for the TWDB water plan + EPA SDWIS DAG.

Two independent fetch tasks are tested:

* fetch_twdb_workbook  — downloads an Excel workbook (mocked as bytes) and
                         parses TWDB water plan project records.
* fetch_epa_sdwis      — calls EPA Envirofacts at the path
                         WATER_SYSTEM/STATE_CODE/TX/PWS_TYPE_CODE/CWS/JSON
                         and returns water system records keyed by PWSID.

All HTTP traffic is intercepted with ``respx``.
Azure SDK calls are patched with ``unittest.mock``.
"""

import io
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pandas as pd
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
    monkeypatch.setenv(
        "TWDB_WATER_PLAN_WORKBOOK_URL",
        "https://mock.twdb.texas.gov/waterplan2026.xlsx",
    )
    monkeypatch.setenv(
        "EPA_SDWIS_BASE_URL",
        "https://enviro.epa.gov/enviro/efservice",
    )


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
# Load TWDB DAG module
# ---------------------------------------------------------------------------

import importlib.util as ilu

DAG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dags", "twdb_water_plan_refresh.py")
)

_twdb_spec = ilu.spec_from_file_location("twdb_water_plan_refresh", DAG_PATH)
_twdb_mod = ilu.module_from_spec(_twdb_spec)


@pytest.fixture(scope="module")
def twdb_module():
    with patch("ddtrace.auto", MagicMock()), patch("requests.get", MagicMock()):
        _twdb_spec.loader.exec_module(_twdb_mod)
    return _twdb_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TWDB_WORKBOOK_URL = "https://mock.twdb.texas.gov/waterplan2026.xlsx"
EPA_SDWIS_BASE = "https://enviro.epa.gov/enviro/efservice"
EPA_SDWIS_URL = f"{EPA_SDWIS_BASE}/WATER_SYSTEM/STATE_CODE/TX/PWS_TYPE_CODE/CWS/JSON"


class FakeTI:
    def __init__(self):
        self._store = {}

    def xcom_push(self, key, value):
        self._store[key] = value

    def xcom_pull(self, key, task_ids=None):
        return self._store.get(key)


def _make_workbook_bytes(rows: list[dict]) -> bytes:
    """Return a minimal Excel workbook as bytes using pandas + openpyxl."""
    if not rows:
        rows = [{"Project Name": "", "Region": "", "County": ""}]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return buf.read()


def _make_project_rows(n: int = 3) -> list[dict]:
    return [
        {
            "Project Name": f"Aquifer Storage Project {i}",
            "Region": chr(ord("A") + (i % 16)),
            "County": "Travis",
            "Water User Group": "City of Austin",
            "Strategy Type": "Aquifer Storage and Recovery",
            "Project Sponsor": "LCRA",
            "2030 Capital Cost": f"{10 + i}",
            "2040 Capital Cost": "",
            "2050 Capital Cost": "",
            "2060 Capital Cost": "",
            "2070 Capital Cost": "",
            "2080 Capital Cost": "",
            "Water Supply Volume": f"{500 * (i + 1)}",
            "Supply Type": "Groundwater",
            "Decade of Need": "2030",
        }
        for i in range(n)
    ]


def _make_sdwis_records(n: int = 2) -> list[dict]:
    return [
        {
            "PWSID": f"TX{1000000 + i:07d}",
            "PWS_NAME": f"City of Test {i} Water System",
            "CITY_NAME": f"Testville{i}",
            "COUNTY_SERVED": "Harris",
            "POPULATION_SERVED_COUNT": str(50000 + i * 1000),
            "PRIMARY_SOURCE_CODE": "GW",
            "PWS_ACTIVITY_CODE": "A",
            "OWNER_TYPE_CODE": "L",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# DAG-level smoke tests
# ---------------------------------------------------------------------------

class TestTwdbDagLoads:
    def test_raw_container_constant(self, twdb_module):
        assert twdb_module.RAW_CONTAINER == "infra-advisor-raw"

    def test_twdb_regions_has_16_entries(self, twdb_module):
        # A through P inclusive = 16 regions
        assert len(twdb_module.TWDB_REGIONS) == 16
        assert "A" in twdb_module.TWDB_REGIONS
        assert "P" in twdb_module.TWDB_REGIONS

    def test_column_map_has_required_keys(self, twdb_module):
        required_keys = [
            "project_name", "county", "region", "cost_2030", "cost_2080", "volume",
        ]
        for key in required_keys:
            assert key in twdb_module.TWDB_COLUMN_MAP, f"Missing column map key: {key}"


# ---------------------------------------------------------------------------
# fetch_twdb_workbook
# ---------------------------------------------------------------------------

class TestFetchTwdbWorkbook:
    @respx.mock
    def test_downloads_workbook_from_env_url(self, twdb_module, mock_env):
        rows = _make_project_rows(2)
        workbook_bytes = _make_workbook_bytes(rows)

        route = respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")

        assert route.called

    @respx.mock
    def test_returns_project_count(self, twdb_module, mock_env):
        rows = _make_project_rows(5)
        workbook_bytes = _make_workbook_bytes(rows)

        respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            result = twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")

        assert result == 5

    @respx.mock
    def test_projects_pushed_to_xcom(self, twdb_module, mock_env):
        rows = _make_project_rows(3)
        workbook_bytes = _make_workbook_bytes(rows)

        respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")

        projects = ti.xcom_pull("twdb_projects")
        assert projects is not None
        assert len(projects) == 3

    @respx.mock
    def test_project_has_region_code(self, twdb_module, mock_env):
        rows = _make_project_rows(1)
        rows[0]["Region"] = "C"
        workbook_bytes = _make_workbook_bytes(rows)

        respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")

        projects = ti.xcom_pull("twdb_projects")
        assert projects[0]["region"] == "C"

    @respx.mock
    def test_project_has_cost_fields(self, twdb_module, mock_env):
        rows = _make_project_rows(1)
        rows[0]["2030 Capital Cost"] = "42"
        workbook_bytes = _make_workbook_bytes(rows)

        respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")

        projects = ti.xcom_pull("twdb_projects")
        assert "cost_2030" in projects[0]
        assert projects[0]["cost_2030"] == "42"

    @respx.mock
    def test_project_record_keys(self, twdb_module, mock_env):
        """Parsed project dicts must include all canonical field names."""
        rows = _make_project_rows(1)
        workbook_bytes = _make_workbook_bytes(rows)

        respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")

        project = ti.xcom_pull("twdb_projects")[0]
        for key in [
            "project_name", "county", "region", "water_user_group",
            "strategy_type", "project_sponsor", "cost_2030", "cost_2040",
            "cost_2050", "cost_2060", "cost_2070", "cost_2080",
            "volume", "supply_type", "decade_of_need",
        ]:
            assert key in project, f"Expected key '{key}' missing from project dict"

    @respx.mock
    def test_raises_on_500(self, twdb_module, mock_env):
        respx.get(TWDB_WORKBOOK_URL).mock(return_value=Response(500, text="Server Error"))

        with pytest.raises(Exception):
            twdb_module.fetch_twdb_workbook(ti=FakeTI(), ds="2026-04-01")


# ---------------------------------------------------------------------------
# fetch_epa_sdwis
# ---------------------------------------------------------------------------

class TestFetchEpaSdwis:
    @respx.mock
    def test_calls_correct_sdwis_path(self, twdb_module, mock_env):
        """Must request STATE_CODE/TX/PWS_TYPE_CODE/CWS."""
        records = _make_sdwis_records(2)
        route = respx.get(EPA_SDWIS_URL).mock(
            return_value=Response(200, json=records)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        assert route.called
        called_url = str(route.calls[0].request.url)
        assert "STATE_CODE" in called_url
        assert "TX" in called_url
        assert "PWS_TYPE_CODE" in called_url
        assert "CWS" in called_url

    @respx.mock
    def test_returns_record_count(self, twdb_module, mock_env):
        records = _make_sdwis_records(4)
        respx.get(EPA_SDWIS_URL).mock(return_value=Response(200, json=records))

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            result = twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        assert result == 4

    @respx.mock
    def test_records_pushed_to_xcom(self, twdb_module, mock_env):
        records = _make_sdwis_records(3)
        respx.get(EPA_SDWIS_URL).mock(return_value=Response(200, json=records))

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        pushed = ti.xcom_pull("sdwis_records")
        assert pushed is not None
        assert len(pushed) == 3

    @respx.mock
    def test_records_contain_pwsid_field(self, twdb_module, mock_env):
        records = _make_sdwis_records(1)
        respx.get(EPA_SDWIS_URL).mock(return_value=Response(200, json=records))

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        record = ti.xcom_pull("sdwis_records")[0]
        assert "PWSID" in record
        assert record["PWSID"].startswith("TX")

    @respx.mock
    def test_record_has_system_name_and_city(self, twdb_module, mock_env):
        records = _make_sdwis_records(1)
        respx.get(EPA_SDWIS_URL).mock(return_value=Response(200, json=records))

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        record = ti.xcom_pull("sdwis_records")[0]
        assert "PWS_NAME" in record
        assert "CITY_NAME" in record
        assert "POPULATION_SERVED_COUNT" in record

    @respx.mock
    def test_raises_on_500(self, twdb_module, mock_env):
        respx.get(EPA_SDWIS_URL).mock(return_value=Response(500, text="Server Error"))

        with pytest.raises(Exception):
            twdb_module.fetch_epa_sdwis(ti=FakeTI(), ds="2026-04-01")

    @respx.mock
    def test_empty_response_returns_zero(self, twdb_module, mock_env):
        respx.get(EPA_SDWIS_URL).mock(return_value=Response(200, json=[]))

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            result = twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        assert result == 0


# ---------------------------------------------------------------------------
# Both endpoints mocked simultaneously
# ---------------------------------------------------------------------------

class TestBothEndpointsTogether:
    @respx.mock
    def test_independent_fetch_tasks_do_not_interfere(self, twdb_module, mock_env):
        """Fetch tasks should each push independent xcom keys."""
        workbook_rows = _make_project_rows(2)
        workbook_bytes = _make_workbook_bytes(workbook_rows)
        sdwis_records = _make_sdwis_records(2)

        respx.get(TWDB_WORKBOOK_URL).mock(
            return_value=Response(200, content=workbook_bytes)
        )
        respx.get(EPA_SDWIS_URL).mock(
            return_value=Response(200, json=sdwis_records)
        )

        with patch("azure.storage.blob.BlobServiceClient.from_connection_string") as mock_blob:
            mock_blob.return_value.get_container_client.return_value.create_container = MagicMock()
            mock_blob.return_value.get_container_client.return_value.get_blob_client.return_value.upload_blob = MagicMock()

            ti = FakeTI()
            twdb_module.fetch_twdb_workbook(ti=ti, ds="2026-04-01")
            twdb_module.fetch_epa_sdwis(ti=ti, ds="2026-04-01")

        projects = ti.xcom_pull("twdb_projects")
        sdwis = ti.xcom_pull("sdwis_records")

        assert len(projects) == 2
        assert len(sdwis) == 2
        # Keys must not overlap
        assert projects[0].get("PWSID") is None
        assert sdwis[0].get("project_name") is None


# ---------------------------------------------------------------------------
# Resolve-column helper
# ---------------------------------------------------------------------------

class TestResolveCol:
    def test_resolves_exact_match(self, twdb_module):
        cols = ["Project Name", "County", "Region"]
        result = twdb_module._resolve_col(cols, ["Project Name"])
        assert result == "Project Name"

    def test_resolves_case_insensitive(self, twdb_module):
        cols = ["project name", "County"]
        result = twdb_module._resolve_col(cols, ["Project Name"])
        assert result == "project name"

    def test_returns_first_candidate_match(self, twdb_module):
        cols = ["WMS Project Name", "Strategy Name"]
        result = twdb_module._resolve_col(cols, ["Project Name", "Strategy Name", "WMS Project Name"])
        assert result == "Strategy Name"

    def test_returns_none_when_no_match(self, twdb_module):
        cols = ["Foo", "Bar"]
        result = twdb_module._resolve_col(cols, ["Project Name", "WMS Project Name"])
        assert result is None
