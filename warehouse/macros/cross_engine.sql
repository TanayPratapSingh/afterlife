{#
  Every place the four engines disagree on syntax, behind adapter.dispatch.
  default__ is DuckDB (Postgres flavoured). spark__ also serves databricks,
  because dbt-databricks falls back to spark__ implementations.
#}

{# Expand an array column into rows: `from t {{ explode_join('t', 'col') }}` #}
{% macro explode_join(alias, col) %}
  {{ return(adapter.dispatch('explode_join', 'afterlife')(alias, col)) }}
{% endmacro %}
{% macro default__explode_join(alias, col) %}cross join unnest({{ alias }}.{{ col }}) as u(item){% endmacro %}
{% macro spark__explode_join(alias, col) %}lateral view explode({{ alias }}.{{ col }}) u as item{% endmacro %}
{% macro snowflake__explode_join(alias, col) %}, lateral flatten(input => {{ alias }}.{{ col }}) u{% endmacro %}
{% macro bigquery__explode_join(alias, col) %}cross join unnest({{ alias }}.{{ col }}) as item{% endmacro %}

{# The value of one exploded element, as a string. #}
{% macro exploded_item() %}
  {{ return(adapter.dispatch('exploded_item', 'afterlife')()) }}
{% endmacro %}
{% macro default__exploded_item() %}u.item{% endmacro %}
{% macro spark__exploded_item() %}u.item{% endmacro %}
{% macro snowflake__exploded_item() %}u.value::string{% endmacro %}
{% macro bigquery__exploded_item() %}item{% endmacro %}

{# Exact interpolated percentile. BigQuery has no exact aggregate percentile,
   so it uses APPROX_QUANTILES; conformance against it would need a tolerance. #}
{% macro pct(expr, p) %}
  {{ return(adapter.dispatch('pct', 'afterlife')(expr, p)) }}
{% endmacro %}
{% macro default__pct(expr, p) %}quantile_cont({{ expr }}, {{ p }}){% endmacro %}
{% macro spark__pct(expr, p) %}percentile({{ expr }}, {{ p }}){% endmacro %}
{% macro snowflake__pct(expr, p) %}percentile_cont({{ p }}) within group (order by {{ expr }}){% endmacro %}
{% macro bigquery__pct(expr, p) %}approx_quantiles({{ expr }}, 100)[offset({{ (p * 100) | int }})]{% endmacro %}

{# Join an array of strings with a separator, sorted for determinism. #}
{% macro join_sorted(col, sep=';') %}
  {{ return(adapter.dispatch('join_sorted', 'afterlife')(col, sep)) }}
{% endmacro %}
{% macro default__join_sorted(col, sep) %}array_to_string(list_sort({{ col }}), '{{ sep }}'){% endmacro %}
{% macro spark__join_sorted(col, sep) %}concat_ws('{{ sep }}', array_sort({{ col }})){% endmacro %}
{% macro snowflake__join_sorted(col, sep) %}array_to_string(array_sort({{ col }}), '{{ sep }}'){% endmacro %}
{% macro bigquery__join_sorted(col, sep) %}array_to_string(array(select x from unnest({{ col }}) x order by x), '{{ sep }}'){% endmacro %}

{# Number of elements in an array. #}
{% macro array_len(col) %}
  {{ return(adapter.dispatch('array_len', 'afterlife')(col)) }}
{% endmacro %}
{% macro default__array_len(col) %}len({{ col }}){% endmacro %}
{% macro spark__array_len(col) %}size({{ col }}){% endmacro %}
{% macro snowflake__array_len(col) %}array_size({{ col }}){% endmacro %}
{% macro bigquery__array_len(col) %}array_length({{ col }}){% endmacro %}

{# Whole days between two dates. dbt.datediff exists, but its Spark
   implementation goes through a timestamp cast; this keeps it on dates. #}
{% macro days_between(start, end) %}
  {{ return(adapter.dispatch('days_between', 'afterlife')(start, end)) }}
{% endmacro %}
{% macro default__days_between(start, end) %}date_diff('day', {{ start }}, {{ end }}){% endmacro %}
{% macro spark__days_between(start, end) %}datediff({{ end }}, {{ start }}){% endmacro %}
{% macro snowflake__days_between(start, end) %}datediff('day', {{ start }}, {{ end }}){% endmacro %}
{% macro bigquery__days_between(start, end) %}date_diff({{ end }}, {{ start }}, day){% endmacro %}

{# True when an array of strings contains a value. #}
{% macro array_has(col, value) %}
  {{ return(adapter.dispatch('array_has', 'afterlife')(col, value)) }}
{% endmacro %}
{% macro default__array_has(col, value) %}list_contains({{ col }}, {{ value }}){% endmacro %}
{% macro spark__array_has(col, value) %}array_contains({{ col }}, {{ value }}){% endmacro %}
{% macro snowflake__array_has(col, value) %}array_contains({{ value }}::variant, {{ col }}){% endmacro %}
{% macro bigquery__array_has(col, value) %}{{ value }} in unnest({{ col }}){% endmacro %}
