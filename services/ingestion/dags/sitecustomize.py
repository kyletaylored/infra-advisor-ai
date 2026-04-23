# Python executes sitecustomize.py before any user code in every process.
# With PYTHONPATH=/opt/airflow/dags, this runs in the scheduler main process
# AND in every LocalExecutor task subprocess, ensuring ddtrace is initialized
# and DD_LOGS_INJECTION injects dd.trace_id/span_id into all log records.
try:
    import ddtrace.auto  # noqa: F401
except ImportError:
    pass  # dag-processor does not install ddtrace — silently skip
