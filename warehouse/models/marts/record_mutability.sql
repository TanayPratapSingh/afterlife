{#
  What Retraction Watch changed after first publishing a record, by column
  and by snapshot date. Changes are semantic (see SEMANTIC in silver.py):
  pure reformatting is not here, and neither is the quarantined 2026-10-15
  snapshot, which was never merged.
#}

with changes as (
    select record_id, change_date, change_type, changed_columns
    from {{ source('silver', 'rw_changes') }}
    where change_type in ('update', 'reappear')
),

exploded as (
    select c.record_id, c.change_date, {{ exploded_item() }} as column_name
    from changes c
    {{ explode_join('c', 'changed_columns') }}
)

select
    change_date,
    column_name,
    count(distinct record_id) as records
from exploded
group by change_date, column_name
