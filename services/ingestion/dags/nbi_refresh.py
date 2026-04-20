import ddtrace.auto  # must be first import — enables DJM + APM auto-instrumentation
# DD_DATA_JOBS_ENABLED=true must be set on the Airflow scheduler pod environment

import json
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
NBI_ARCGIS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services"
    "/National_Bridge_Inventory/FeatureServer/0/query"
)
NBI_FIELDS = ",".join([
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
])
STATE_CODE_TX = "48"
PAGE_SIZE = 2000
RAW_CONTAINER = "raw-data"
CHUNK_SIZE = 500  # characters per text chunk

CONDITION_LABELS = {
    "9": "excellent", "8": "very good", "7": "good",
    "6": "satisfactory", "5": "fair", "4": "poor",
    "3": "serious", "2": "critical", "1": "imminent failure", "0": "failed",
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="nbi_refresh",
    schedule_interval="0 3 * * 0",  # weekly Sunday 03:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["ingestion", "transportation", "nbi"],
    doc_md="""
    ## NBI Refresh DAG

    Pulls Texas (state code 48) bridge inventory data from the FHWA BTS ArcGIS
    feature server, stores raw records as Parquet in Azure Blob Storage
    (`infra-advisor-raw`), then chunks and indexes each bridge record into
    Azure AI Search under domain='transportation'.

    **Schedule:** Weekly — Sunday 03:00 UTC
    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Task 1 — fetch_nbi_data
    # -----------------------------------------------------------------------
    def fetch_nbi_data(**context):
        """Paginate the BTS ArcGIS feature server and pull all TX NBI records."""
        all_features = []
        offset = 0

        while True:
            params = {
                "where": (
                    f"STATE_CODE_001='{STATE_CODE_TX}' "
                    "AND SUFFICIENCY_RATING IS NOT NULL"
                ),
                "outFields": NBI_FIELDS,
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "f": "json",
            }

            resp = requests.get(NBI_ARCGIS_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            features = data.get("features", [])
            log.info("Fetched %d records at offset %d", len(features), offset)
            all_features.extend(features)

            if len(features) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        log.info("Total NBI records fetched: %d", len(all_features))

        # Flatten attributes from GeoJSON feature wrapper
        records = [f["attributes"] for f in all_features]
        context["ti"].xcom_push(key="nbi_records", value=records)
        return len(records)

    # -----------------------------------------------------------------------
    # Task 2 — store_raw_parquet
    # -----------------------------------------------------------------------
    def store_raw_parquet(**context):
        """Persist raw NBI records as Parquet in Azure Blob Storage."""
        records = context["ti"].xcom_pull(key="nbi_records", task_ids="fetch_nbi_data")
        if not records:
            raise ValueError("No NBI records received from fetch step.")

        df = pd.DataFrame(records)
        run_date = context["ds"]  # e.g. 2025-04-20
        blob_path = f"nbi/texas/nbi_tx_{run_date.replace('-', '')}.parquet"

        parquet_buf = BytesIO()
        df.to_parquet(parquet_buf, index=False)
        parquet_buf.seek(0)

        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_client = BlobServiceClient.from_connection_string(conn_str)
        container = blob_client.get_container_client(RAW_CONTAINER)

        # Create container if it doesn't exist
        try:
            container.create_container()
        except Exception:
            pass  # already exists

        blob = container.get_blob_client(blob_path)
        blob.upload_blob(parquet_buf, overwrite=True)
        log.info("Stored raw Parquet at: %s/%s", RAW_CONTAINER, blob_path)
        context["ti"].xcom_push(key="parquet_blob_path", value=blob_path)

    # -----------------------------------------------------------------------
    # Task 3 — index_to_search
    # -----------------------------------------------------------------------
    def index_to_search(**context):
        """Chunk each bridge record into 500-char text chunks and upsert into Azure AI Search."""
        records = context["ti"].xcom_pull(key="nbi_records", task_ids="fetch_nbi_data")
        if not records:
            raise ValueError("No NBI records to index.")

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
            struct_num = rec.get("STRUCTURE_NUMBER_008", "UNKNOWN").strip()
            facility = rec.get("FACILITY_CARRIED_007", "")
            location = rec.get("LOCATION_009", "")
            county = rec.get("COUNTY_CODE_003", "")
            adt = rec.get("ADT_029", "")
            deck = CONDITION_LABELS.get(str(rec.get("DECK_COND_058", "")), "unknown")
            superstr = CONDITION_LABELS.get(str(rec.get("SUPERSTRUCTURE_COND_059", "")), "unknown")
            substr = CONDITION_LABELS.get(str(rec.get("SUBSTRUCTURE_COND_060", "")), "unknown")
            sd_flag = "Yes" if str(rec.get("STRUCTURALLY_DEFICIENT", "")) == "1" else "No"
            suf_rating = rec.get("SUFFICIENCY_RATING", "N/A")
            inspect_date = rec.get("INSPECT_DATE_090", "")
            year_built = rec.get("YEAR_BUILT_027", "")
            lat = rec.get("LAT_016", "")
            lon = rec.get("LONG_017", "")

            narrative = (
                f"Bridge Structure {struct_num} — Texas (State Code 48). "
                f"Facility carried: {facility}. Location: {location}. "
                f"County code: {county}. Average daily traffic: {adt}. "
                f"Deck condition: {deck}. Superstructure condition: {superstr}. "
                f"Substructure condition: {substr}. Structurally deficient: {sd_flag}. "
                f"Sufficiency rating: {suf_rating}. Last inspection: {inspect_date}. "
                f"Year built: {year_built}. Coordinates: {lat}, {lon}."
            )

            # Chunk into 500-character segments
            chunks = [narrative[i:i + CHUNK_SIZE] for i in range(0, len(narrative), CHUNK_SIZE)]

            for chunk_idx, chunk_text in enumerate(chunks):
                safe_struct = struct_num.replace(" ", "_").replace("/", "-")
                doc_id = f"nbi_{safe_struct}_{chunk_idx}"

                # Embed via Azure OpenAI
                embedding_resp = oai_client.embeddings.create(
                    model="text-embedding-ada-002",
                    input=chunk_text,
                )
                vector = embedding_resp.data[0].embedding

                docs_to_upsert.append({
                    "id": doc_id,
                    "content": chunk_text,
                    "content_vector": vector,
                    "source": "FHWA_NBI",
                    "document_type": "asset_record",
                    "domain": "transportation",
                    "last_updated": now_iso,
                    "chunk_index": chunk_idx,
                    "source_url": NBI_ARCGIS_URL,
                })

                # Batch upsert every 100 docs
                if len(docs_to_upsert) >= 100:
                    search_client.upsert_documents(documents=docs_to_upsert)
                    log.info("Upserted batch of %d documents", len(docs_to_upsert))
                    docs_to_upsert = []

        # Flush remaining
        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d documents", len(docs_to_upsert))

        log.info("NBI indexing complete for %d bridge records.", len(records))

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1 = PythonOperator(
        task_id="fetch_nbi_data",
        python_callable=fetch_nbi_data,
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
