import ddtrace.auto  # must be first import — enables DJM + APM auto-instrumentation

import logging
import os
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd
import requests
import tiktoken
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient
from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EIA_API_URL = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/"
SOUTHEASTERN_STATES = ["FL", "GA", "AL", "MS", "LA", "TX", "AR", "TN", "SC", "NC", "VA"]
RAW_CONTAINER = "raw-data"
PAGE_SIZE = 5000

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="eia_refresh",
    schedule_interval="0 4 * * 0",  # weekly Sunday 04:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["ingestion", "energy", "eia"],
    doc_md="""
    ## EIA Refresh DAG

    Pulls state-level electricity generation capacity data from the EIA Open Data
    API for southeastern states (FL, GA, AL, MS, LA, TX, AR, TN, SC, NC, VA),
    stores raw records as Parquet in Azure Blob Storage (`infra-advisor-raw`),
    and indexes them into Azure AI Search under domain='energy'.

    **Schedule:** Weekly — Sunday 04:00 UTC
    **Requires:** EIA_API_KEY environment variable (free key from eia.gov).
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Task 1 — fetch_eia_data
    # -----------------------------------------------------------------------
    def fetch_eia_data(**context):
        """Pull EIA electric power operational data for southeastern states."""
        eia_api_key = os.environ["EIA_API_KEY"]
        all_records = []

        # Fetch data for each state to keep requests manageable
        for state in SOUTHEASTERN_STATES:
            offset = 0
            while True:
                params = {
                    "api_key": eia_api_key,
                    "frequency": "annual",
                    "data[]": ["generation", "capacity"],
                    "facets[location][]": state,
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                    "offset": offset,
                    "length": PAGE_SIZE,
                }

                resp = requests.get(EIA_API_URL, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()

                page_records = data.get("response", {}).get("data", [])
                log.info("EIA: fetched %d records for state=%s offset=%d", len(page_records), state, offset)

                for rec in page_records:
                    rec["state_code"] = state
                all_records.extend(page_records)

                total = data.get("response", {}).get("total", 0)
                offset += PAGE_SIZE
                if offset >= total or len(page_records) == 0:
                    break

        log.info("Total EIA records fetched: %d across %d states", len(all_records), len(SOUTHEASTERN_STATES))
        context["ti"].xcom_push(key="eia_records", value=all_records)
        return len(all_records)

    # -----------------------------------------------------------------------
    # Task 2 — store_raw_parquet
    # -----------------------------------------------------------------------
    def store_raw_parquet(**context):
        """Persist raw EIA records as Parquet in Azure Blob Storage."""
        records = context["ti"].xcom_pull(key="eia_records", task_ids="fetch_eia_data")
        if not records:
            raise ValueError("No EIA records received from fetch step.")

        df = pd.DataFrame(records)
        run_date = context["ds"]
        blob_path = f"eia/eia_southeast_{run_date.replace('-', '')}.parquet"

        parquet_buf = BytesIO()
        df.to_parquet(parquet_buf, index=False)
        parquet_buf.seek(0)

        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_client = BlobServiceClient.from_connection_string(conn_str)
        container = blob_client.get_container_client(RAW_CONTAINER)

        try:
            container.create_container()
        except Exception:
            pass

        blob = container.get_blob_client(blob_path)
        blob.upload_blob(parquet_buf, overwrite=True)
        log.info("Stored raw EIA Parquet at: %s/%s", RAW_CONTAINER, blob_path)
        context["ti"].xcom_push(key="parquet_blob_path", value=blob_path)

    # -----------------------------------------------------------------------
    # Task 3 — index_to_search
    # -----------------------------------------------------------------------
    def index_to_search(**context):
        """Chunk each EIA record into text and upsert into Azure AI Search."""
        records = context["ti"].xcom_pull(key="eia_records", task_ids="fetch_eia_data")
        if not records:
            raise ValueError("No EIA records to index.")

        search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        search_api_key = os.environ["AZURE_SEARCH_API_KEY"]
        index_name = os.environ["AZURE_SEARCH_INDEX_NAME"]
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]

        from openai import AzureOpenAI

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

        enc = tiktoken.get_encoding("cl100k_base")
        now_iso = datetime.now(timezone.utc).isoformat()
        docs_to_upsert = []

        # Group records by state and period to form meaningful narratives
        for idx, rec in enumerate(records):
            period = rec.get("period", "")
            state = rec.get("state_code", rec.get("location", ""))
            sector = rec.get("sectorDescription", rec.get("sector-name", ""))
            fuel_type = rec.get("fuelTypeDescription", rec.get("fueltypeid", ""))
            generation = rec.get("generation", "")
            generation_units = rec.get("generation-units", "thousand megawatthours")
            capacity = rec.get("capacity", "")
            capacity_units = rec.get("capacity-units", "gigawatts")

            narrative = (
                f"EIA Electric Power Data — State: {state}, Period: {period}. "
                f"Sector: {sector}. Fuel type: {fuel_type}. "
                f"Net generation: {generation} {generation_units}. "
                f"Capacity: {capacity} {capacity_units}. "
                f"Source: EIA Electric Power Operational Data API."
            )

            tokens = enc.encode(narrative)
            chunk_size_tok = 512
            overlap_tok = 64
            token_chunks = []
            start = 0
            while start < len(tokens):
                end = min(start + chunk_size_tok, len(tokens))
                token_chunks.append(enc.decode(tokens[start:end]))
                if end == len(tokens):
                    break
                start += chunk_size_tok - overlap_tok

            for chunk_idx, chunk_text in enumerate(token_chunks):
                doc_id = f"eia_{state}_{period}_{idx}_{chunk_idx}".replace(" ", "_")

                embedding_resp = oai_client.embeddings.create(
                    model="text-embedding-ada-002",
                    input=chunk_text,
                )
                vector = embedding_resp.data[0].embedding

                docs_to_upsert.append({
                    "id": doc_id,
                    "content": chunk_text,
                    "content_vector": vector,
                    "source": "EIA",
                    "document_type": "energy_record",
                    "domain": "energy",
                    "last_updated": now_iso,
                    "chunk_index": chunk_idx,
                    "source_url": EIA_API_URL,
                })

                if len(docs_to_upsert) >= 100:
                    search_client.upsert_documents(documents=docs_to_upsert)
                    log.info("Upserted batch of %d EIA documents", len(docs_to_upsert))
                    docs_to_upsert = []

        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d EIA documents", len(docs_to_upsert))

        log.info("EIA indexing complete for %d records.", len(records))

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1 = PythonOperator(
        task_id="fetch_eia_data",
        python_callable=fetch_eia_data,
    )

    t2 = PythonOperator(
        task_id="store_raw_parquet",
        python_callable=store_raw_parquet,
    )

    t3 = PythonOperator(
        task_id="index_to_search",
        python_callable=index_to_search,
    )

    t1 >> t2 >> t3
