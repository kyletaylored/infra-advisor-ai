-- Datadog DBM grants and function setup (Postgres >= 15).
-- CREATE USER datadog is handled separately by setup-dbm.sh.
-- This file is the SOLE source of post-install Postgres state required by
-- the DD postgres integration's full feature set:
--   * pg_monitor role     → most stat-table reads
--   * pg_stat_statements  → query metrics
--   * pg_buffercache      → collect_buffercache_metrics
--   * datadog.explain_statement → query samples / explain plans (DBM)
--   * datadog.column_statistics → collect_column_statistics (DBM)
--
-- Re-runnable: every CREATE/GRANT uses IF NOT EXISTS or REPLACE.

ALTER ROLE datadog INHERIT;
CREATE SCHEMA IF NOT EXISTS datadog;
GRANT USAGE ON SCHEMA datadog TO datadog;
GRANT USAGE ON SCHEMA public TO datadog;
GRANT pg_monitor TO datadog;

-- Extensions required by the integration's metric collectors.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_buffercache;

-- DBM query samples need a SECURITY DEFINER wrapper around EXPLAIN so the
-- low-privilege datadog role can run plans against application queries
-- without needing full table read access.
CREATE OR REPLACE FUNCTION datadog.explain_statement(
  l_query TEXT,
  OUT explain JSON
)
RETURNS SETOF JSON AS
$$
DECLARE
  curs REFCURSOR;
  plan JSON;
BEGIN
  SET TRANSACTION READ ONLY;
  OPEN curs FOR EXECUTE pg_catalog.concat('EXPLAIN (FORMAT JSON) ', l_query);
  FETCH curs INTO plan;
  CLOSE curs;
  RETURN QUERY SELECT plan;
END;
$$
LANGUAGE 'plpgsql'
RETURNS NULL ON NULL INPUT
SECURITY DEFINER;

-- DBM's collect_column_statistics reads pg_stats and exposes column-level
-- distribution data (most-common-vals, n_distinct, etc.) to power the DBM
-- Query Optimization view. anyarray pg_stats columns are cast to text per
-- DD's canonical function template — Postgres can't infer concrete result
-- types for anyarray when no anyelement input exists. SECURITY DEFINER
-- lets the low-priv datadog role read pg_stats rows owned by other roles.
CREATE OR REPLACE FUNCTION datadog.column_statistics()
RETURNS TABLE (
    schemaname             text,
    tablename              text,
    attname                text,
    inherited              boolean,
    null_frac              real,
    avg_width              integer,
    n_distinct             real,
    most_common_vals       text,
    most_common_freqs      real[],
    histogram_bounds       text,
    correlation            real,
    most_common_elems      text,
    most_common_elem_freqs real[],
    elem_count_histogram   real[]
) AS $$
    SELECT
        schemaname::text,
        tablename::text,
        attname::text,
        inherited,
        null_frac,
        avg_width,
        n_distinct,
        most_common_vals::text,
        most_common_freqs,
        histogram_bounds::text,
        correlation,
        most_common_elems::text,
        most_common_elem_freqs,
        elem_count_histogram
    FROM pg_stats;
$$ LANGUAGE SQL
SECURITY DEFINER;
