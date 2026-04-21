import ddtrace.auto  # must be first import — enables DJM + APM auto-instrumentation
# DD_DATA_JOBS_ENABLED=true must be set on the Airflow scheduler pod environment

import logging
import os
from datetime import datetime, timezone, timedelta
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
USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
RAW_CONTAINER = "raw-data"
MIN_AWARD_USD = 500_000
PAGE_SIZE = 100

# Primary NAICS groups for infrastructure
_NAICS_PREFIXES = ["2371", "2373", "2372", "2379"]

AWARD_TYPE_LABELS = {
    "A": "BPA Call",
    "B": "Purchase Order",
    "C": "Delivery Order",
    "D": "Definitive Contract",
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="samgov_awards_refresh",
    schedule_interval="0 6 * * 0",  # weekly Sunday 06:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["ingestion", "business_development", "usaspending"],
    doc_md="""
    ## SAM.gov Awards Refresh DAG

    Queries USASpending.gov for federal contract awards in the past 12 months
    across primary infrastructure NAICS groups (water/sewer 2371xx,
    highway/bridge 2373xx, power/communication 2372xx, other heavy 2379xx).

    Filters to awards >= $500,000. Constructs narrative text for each award,
    embeds with text-embedding-3-small, and upserts into Azure AI Search under
    domain='business_development', document_type='contract_award'.

    Also stores raw results as Parquet in Azure Blob Storage (raw-data/awards/).

    **Schedule:** Weekly — Sunday 06:00 UTC
    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Task 1 — fetch_usaspending_awards
    # -----------------------------------------------------------------------
    def fetch_usaspending_awards(**context):
        """Fetch recent infrastructure contract awards from USASpending.gov."""
        today = datetime.now(timezone.utc).date()
        date_from = (today - timedelta(days=365)).isoformat()
        date_to = today.isoformat()

        all_awards = []

        for naics_prefix in _NAICS_PREFIXES:
            page = 1
            while True:
                body = {
                    "filters": {
                        "time_period": [{"start_date": date_from, "end_date": date_to}],
                        "award_type_codes": ["A", "B", "C", "D"],
                        "naics_codes": [naics_prefix],
                    },
                    "fields": [
                        "Award ID", "Recipient Name", "Award Amount", "Total Outlays",
                        "Description", "Start Date", "End Date",
                        "Awarding Agency", "Awarding Sub Agency", "Contract Award Type",
                        "Place of Performance State Code", "Place of Performance City Name",
                        "naics_code", "naics_description",
                    ],
                    "page": page,
                    "limit": PAGE_SIZE,
                    "sort": "Award Amount",
                    "order": "desc",
                }

                try:
                    resp = requests.post(USASPENDING_URL, json=body, timeout=60)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    log.warning("USASpending fetch failed for NAICS prefix %s page %d: %s", naics_prefix, page, exc)
                    break

                results = data.get("results", [])
                log.info("NAICS %s page %d: %d records", naics_prefix, page, len(results))
                all_awards.extend(results)

                if len(results) < PAGE_SIZE:
                    break
                page += 1

        # Filter to >= MIN_AWARD_USD
        filtered = []
        for award in all_awards:
            amount = award.get("Award Amount") or award.get("Total Outlays") or 0
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                amount = 0.0
            if amount >= MIN_AWARD_USD:
                award["_amount_float"] = amount
                filtered.append(award)

        log.info("Total awards after filtering (>= $%d): %d", MIN_AWARD_USD, len(filtered))
        context["ti"].xcom_push(key="awards", value=filtered)
        return len(filtered)

    # -----------------------------------------------------------------------
    # Task 2 — store_raw_parquet
    # -----------------------------------------------------------------------
    def store_raw_parquet(**context):
        """Persist raw award records as Parquet in Azure Blob Storage."""
        awards = context["ti"].xcom_pull(key="awards", task_ids="fetch_usaspending_awards")
        if not awards:
            log.warning("No award records to store.")
            return

        df = pd.DataFrame(awards)
        run_date = context["ds"]
        blob_path = f"awards/usaspending_awards_{run_date.replace('-', '')}.parquet"

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

        dd_upload_blob(container, blob_path, parquet_buf, dag_id="samgov_awards_refresh")
        log.info("Stored raw Parquet at: %s/%s", RAW_CONTAINER, blob_path)

    # -----------------------------------------------------------------------
    # Task 3 — index_to_search
    # -----------------------------------------------------------------------
    def index_to_search(**context):
        """Build narrative text for each award, embed, and upsert into Azure AI Search."""
        awards = context["ti"].xcom_pull(key="awards", task_ids="fetch_usaspending_awards")
        if not awards:
            log.warning("No award records to index.")
            return

        search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        search_api_key = os.environ["AZURE_SEARCH_API_KEY"]
        index_name = os.environ["AZURE_SEARCH_INDEX_NAME"]
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]
        embedding_deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

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
        MAX_TOKENS = 512
        docs_to_upsert = []

        for award in awards:
            recipient = award.get("Recipient Name") or "Unknown Recipient"
            amount = award.get("_amount_float") or award.get("Award Amount") or 0
            agency = award.get("Awarding Agency") or award.get("Awarding Sub Agency") or "Unknown Agency"
            description = award.get("Description") or "No description available"
            city = award.get("Place of Performance City Name") or ""
            state = award.get("Place of Performance State Code") or ""
            location = f"{city}, {state}".strip(", ") if (city or state) else "location not specified"
            start_date = award.get("Start Date") or "unknown"
            end_date = award.get("End Date") or "unknown"
            naics_desc = award.get("naics_description") or award.get("naics_code") or "infrastructure"
            award_id = award.get("Award ID") or "unknown"
            contract_type_code = award.get("Contract Award Type") or ""
            contract_type = AWARD_TYPE_LABELS.get(contract_type_code, contract_type_code)

            narrative = (
                f"Federal contract award: {recipient} was awarded ${amount:,.0f} "
                f"by {agency} for {description} in {location}. "
                f"Contract period: {start_date} to {end_date}. "
                f"NAICS: {naics_desc}. Award ID: {award_id}."
                f"{f' Contract type: {contract_type}.' if contract_type else ''}"
            )

            # Chunk if over 512 tokens
            tokens = enc.encode(narrative)
            if len(tokens) <= MAX_TOKENS:
                chunks = [narrative]
            else:
                # Split into 512-token chunks with no overlap for simplicity
                chunks = []
                for i in range(0, len(tokens), MAX_TOKENS):
                    chunk_tokens = tokens[i:i + MAX_TOKENS]
                    chunks.append(enc.decode(chunk_tokens))

            safe_id = award_id.replace("/", "-").replace(" ", "_").replace(":", "-")

            for chunk_idx, chunk_text in enumerate(chunks):
                doc_id = f"award_{safe_id}_{chunk_idx}"

                embedding_resp = oai_client.embeddings.create(
                    model=embedding_deployment,
                    input=chunk_text,
                )
                vector = embedding_resp.data[0].embedding

                docs_to_upsert.append({
                    "id": doc_id,
                    "content": chunk_text,
                    "content_vector": vector,
                    "source": "USASpending.gov",
                    "document_type": "contract_award",
                    "domain": "business_development",
                    "last_updated": now_iso,
                    "chunk_index": chunk_idx,
                    "source_url": f"https://www.usaspending.gov/award/{award_id}",
                })

                if len(docs_to_upsert) >= 100:
                    search_client.upsert_documents(documents=docs_to_upsert)
                    log.info("Upserted batch of %d award documents", len(docs_to_upsert))
                    docs_to_upsert = []

        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d award documents", len(docs_to_upsert))

        log.info("Award indexing complete for %d award records.", len(awards))

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1 = PythonOperator(
        task_id="fetch_usaspending_awards",
        python_callable=fetch_usaspending_awards,
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
