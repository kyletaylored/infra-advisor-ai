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
from _dd_blob import dd_upload_blob
from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FEMA_API_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
FILTER_DATE_FROM = "2010-01-01T00:00:00.000Z"
PAGE_SIZE = 1000
RAW_CONTAINER = "raw-data"

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="fema_refresh",
    schedule_interval="0 2 * * *",  # daily 02:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["ingestion", "fema", "environmental"],
    doc_md="""
    ## FEMA Refresh DAG

    Pulls FEMA DisasterDeclarationsSummaries for all declarations since 2010,
    stores raw records as Parquet in Azure Blob Storage (`infra-advisor-raw`),
    then chunks and indexes each record into Azure AI Search under
    domain='environmental'.

    **Schedule:** Daily — 02:00 UTC
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Task 1 — fetch_fema_data
    # -----------------------------------------------------------------------
    def fetch_fema_data(**context):
        """Paginate the OpenFEMA API and pull all disaster declarations since 2010."""
        all_records = []
        skip = 0

        while True:
            params = {
                "$filter": f"declarationDate ge '{FILTER_DATE_FROM}'",
                "$format": "json",
                "$top": PAGE_SIZE,
                "$skip": skip,
                "$orderby": "declarationDate asc",
            }

            resp = requests.get(FEMA_API_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("DisasterDeclarationsSummaries", [])
            log.info("Fetched %d FEMA records at skip=%d", len(records), skip)
            all_records.extend(records)

            if len(records) < PAGE_SIZE:
                break
            skip += PAGE_SIZE

        log.info("Total FEMA disaster declarations fetched: %d", len(all_records))
        context["ti"].xcom_push(key="fema_records", value=all_records)
        return len(all_records)

    # -----------------------------------------------------------------------
    # Task 2 — store_raw_parquet
    # -----------------------------------------------------------------------
    def store_raw_parquet(**context):
        """Persist raw FEMA records as Parquet in Azure Blob Storage."""
        records = context["ti"].xcom_pull(key="fema_records", task_ids="fetch_fema_data")
        if not records:
            raise ValueError("No FEMA records received from fetch step.")

        df = pd.DataFrame(records)
        run_date = context["ds"]
        blob_path = f"fema/fema_declarations_{run_date.replace('-', '')}.parquet"

        parquet_buf = BytesIO()
        df.to_parquet(parquet_buf, index=False)
        parquet_buf.seek(0)

        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_client = BlobServiceClient.from_connection_string(conn_str)
        container = blob_client.get_container_client(RAW_CONTAINER)

        try:
            container.create_container()
        except Exception:
            pass  # already exists

        dd_upload_blob(container, blob_path, parquet_buf, dag_id="fema_refresh")
        log.info("Stored raw FEMA Parquet at: %s/%s", RAW_CONTAINER, blob_path)
        context["ti"].xcom_push(key="parquet_blob_path", value=blob_path)

    # -----------------------------------------------------------------------
    # Task 3 — index_to_search
    # -----------------------------------------------------------------------
    def index_to_search(**context):
        """Chunk each FEMA declaration and upsert into Azure AI Search."""
        records = context["ti"].xcom_pull(key="fema_records", task_ids="fetch_fema_data")
        if not records:
            raise ValueError("No FEMA records to index.")

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

        for rec in records:
            disaster_number = rec.get("disasterNumber", "UNKNOWN")
            declaration_type = rec.get("declarationType", "")
            incident_type = rec.get("incidentType", "")
            title = rec.get("declarationTitle", "")
            state = rec.get("stateCode", "")
            county = rec.get("designatedArea", "")
            declaration_date = rec.get("declarationDate", "")
            incident_begin = rec.get("incidentBeginDate", "")
            incident_end = rec.get("incidentEndDate", "")
            close_date = rec.get("disasterCloseoutDate", "")
            pa_declared = rec.get("paDeclarationString", "")
            hm_declared = rec.get("hmDeclarationString", "")
            fips = rec.get("fipsStateCode", "") + rec.get("fipsCountyCode", "")

            narrative = (
                f"FEMA Disaster {disaster_number} — {title}. "
                f"State: {state}. Designated area: {county} (FIPS: {fips}). "
                f"Declaration type: {declaration_type}. Incident type: {incident_type}. "
                f"Declaration date: {declaration_date}. "
                f"Incident period: {incident_begin} to {incident_end}. "
                f"Closeout date: {close_date}. "
                f"Public Assistance declared: {pa_declared}. "
                f"Hazard Mitigation declared: {hm_declared}."
            )

            # Chunk into 512-token windows (use tiktoken for accuracy)
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
                doc_id = f"fema_{disaster_number}_{chunk_idx}"

                embedding_resp = oai_client.embeddings.create(
                    model="text-embedding-ada-002",
                    input=chunk_text,
                )
                vector = embedding_resp.data[0].embedding

                docs_to_upsert.append({
                    "id": doc_id,
                    "content": chunk_text,
                    "content_vector": vector,
                    "source": "OpenFEMA",
                    "document_type": "disaster_declaration",
                    "domain": "environmental",
                    "last_updated": now_iso,
                    "chunk_index": chunk_idx,
                    "source_url": FEMA_API_URL,
                })

                if len(docs_to_upsert) >= 100:
                    search_client.upsert_documents(documents=docs_to_upsert)
                    log.info("Upserted batch of %d FEMA documents", len(docs_to_upsert))
                    docs_to_upsert = []

        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d FEMA documents", len(docs_to_upsert))

        log.info("FEMA indexing complete for %d disaster records.", len(records))

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1 = PythonOperator(
        task_id="fetch_fema_data",
        python_callable=fetch_fema_data,
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
