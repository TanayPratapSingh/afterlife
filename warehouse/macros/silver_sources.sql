{#
  Make `source('silver', ...)` resolve to the Delta tables written by the
  Spark jobs, the same way on both local engines:
    duckdb  a view per table over delta_scan(), inside the DuckDB file
    spark   a Delta table per directory in the session's catalog (dbt-spark
            in session mode starts with an empty in memory catalog)
  On Databricks, Snowflake, and BigQuery the tables already exist by name.
#}
{% macro register_silver_sources() %}
  {% if execute and target.type in ['duckdb', 'spark'] %}
    {% set tables = ['rw_paper_status', 'rw_notices', 'rw_changes', 'rw_history', 'rw_quarantine',
                     'openalex_works', 'openalex_citations', 'pubmed_flags', 'pmc_ids', 'pmc_metadata'] %}
    {% if target.type == 'duckdb' %}
      {% do run_query('create schema if not exists silver') %}
    {% else %}
      {% do run_query('create database if not exists silver') %}
    {% endif %}
    {% for t in tables %}
      {% set location = lake_root() ~ '/silver/' ~ t %}
      {% if target.type == 'duckdb' %}
        {% do run_query("create or replace view silver." ~ t ~ " as select * from delta_scan('" ~ location ~ "')") %}
      {% else %}
        {% do run_query("create table if not exists silver." ~ t ~ " using delta location '" ~ location ~ "'") %}
      {% endif %}
    {% endfor %}
  {% endif %}
{% endmacro %}

{#
  Write gold under the schema name itself (`gold`), not `<target schema>_gold`.
  The marts are a published contract; their names should not depend on who ran dbt.
#}
{% macro generate_schema_name(custom_schema_name, node) %}
  {{ return(custom_schema_name | trim if custom_schema_name else target.schema) }}
{% endmacro %}

{# Absolute path of the local Delta lake. afterlife/gold.py sets the variable. #}
{% macro lake_root() %}
  {{ return(env_var('AFTERLIFE_LAKE_ABS', '../data/lake')) }}
{% endmacro %}
