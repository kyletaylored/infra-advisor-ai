import ddtrace.auto  # must be first import — enables DJM + APM auto-instrumentation
# DD_DATA_JOBS_ENABLED=true must be set on the Airflow scheduler pod environment

import logging
import os
import shutil
from datetime import datetime, timezone
from io import BytesIO

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_CONTAINER = "raw-data"
PROCESSED_CONTAINER = "processed-data"
LOCAL_RAW_PATH = "/tmp/infra_advisor_raw"
LOCAL_PROCESSED_PATH = "/tmp/infra_advisor_processed"

INDEXER_NAME = "infra-advisor-indexer"

# NBI columns expected in raw Parquet; used for null-drop and chunk construction
KEY_COLUMNS = [
    "STRUCTURE_NUMBER_008",
    "FACILITY_CARRIED_007",
    "LOCATION_009",
    "DECK_COND_058",
    "SUPERSTRUCTURE_COND_059",
    "SUBSTRUCTURE_COND_060",
    "SUFFICIENCY_RATING",
]

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="spark_feature_engineering",
    schedule_interval="@daily",
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["feature-engineering", "spark", "transportation"],
    doc_md="""
    ## Spark Feature Engineering DAG

    Transforms raw government infrastructure data stored in Azure Blob Storage
    (`raw-data` container) into embedding-ready text chunks and uploads the
    results to the `processed-data` container, then triggers an Azure AI Search
    indexer run so new content is available to the MCP server within minutes.

    ### Pipeline stages

    1. **download_raw_data** — Downloads the most recent NBI Parquet file from
       `raw-data/nbi/` in Azure Blob Storage.  If no file exists yet (e.g.
       `nbi_refresh` has not run), a small synthetic DataFrame is generated
       locally so the DAG completes end-to-end without blocking on upstream data.

    2. **run_spark_feature_engineering** — Spins up a local PySpark session and
       applies the following transformations:
       - Drop rows missing any of the 7 key NBI columns
       - Normalise all string columns (lowercase + strip whitespace)
       - Compute `chunk_text` by concatenating structure number, facility,
         location, and condition fields into a single searchable narrative
       - Append `processed_at` (UTC ISO timestamp) for provenance tracking
       - Write output as Parquet to `/tmp/infra_advisor_processed/`

    3. **upload_processed_data** — Uploads processed Parquet files to
       `processed-data/nbi/{ds}/` with date partitioning so each daily run
       is independently addressable.

    4. **trigger_search_reindex** — Calls the Azure AI Search REST API to run
       `infra-advisor-indexer`.  If the indexer does not exist, the step logs a
       warning and exits cleanly (idempotent).

    **Schedule:** Daily (`@daily`)
    **DJM:** Requires `DD_DATA_JOBS_ENABLED=true` on the Airflow scheduler pod.
    **Env vars required:** `AZURE_STORAGE_CONNECTION_STRING`,
    `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_API_KEY`
    """,
) as dag:

    # -----------------------------------------------------------------------
    # Task 1 — download_raw_data
    # -----------------------------------------------------------------------
    def download_raw_data(**context):
        """Download the latest NBI Parquet from Azure Blob Storage (raw-data).

        Falls back to a small synthetic DataFrame when no blob is found, so the
        DAG runs end-to-end even before nbi_refresh has produced real data.
        """
        import pandas as pd

        run_date = context["ds"]  # e.g. 2025-04-20
        date_nodash = run_date.replace("-", "")
        blob_name = f"nbi/texas/nbi_tx_{date_nodash}.parquet"

        os.makedirs(LOCAL_RAW_PATH, exist_ok=True)
        local_file = os.path.join(LOCAL_RAW_PATH, f"nbi_tx_{date_nodash}.parquet")

        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

        downloaded = False
        if conn_str:
            try:
                from azure.storage.blob import BlobServiceClient

                blob_service = BlobServiceClient.from_connection_string(conn_str)
                container_client = blob_service.get_container_client(RAW_CONTAINER)
                blob_client = container_client.get_blob_client(blob_name)

                log.info("Attempting to download blob: %s/%s", RAW_CONTAINER, blob_name)
                blob_data = blob_client.download_blob().readall()
                with open(local_file, "wb") as f:
                    f.write(blob_data)
                log.info("Downloaded %d bytes to %s", len(blob_data), local_file)
                downloaded = True

            except Exception as exc:  # ResourceNotFoundError or connection error
                log.warning(
                    "Blob %s not found or connection failed (%s). "
                    "Generating synthetic data for end-to-end validation.",
                    blob_name,
                    exc,
                )
        else:
            log.warning(
                "AZURE_STORAGE_CONNECTION_STRING not set — generating synthetic data."
            )

        if not downloaded:
            log.info("Building synthetic NBI DataFrame (%d rows)", 25)
            synthetic_records = []
            for i in range(25):
                synthetic_records.append(
                    {
                        "STRUCTURE_NUMBER_008": f"TX-SYNTH-{i:04d}",
                        "FACILITY_CARRIED_007": f"US HWY {(i % 10) * 10 + 10}",
                        "LOCATION_009": f"0.{i:02d} MI S OF FM {100 + i}",
                        "COUNTY_CODE_003": str(200 + i),
                        "STATE_CODE_001": "48",
                        "ADT_029": str(5000 + i * 100),
                        "YEAR_ADT_030": "2023",
                        "DECK_COND_058": str((i % 9) + 1),
                        "SUPERSTRUCTURE_COND_059": str((i % 9) + 1),
                        "SUBSTRUCTURE_COND_060": str((i % 9) + 1),
                        "STRUCTURALLY_DEFICIENT": "0",
                        "SUFFICIENCY_RATING": str(60.0 + i),
                        "INSPECT_DATE_090": "0124",
                        "YEAR_BUILT_027": str(1960 + i),
                        "LAT_016": str(30.0 + i * 0.01),
                        "LONG_017": str(-97.0 - i * 0.01),
                    }
                )
            df_synthetic = pd.DataFrame(synthetic_records)
            df_synthetic.to_parquet(local_file, index=False)
            log.info("Synthetic Parquet written to %s", local_file)

        context["ti"].xcom_push(key="local_raw_file", value=local_file)
        return local_file

    # -----------------------------------------------------------------------
    # Task 2 — run_spark_feature_engineering
    # -----------------------------------------------------------------------
    def run_spark_feature_engineering(**context):
        """Run PySpark transformations on the raw NBI Parquet file.

        Transformations applied:
        - Drop rows with nulls in KEY_COLUMNS
        - Lowercase + strip all string columns
        - Compute chunk_text narrative column
        - Append processed_at timestamp
        Output written as Parquet to LOCAL_PROCESSED_PATH.
        """
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F

        local_raw_file = context["ti"].xcom_pull(
            key="local_raw_file", task_ids="download_raw_data"
        )
        if not local_raw_file or not os.path.exists(local_raw_file):
            raise FileNotFoundError(f"Raw file not found: {local_raw_file}")

        log.info("Initialising SparkSession (local[*])...")
        spark = (
            SparkSession.builder.appName("infra-advisor-feature-eng")
            .master("local[*]")
            .config("spark.sql.shuffle.partitions", "4")
            .config("spark.driver.memory", "1g")
            .getOrCreate()
        )
        spark.sparkContext.setLogLevel("WARN")

        log.info("Reading raw Parquet from %s", local_raw_file)
        df = spark.read.parquet(local_raw_file)
        raw_count = df.count()
        log.info("Raw row count: %d", raw_count)
        log.info("Raw schema:\n%s", df._jdf.schema().treeString())

        # -- Drop rows missing key columns --
        df = df.dropna(subset=KEY_COLUMNS)
        log.info("Rows after null drop on key columns: %d", df.count())

        # -- Normalise string columns (lowercase + strip) --
        string_cols = [
            f.name for f in df.schema.fields
            if str(f.dataType) == "StringType()"
        ]
        for col_name in string_cols:
            df = df.withColumn(col_name, F.trim(F.lower(F.col(col_name))))

        # -- Compute chunk_text narrative --
        df = df.withColumn(
            "chunk_text",
            F.concat_ws(
                " | ",
                F.concat(F.lit("Structure: "), F.col("STRUCTURE_NUMBER_008")),
                F.concat(F.lit("Facility: "), F.col("FACILITY_CARRIED_007")),
                F.concat(F.lit("Location: "), F.col("LOCATION_009")),
                F.concat(F.lit("Deck condition: "), F.col("DECK_COND_058")),
                F.concat(F.lit("Superstructure condition: "), F.col("SUPERSTRUCTURE_COND_059")),
                F.concat(F.lit("Substructure condition: "), F.col("SUBSTRUCTURE_COND_060")),
                F.concat(F.lit("Sufficiency rating: "), F.col("SUFFICIENCY_RATING")),
            ),
        )

        # -- Append processed_at timestamp --
        processed_at_iso = datetime.now(timezone.utc).isoformat()
        df = df.withColumn("processed_at", F.lit(processed_at_iso))

        # -- Write processed output --
        shutil.rmtree(LOCAL_PROCESSED_PATH, ignore_errors=True)
        os.makedirs(LOCAL_PROCESSED_PATH, exist_ok=True)

        output_path = os.path.join(LOCAL_PROCESSED_PATH, "nbi")
        df.write.mode("overwrite").parquet(output_path)

        final_count = df.count()
        log.info("Processed row count: %d", final_count)
        log.info("Processed schema:\n%s", df._jdf.schema().treeString())
        log.info("Processed Parquet written to %s", output_path)

        spark.stop()

        context["ti"].xcom_push(key="processed_output_path", value=output_path)
        return output_path

    # -----------------------------------------------------------------------
    # Task 3 — upload_processed_data
    # -----------------------------------------------------------------------
    def upload_processed_data(**context):
        """Upload processed Parquet files to Azure Blob Storage (processed-data).

        Uses a date-partitioned blob prefix: processed-data/nbi/{ds}/
        All .parquet part files produced by Spark are uploaded individually.
        """
        processed_path = context["ti"].xcom_pull(
            key="processed_output_path", task_ids="run_spark_feature_engineering"
        )
        run_date = context["ds"]

        if not processed_path or not os.path.isdir(processed_path):
            raise FileNotFoundError(f"Processed output directory not found: {processed_path}")

        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if not conn_str:
            log.warning(
                "AZURE_STORAGE_CONNECTION_STRING not set — skipping blob upload. "
                "Processed files remain at %s",
                processed_path,
            )
            return

        from azure.storage.blob import BlobServiceClient

        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service.get_container_client(PROCESSED_CONTAINER)
        from _dd_blob import dd_upload_blob

        # Ensure container exists
        try:
            container_client.create_container()
            log.info("Created container: %s", PROCESSED_CONTAINER)
        except Exception:
            pass  # already exists

        blob_prefix = f"nbi/{run_date}/"
        uploaded = 0

        for fname in os.listdir(processed_path):
            if not fname.endswith(".parquet"):
                continue
            local_fp = os.path.join(processed_path, fname)
            blob_name = f"{blob_prefix}{fname}"

            with open(local_fp, "rb") as data:
                dd_upload_blob(container_client, blob_name, data, dag_id="spark_feature_engineering")
            log.info("Uploaded %s → %s/%s", fname, PROCESSED_CONTAINER, blob_name)
            uploaded += 1

        log.info("Upload complete: %d Parquet file(s) → %s/%s", uploaded, PROCESSED_CONTAINER, blob_prefix)

    # -----------------------------------------------------------------------
    # Task 4 — trigger_search_reindex
    # -----------------------------------------------------------------------
    def trigger_search_reindex(**context):
        """Invoke the Azure AI Search indexer REST API to reindex processed data.

        Calls POST .../indexers/{indexer}/run if the indexer exists.
        Logs a warning and exits cleanly if the indexer is not found (idempotent).
        """
        search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
        search_api_key = os.environ.get("AZURE_SEARCH_API_KEY")

        if not search_endpoint or not search_api_key:
            log.warning(
                "AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_API_KEY not set — "
                "skipping reindex trigger."
            )
            return

        # Normalise endpoint (strip trailing slash)
        search_endpoint = search_endpoint.rstrip("/")

        headers = {
            "api-key": search_api_key,
            "Content-Type": "application/json",
        }
        api_version = "2023-11-01"

        # Check whether the indexer exists before attempting to run it
        get_url = f"{search_endpoint}/indexers/{INDEXER_NAME}?api-version={api_version}"
        resp = requests.get(get_url, headers=headers, timeout=30)

        if resp.status_code == 404:
            log.warning(
                "Indexer '%s' does not exist in Azure AI Search — "
                "would trigger reindex once indexer is created.",
                INDEXER_NAME,
            )
            return

        if resp.status_code != 200:
            resp.raise_for_status()

        log.info("Indexer '%s' found — triggering run.", INDEXER_NAME)

        run_url = f"{search_endpoint}/indexers/{INDEXER_NAME}/run?api-version={api_version}"
        run_resp = requests.post(run_url, headers=headers, timeout=30)

        if run_resp.status_code == 202:
            log.info(
                "Indexer '%s' run accepted (HTTP 202). Reindex in progress.",
                INDEXER_NAME,
            )
        else:
            run_resp.raise_for_status()

    # -----------------------------------------------------------------------
    # Wire up operators
    # -----------------------------------------------------------------------
    t1 = PythonOperator(
        task_id="download_raw_data",
        python_callable=download_raw_data,
    )

    t2 = PythonOperator(
        task_id="run_spark_feature_engineering",
        python_callable=run_spark_feature_engineering,
    )

    t3 = PythonOperator(
        task_id="upload_processed_data",
        python_callable=upload_processed_data,
    )

    t4 = PythonOperator(
        task_id="trigger_search_reindex",
        python_callable=trigger_search_reindex,
    )

    t1 >> t2 >> t3 >> t4
