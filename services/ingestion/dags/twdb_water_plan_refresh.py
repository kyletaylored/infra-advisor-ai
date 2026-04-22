import io
import logging
import os
import re
from datetime import datetime, timezone
from io import BytesIO

from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_CONTAINER = "raw-data"

# TWDB planning regions A–P
TWDB_REGIONS = list("ABCDEFGHIJKLMNOP")

# Column name mappings for the TWDB workbook (adapt to actual Excel column headers)
TWDB_COLUMN_MAP = {
    "project_name": ["Project Name", "Strategy Name", "WMS Project Name"],
    "county": ["County", "Counties"],
    "region": ["Region", "Planning Region", "WMS Region"],
    "water_user_group": ["Water User Group", "WUG", "WUG Name"],
    "strategy_type": ["Strategy Type", "Water Management Strategy"],
    "project_sponsor": ["Project Sponsor", "Sponsor", "Entity"],
    "cost_2030": ["2030 Capital Cost", "Cost 2030", "Capital Cost 2030"],
    "cost_2040": ["2040 Capital Cost", "Cost 2040", "Capital Cost 2040"],
    "cost_2050": ["2050 Capital Cost", "Cost 2050", "Capital Cost 2050"],
    "cost_2060": ["2060 Capital Cost", "Cost 2060", "Capital Cost 2060"],
    "cost_2070": ["2070 Capital Cost", "Cost 2070", "Capital Cost 2070"],
    "cost_2080": ["2080 Capital Cost", "Cost 2080", "Capital Cost 2080"],
    "volume": ["Water Supply Volume", "Volume (ac-ft/yr)", "Supply Volume"],
    "supply_type": ["Supply Type", "Source", "Water Source Type"],
    "decade_of_need": ["Decade of Need", "Need Decade"],
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="twdb_water_plan_refresh",
    schedule="0 5 1 * *",  # monthly — 1st of month at 05:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["ingestion", "water", "twdb", "epa"],
    doc_md="""
    ## TWDB Water Plan Refresh DAG

    1. Downloads the TWDB 2026 State Water Plan Excel workbook from
       `TWDB_WATER_PLAN_WORKBOOK_URL` and indexes all ~3,000 project records
       into Azure AI Search as water plan project narratives.

    2. Pulls EPA SDWIS Texas community water system records from
       `EPA_SDWIS_BASE_URL` and indexes them under domain='water'.

    **Schedule:** Monthly — 1st of month, 05:00 UTC
    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Helper — resolve fuzzy column name
    # -----------------------------------------------------------------------
    def _resolve_col(df_cols, candidates):
        """Return the first matching column name from a list of candidates (case-insensitive)."""
        df_cols_lower = {c.lower(): c for c in df_cols}
        for candidate in candidates:
            if candidate.lower() in df_cols_lower:
                return df_cols_lower[candidate.lower()]
        return None

    # -----------------------------------------------------------------------
    # Task 1 — fetch_twdb_workbook
    # -----------------------------------------------------------------------
    def fetch_twdb_workbook(**context):
        """Download and parse the TWDB 2026 State Water Plan Excel workbook."""
        import pandas as pd
        import requests
        from azure.storage.blob import BlobServiceClient
        from _dd_blob import dd_upload_blob

        workbook_url = os.environ["TWDB_WATER_PLAN_WORKBOOK_URL"]

        log.info("Downloading TWDB workbook from: %s", workbook_url)
        resp = requests.get(workbook_url, timeout=120)
        resp.raise_for_status()

        workbook_bytes = resp.content
        log.info("Downloaded workbook: %d bytes", len(workbook_bytes))

        # Store raw workbook in Blob Storage
        run_date = context["ds"]
        blob_path = f"twdb/twdb_water_plan_{run_date.replace('-', '')}.xlsx"
        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_svc = BlobServiceClient.from_connection_string(conn_str)
        container = blob_svc.get_container_client(RAW_CONTAINER)
        try:
            container.create_container()
        except Exception:
            pass
        dd_upload_blob(container, blob_path, BytesIO(workbook_bytes), dag_id="twdb_water_plan_refresh")
        log.info("Stored raw TWDB workbook at: %s/%s", RAW_CONTAINER, blob_path)

        # Parse using openpyxl via pandas
        xls = pd.ExcelFile(BytesIO(workbook_bytes), engine="openpyxl")
        all_projects = []

        for sheet_name in xls.sheet_names:
            try:
                df = pd.read_excel(xls, sheet_name=sheet_name, dtype=str)
                df.columns = [str(c).strip() for c in df.columns]

                # Detect if this sheet has project data by looking for key columns
                name_col = _resolve_col(df.columns, TWDB_COLUMN_MAP["project_name"])
                if name_col is None:
                    log.debug("Sheet '%s' has no project name column — skipping", sheet_name)
                    continue

                log.info("Parsing sheet '%s' with %d rows", sheet_name, len(df))

                for _, row in df.iterrows():
                    project_name = str(row.get(name_col, "")).strip()
                    if not project_name or project_name.lower() in ("nan", "project name", ""):
                        continue

                    def get_val(field):
                        col = _resolve_col(df.columns, TWDB_COLUMN_MAP.get(field, [field]))
                        if col and col in row.index:
                            v = str(row[col]).strip()
                            return v if v.lower() != "nan" else ""
                        return ""

                    project = {
                        "project_name": project_name,
                        "county": get_val("county"),
                        "region": get_val("region"),
                        "water_user_group": get_val("water_user_group"),
                        "strategy_type": get_val("strategy_type"),
                        "project_sponsor": get_val("project_sponsor"),
                        "cost_2030": get_val("cost_2030"),
                        "cost_2040": get_val("cost_2040"),
                        "cost_2050": get_val("cost_2050"),
                        "cost_2060": get_val("cost_2060"),
                        "cost_2070": get_val("cost_2070"),
                        "cost_2080": get_val("cost_2080"),
                        "volume": get_val("volume"),
                        "supply_type": get_val("supply_type"),
                        "decade_of_need": get_val("decade_of_need"),
                        "sheet": sheet_name,
                    }
                    all_projects.append(project)

            except Exception as exc:
                log.warning("Failed to parse sheet '%s': %s", sheet_name, exc)
                continue

        log.info("Parsed %d TWDB water plan project records across %d sheets", len(all_projects), len(xls.sheet_names))
        context["ti"].xcom_push(key="twdb_projects", value=all_projects)
        return len(all_projects)

    # -----------------------------------------------------------------------
    # Task 2 — fetch_epa_sdwis
    # -----------------------------------------------------------------------
    def fetch_epa_sdwis(**context):
        """Pull EPA SDWIS Texas community water system records from Envirofacts."""
        import pandas as pd
        import requests
        from azure.storage.blob import BlobServiceClient
        from _dd_blob import dd_upload_blob

        sdwis_base = os.environ["EPA_SDWIS_BASE_URL"]
        url = f"{sdwis_base}/WATER_SYSTEM/STATE_CODE/TX/PWS_TYPE_CODE/CWS/JSON"

        log.info("Fetching EPA SDWIS Texas CWS records from: %s", url)
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        records = resp.json()

        log.info("Fetched %d EPA SDWIS CWS records", len(records))

        # Store raw as parquet
        run_date = context["ds"]
        blob_path = f"epa_sdwis/sdwis_tx_cws_{run_date.replace('-', '')}.parquet"
        df = pd.DataFrame(records)
        parquet_buf = BytesIO()
        df.to_parquet(parquet_buf, index=False)
        parquet_buf.seek(0)

        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_svc = BlobServiceClient.from_connection_string(conn_str)
        container = blob_svc.get_container_client(RAW_CONTAINER)
        try:
            container.create_container()
        except Exception:
            pass
        dd_upload_blob(container, blob_path, parquet_buf, dag_id="twdb_water_plan_refresh")
        log.info("Stored raw EPA SDWIS Parquet at: %s/%s", RAW_CONTAINER, blob_path)

        context["ti"].xcom_push(key="sdwis_records", value=records)
        return len(records)

    # -----------------------------------------------------------------------
    # Task 3 — index_twdb_projects
    # -----------------------------------------------------------------------
    def index_twdb_projects(**context):
        """Convert TWDB project records to text narratives and upsert into Azure AI Search."""
        import tiktoken
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from openai import AzureOpenAI

        projects = context["ti"].xcom_pull(key="twdb_projects", task_ids="fetch_twdb_workbook")
        if not projects:
            log.warning("No TWDB projects to index — skipping.")
            return

        search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        search_api_key = os.environ["AZURE_SEARCH_API_KEY"]
        index_name = os.environ["AZURE_SEARCH_INDEX_NAME"]
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]

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

        for idx, proj in enumerate(projects):
            region = proj.get("region", "Unknown")
            project_name = proj.get("project_name", "Unnamed Project")
            county = proj.get("county", "Unknown")
            entity = proj.get("project_sponsor", proj.get("water_user_group", "Unknown entity"))
            strategy_type = proj.get("strategy_type", "Unknown")
            volume = proj.get("volume", "N/A")
            supply_type = proj.get("supply_type", "water")
            decade_of_need = proj.get("decade_of_need", "")

            # Find the earliest non-empty cost decade
            cost = ""
            decade_label = decade_of_need
            for dec in ["cost_2030", "cost_2040", "cost_2050", "cost_2060", "cost_2070", "cost_2080"]:
                val = proj.get(dec, "")
                if val and val.lower() not in ("nan", "0", ""):
                    cost = val
                    if not decade_label:
                        decade_label = dec.replace("cost_", "")
                    break

            # PRD-mandated narrative format
            narrative = (
                f"TWDB 2026 Water Plan — Region {region}: {project_name} in {county} County, "
                f"sponsored by {entity}. Strategy type: {strategy_type}. "
                f"Estimated cost: ${cost}M (decade of need: {decade_label}). "
                f"Adds {volume} acre-feet/year of {supply_type} water supply."
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
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", project_name)[:60]
                doc_id = f"twdb_{safe_name}_{idx}_{chunk_idx}"

                embedding_resp = oai_client.embeddings.create(
                    model="text-embedding-ada-002",
                    input=chunk_text,
                )
                vector = embedding_resp.data[0].embedding

                docs_to_upsert.append({
                    "id": doc_id,
                    "content": chunk_text,
                    "content_vector": vector,
                    "source": "TWDB_2026_State_Water_Plan",
                    "document_type": "water_plan_project",
                    "domain": "water",
                    "last_updated": now_iso,
                    "chunk_index": chunk_idx,
                    "source_url": os.environ["TWDB_WATER_PLAN_WORKBOOK_URL"],
                })

                if len(docs_to_upsert) >= 100:
                    search_client.upsert_documents(documents=docs_to_upsert)
                    log.info("Upserted batch of %d TWDB project documents", len(docs_to_upsert))
                    docs_to_upsert = []

        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d TWDB project documents", len(docs_to_upsert))

        log.info("TWDB water plan project indexing complete for %d records.", len(projects))

    # -----------------------------------------------------------------------
    # Task 4 — index_sdwis_records
    # -----------------------------------------------------------------------
    def index_sdwis_records(**context):
        """Convert EPA SDWIS water system records to text chunks and upsert into Azure AI Search."""
        import tiktoken
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from openai import AzureOpenAI

        records = context["ti"].xcom_pull(key="sdwis_records", task_ids="fetch_epa_sdwis")
        if not records:
            log.warning("No EPA SDWIS records to index — skipping.")
            return

        search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        search_api_key = os.environ["AZURE_SEARCH_API_KEY"]
        index_name = os.environ["AZURE_SEARCH_INDEX_NAME"]
        openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]
        sdwis_base = os.environ["EPA_SDWIS_BASE_URL"]

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
        source_url = f"{sdwis_base}/WATER_SYSTEM/STATE_CODE/TX/PWS_TYPE_CODE/CWS/JSON"

        for idx, rec in enumerate(records):
            # Envirofacts returns uppercase field names
            pwsid = rec.get("PWSID", rec.get("pwsid", f"TX{idx:07d}"))
            system_name = rec.get("PWS_NAME", rec.get("pws_name", "Unknown"))
            city = rec.get("CITY_NAME", rec.get("city_name", ""))
            county = rec.get("COUNTY_SERVED", rec.get("county_served", ""))
            population = rec.get("POPULATION_SERVED_COUNT", rec.get("population_served_count", ""))
            primary_source = rec.get("PRIMARY_SOURCE_CODE", rec.get("primary_source_code", ""))
            activity_status = rec.get("PWS_ACTIVITY_CODE", rec.get("pws_activity_code", ""))
            owner_type = rec.get("OWNER_TYPE_CODE", rec.get("owner_type_code", ""))

            narrative = (
                f"EPA SDWIS — Texas Community Water System: {system_name} (PWSID: {pwsid}). "
                f"City: {city}. County: {county}. "
                f"Population served: {population}. Primary water source: {primary_source}. "
                f"System activity status: {activity_status}. Owner type: {owner_type}. "
                f"State: TX. System type: Community Water System (CWS). "
                f"Regulated under the Safe Drinking Water Act (SDWA) and TCEQ."
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
                safe_pwsid = re.sub(r"[^a-zA-Z0-9_-]", "_", str(pwsid))
                doc_id = f"sdwis_{safe_pwsid}_{chunk_idx}"

                embedding_resp = oai_client.embeddings.create(
                    model="text-embedding-ada-002",
                    input=chunk_text,
                )
                vector = embedding_resp.data[0].embedding

                docs_to_upsert.append({
                    "id": doc_id,
                    "content": chunk_text,
                    "content_vector": vector,
                    "source": "EPA_SDWIS",
                    "document_type": "water_system_record",
                    "domain": "water",
                    "last_updated": now_iso,
                    "chunk_index": chunk_idx,
                    "source_url": source_url,
                })

                if len(docs_to_upsert) >= 100:
                    search_client.upsert_documents(documents=docs_to_upsert)
                    log.info("Upserted batch of %d SDWIS documents", len(docs_to_upsert))
                    docs_to_upsert = []

        if docs_to_upsert:
            search_client.upsert_documents(documents=docs_to_upsert)
            log.info("Upserted final batch of %d SDWIS documents", len(docs_to_upsert))

        log.info("EPA SDWIS indexing complete for %d water system records.", len(records))

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1_twdb = PythonOperator(
        task_id="fetch_twdb_workbook",
        python_callable=fetch_twdb_workbook,
    )

    t2_sdwis = PythonOperator(
        task_id="fetch_epa_sdwis",
        python_callable=fetch_epa_sdwis,
    )

    t3_twdb_idx = PythonOperator(
        task_id="index_twdb_projects",
        python_callable=index_twdb_projects,
    )

    t4_sdwis_idx = PythonOperator(
        task_id="index_sdwis_records",
        python_callable=index_sdwis_records,
    )

    # Fetch tasks run in parallel; index tasks fan in after
    [t1_twdb, t2_sdwis] >> t3_twdb_idx
    t2_sdwis >> t4_sdwis_idx
