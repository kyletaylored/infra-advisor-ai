"""Datadog-instrumented Azure Blob Storage upload helper.

Wraps container.get_blob_client(path).upload_blob(...) with a ddtrace span so
every raw/processed Parquet write appears in Datadog APM as azure.blob.upload.
Falls back to a plain upload if ddtrace is unavailable.
"""

import logging

log = logging.getLogger(__name__)


def dd_upload_blob(container_client, blob_path: str, data, *, dag_id: str = "", overwrite: bool = True) -> None:
    """Upload *data* to *blob_path* inside *container_client*, traced via ddtrace.

    Parameters
    ----------
    container_client:
        azure.storage.blob.ContainerClient already bound to the target container.
    blob_path:
        Path within the container (e.g. ``"nbi/2026-04-20/bridges.parquet"``).
    data:
        Bytes or file-like object to upload.
    dag_id:
        Airflow DAG ID, attached as a span tag for filtering in Datadog.
    overwrite:
        Passed through to upload_blob (default True).
    """
    try:
        from ddtrace import tracer

        container_name = container_client.container_name
        size_bytes = len(data) if isinstance(data, (bytes, bytearray)) else -1

        with tracer.trace("azure.blob.upload", service="airflow", resource=blob_path) as span:
            span.set_tag("blob.container", container_name)
            span.set_tag("blob.path", blob_path)
            span.set_tag("dag.id", dag_id)
            span.set_tag("component", "azure-blob-storage")
            if size_bytes >= 0:
                span.set_metric("blob.size_bytes", size_bytes)

            blob_client = container_client.get_blob_client(blob_path)
            blob_client.upload_blob(data, overwrite=overwrite)

        log.info("Stored blob (traced): %s/%s", container_name, blob_path)

    except ImportError:
        blob_client = container_client.get_blob_client(blob_path)
        blob_client.upload_blob(data, overwrite=overwrite)
        log.info("Stored blob (untraced): %s", blob_path)
