\set ON_ERROR_STOP on
\set QUIET 1
CREATE TEMP TABLE migration_table_counts (
  table_name text PRIMARY KEY,
  row_count bigint NOT NULL
);
SELECT format(
  'INSERT INTO migration_table_counts SELECT %L, count(*)::bigint FROM %I.%I;',
  schemaname || '.' || tablename,
  schemaname,
  tablename
)
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'keycloak')
ORDER BY schemaname, tablename
\gexec
\set QUIET 0
\pset format unaligned
\pset tuples_only on
SELECT
  count(*) AS table_count,
  sum(row_count) AS total_rows,
  md5(string_agg(table_name || '=' || row_count, '|' ORDER BY table_name)) AS table_count_fingerprint
FROM migration_table_counts;
