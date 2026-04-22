import logging
import os
from datetime import datetime, timezone
from io import BytesIO

from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CENSUS_POP_BASE = "https://api.census.gov/data/2023/pep/population"
CENSUS_PERMITS_BASE = "https://api.census.gov/data/timeseries/eits/bps"
RAW_CONTAINER = "raw-data"

# High-growth states: TX=48, FL=12, AZ=04, CO=08, NC=37, GA=13, TN=47, NV=32
HIGH_GROWTH_STATES = ["48", "12", "04", "08", "37", "13", "47", "32"]

STATE_FIPS_TO_NAME = {
    "48": "Texas", "12": "Florida", "04": "Arizona", "08": "Colorado",
    "37": "North Carolina", "13": "Georgia", "47": "Tennessee", "32": "Nevada",
}


def _demand_indicator(growth_pct: float) -> str:
    if growth_pct > 5.0:
        return "high"
    if growth_pct >= 2.0:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="census_market_intelligence_refresh",
    schedule="0 7 1 * *",  # monthly, 1st of month 07:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["ingestion", "business_development", "census"],
    doc_md="""
    ## Census Market Intelligence Refresh DAG

    Indexes Census Bureau population growth and housing permit data by county
    to support market sizing queries.

    Data sources (all free, no auth):
    - Census Population Estimates API (2023 vintage) for high-growth states
    - Census Building Permits Survey (timeseries BPS)

    Constructs county-level market intelligence narratives, embeds with
    text-embedding-3-small, and upserts into Azure AI Search under
    domain='business_development', document_type='market_intelligence'.

    **Schedule:** Monthly — 1st of month 07:00 UTC
    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Task 1 — fetch_population_data
    # -----------------------------------------------------------------------
    def fetch_population_data(**context):
        """Fetch county-level population estimates for high-growth states."""
        import requests

        all_counties = []

        for state_fips in HIGH_GROWTH_STATES:
            params = {
                "get": "NAME,POP_2020,POP_2021,POP_2022,POP_2023",
                "for": "county:*",
                "in": f"state:{state_fips}",
            }
            try:
                resp = requests.get(CENSUS_POP_BASE, params=params, timeout=60)
                resp.raise_for_status()
                rows = resp.json()
            except Exception as exc:
                log.warning(
                    "Census population fetch failed for state %s: %s", state_fips, exc)
                continue

            if not rows or len(rows) < 2:
                log.warning(
                    "No population data returned for state %s", state_fips)
                continue

            headers = rows[0]
            for row in rows[1:]:
                record = dict(zip(headers, row))
                record["_state_fips"] = state_fips
                all_counties.append(record)

            log.info("Fetched %d counties for state %s",
                     len(rows) - 1, state_fips)

        log.info("Total county population records fetched: %d", len(all_counties))
        context["ti"].xcom_push(key="population_data", value=all_counties)
        return len(all_counties)

    # -----------------------------------------------------------------------
    # Task 2 — fetch_permit_data
    # -----------------------------------------------------------------------
    def fetch_permit_data(**context):
        """Fetch county-level building permit data for high-growth states."""
        import requests

        all_permits = []

        for state_fips in HIGH_GROWTH_STATES:
            params = {
                "get": "cell_value,time_slot_id,category_code",
                "for": "county:*",
                "in": f"state:{state_fips}",
            }
            try:
                resp = requests.get(CENSUS_PERMITS_BASE,
                                    params=params, timeout=60)
                resp.raise_for_status()
                rows = resp.json()
            except Exception as exc:
                log.warning(
                    "Census permits fetch failed for state %s: %s", state_fips, exc)
                continue

            if not rows or len(rows) < 2:
                log.warning("No permit data returned for state %s", state_fips)
                continue

            headers = rows[0]
            for row in rows[1:]:
                record = dict(zip(headers, row))
                record["_state_fips"] = state_fips
                all_permits.append(record)

            log.info("Fetched %d permit records for state %s",
                     len(rows) - 1, state_fips)

        log.info("Total building permit records fetched: %d", len(all_permits))
        context["ti"].xcom_push(key="permit_data", value=all_permits)
        return len(all_permits)

    # -----------------------------------------------------------------------
    # Task 3 — store_raw_parquet
    # -----------------------------------------------------------------------
    def store_raw_parquet(**context):
        """Persist raw census records as Parquet in Azure Blob Storage."""
        import pandas as pd
        from azure.storage.blob import BlobServiceClient
        from _dd_blob import dd_upload_blob

        population_data = context["ti"].xcom_pull(
            key="population_data", task_ids="fetch_population_data")
        permit_data = context["ti"].xcom_pull(
            key="permit_data", task_ids="fetch_permit_data")

        run_date = context["ds"]
        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_client = BlobServiceClient.from_connection_string(conn_str)
        container = blob_client.get_container_client(RAW_CONTAINER)

        try:
            container.create_container()
        except Exception:
            pass

        for dataset_name, records in [("population", population_data), ("permits", permit_data)]:
            if not records:
                continue
            df = pd.DataFrame(records)
            blob_path = f"census/{dataset_name}/census_{dataset_name}_{run_date.replace('-', '')}.parquet"
            buf = BytesIO()
            df.to_parquet(buf, index=False)
            buf.seek(0)
            dd_upload_blob(container, blob_path, buf,
                           dag_id="census_market_intelligence_refresh")
            log.info("Stored %s Parquet at: %s/%s",
                     dataset_name, RAW_CONTAINER, blob_path)

    # -----------------------------------------------------------------------
    # Task 4 — index_to_search
    # -----------------------------------------------------------------------
    def index_to_search(**context):
        """Build market intelligence narratives per county and upsert into Azure AI Search."""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from openai import AzureOpenAI

        population_data = context["ti"].xcom_pull(
            key="population_data", task_ids="fetch_population_data")
        permit_data = context["ti"].xcom_pull(
            key="permit_data", task_ids="fetch_permit_data")

        if not population_data:
            log.warning("No population data to index.")
            return

        search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        search_api_key = os.environ["AZURE_SEARCH_API_KEY"]
        index_name = os.environ["AZURE_SEARCH_INDEX_NAME"]
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]
        embedding_deployment = os.environ.get(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

        oai_client = AzureOpenAI(
            azure_endpoint=openai_endpoint,
            api_key=openai_api_key,
            api_version="2024-02-01",
        )

        search_client = SearchClient(
            endpoint=search_endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(search_api_key),
        )

        # Build a permit lookup: {(state_fips, county_fips): total_permits}
        permit_lookup: dict[tuple[str, str], int] = {}
        for rec in (permit_data or []):
            state = rec.get("_state_fips", "")
            county = rec.get("county", "")
            try:
                count = int(rec.get("cell_value") or 0)
            except (TypeError, ValueError):
                count = 0
            key = (state, county)
            permit_lookup[key] = permit_lookup.get(key, 0) + count

        now_iso = datetime.now(timezone.utc).isoformat()
        docs_to_upsert = []

        for rec in population_data:
            county_name = rec.get("NAME", "Unknown County")
            state_fips = rec.get("_state_fips", "")
            county_fips = rec.get("county", "")
            state_name = STATE_FIPS_TO_NAME.get(
                state_fips, f"State {state_fips}")

            try:
                pop_2020 = int(rec.get("POP_2020") or 0)
                pop_2023 = int(rec.get("POP_2023") or 0)
            except (TypeError, ValueError):
                pop_2020 = 0
                pop_2023 = 0

            if pop_2020 > 0:
                growth_pct = ((pop_2023 - pop_2020) / pop_2020) * 100
            else:
                growth_pct = 0.0

            total_permits = permit_lookup.get((state_fips, county_fips), 0)
            demand = _demand_indicator(growth_pct)

            narrative = (
                f"Market intelligence: {county_name}, {state_name}. "
                f"Population 2023: {pop_2023:,} (growth since 2020: {growth_pct:.1f}%). "
                f"Building permits issued: {total_permits:,} (latest available year). "
                f"Infrastructure demand indicator: {demand} based on growth rate."
            )

            safe_county = county_name.replace(
                " ", "_").replace(",", "").replace("/", "-")
            doc_id = f"census_{state_fips}_{county_fips}_{safe_county}"

            try:
                embedding_resp = oai_client.embeddings.create(
                    model=embedding_deployment,
                    input=narrative,
                )
                vector = embedding_resp.data[0].embedding
            except Exception as exc:
                log.warning("Embedding failed for %s: %s", county_name, exc)
                continue

            docs_to_upsert.append({
                "id": doc_id,
                "content": narrative,
                "content_vector": vector,
                "source": "US Census Bureau",
                "document_type": "market_intelligence",
                "domain": "business_development",
                "last_updated": now_iso,
                "chunk_index": 0,
                "source_url": CENSUS_POP_BASE,
            })

            if len(docs_to_upsert) >= 100:
                search_client.upsert_documents(documents=docs_to_upsert)
                log.info("Upserted batch of %d market intelligence documents", len(
                    docs_to_upsert))
                docs_to_upsert = []

        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d market intelligence documents", len(
                docs_to_upsert))

        log.info("Census market intelligence indexing complete for %d counties.", len(
            population_data))

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1 = PythonOperator(
        task_id="fetch_population_data",
        python_callable=fetch_population_data,
    )

    t2 = PythonOperator(
        task_id="fetch_permit_data",
        python_callable=fetch_permit_data,
    )

    t3 = PythonOperator(
        task_id="store_raw_parquet",
        python_callable=store_raw_parquet,
    )

    t4 = PythonOperator(
        task_id="index_to_search",
        python_callable=index_to_search,
    )

    [t1, t2] >> t3 >> t4
