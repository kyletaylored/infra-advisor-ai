import ddtrace.auto  # must be first import — enables DJM + APM auto-instrumentation
# DD_DATA_JOBS_ENABLED=true must be set on the Airflow scheduler pod environment

# Weekly DAG that fetches real public infrastructure documents and indexes
# them into Azure AI Search. Supplements the synthetic knowledge base with
# live data from OpenFEMA, EIA, and FHWA NBI public APIs.
# Schedule: every Sunday at 02:00 UTC (low-traffic window)

import logging
from datetime import datetime, timezone

from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="public_docs_ingestion",
    schedule_interval="0 2 * * 0",  # weekly Sunday 02:00 UTC
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["public-data", "knowledge-base", "weekly"],
    doc_md="""
    ## Public Docs Ingestion DAG

    Fetches real infrastructure documents from four public data sources and
    upserts them into Azure AI Search, supplementing the synthetic knowledge
    base with live government data:

    1. **OpenFEMA Disaster Summaries** — per-state disaster declaration profiles
       grouped by incident type with infrastructure implications narrative.
    2. **OpenFEMA Hazard Mitigation** — HM grant project categories and federal
       spend by state, mapped to resilience investment priorities.
    3. **EIA State Energy Profiles** — annual retail electricity sales and prices
       by state (requires `EIA_API_KEY`; skipped if not set).
    4. **NBI County Bridge Summaries** — county-level bridge condition summaries
       including structural deficiency rates, scour-critical counts, and pre-1970
       inventory from the FHWA NBI bridge API.

    **Schedule:** Weekly — Sunday 02:00 UTC (low-traffic window)

    **Idempotency:** If Azure AI Search already contains ≥ 200 non-synthetic
    documents, the script exits without making API calls.

    **States covered:** TX, LA, FL, OK, AZ, CA

    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    """,
) as dag:

    def fetch_and_index(**context):
        """Import and call fetch_public_docs.main() as a PythonOperator task."""
        import sys
        import os

        # The script lives at ../../scripts/ relative to the dags/ directory.
        # In the Airflow container, DAGs are mounted at /opt/airflow/dags and
        # scripts at /opt/airflow/scripts.
        script_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        script_dir = os.path.abspath(script_dir)

        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        log.info("Importing fetch_public_docs from: %s", script_dir)

        import fetch_public_docs

        fetch_public_docs.main()
        log.info("fetch_public_docs.main() completed successfully.")

    ingest = PythonOperator(
        task_id="fetch_and_index",
        python_callable=fetch_and_index,
    )

    ingest
