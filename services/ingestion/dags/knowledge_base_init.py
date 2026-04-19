import ddtrace.auto  # must be first import — enables DJM + APM auto-instrumentation
# DD_DATA_JOBS_ENABLED=true must be set on the Airflow scheduler pod environment

# Run manually once at initial deployment and after corpus refresh.
# This DAG has no cron schedule (schedule_interval=None) — trigger via Airflow UI or CLI:
#   airflow dags trigger knowledge_base_init

import logging
from datetime import datetime, timezone

from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="knowledge_base_init",
    schedule_interval=None,  # manual trigger only
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["init", "knowledge-base", "synthetic"],
    doc_md="""
    ## Knowledge Base Init DAG

    Generates 80 synthetic knowledge base documents using Azure OpenAI GPT-4o
    and indexes them into Azure AI Search. Generation is idempotent — if the
    index already contains ≥80 documents with `source='synthetic'`, the script
    exits without making API calls.

    **Trigger:** Manual only (`schedule_interval=None`).
    Run once at initial deployment, then re-run whenever the document corpus
    needs refreshing.

    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    """,
) as dag:

    def run_generate_synthetic_docs(**context):
        """Import and call generate_synthetic_docs.main() as a PythonOperator task."""
        import sys
        import os

        # The script lives at ../../scripts/ relative to the dags/ directory.
        # In the Airflow container, DAGs are mounted at /opt/airflow/dags and
        # scripts at /opt/airflow/scripts.
        script_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        script_dir = os.path.abspath(script_dir)

        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        log.info("Importing generate_synthetic_docs from: %s", script_dir)

        import generate_synthetic_docs

        generate_synthetic_docs.main()
        log.info("generate_synthetic_docs.main() completed successfully.")

    generate_docs = PythonOperator(
        task_id="generate_synthetic_docs",
        python_callable=run_generate_synthetic_docs,
    )

    generate_docs
