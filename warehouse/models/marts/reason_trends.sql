{#
  Stated reasons for retraction, by the year of the notice. A notice usually
  lists several reasons; each is counted once per notice. Labels come from the
  current version of each record, after Retraction Watch's 2025 taxonomy
  edits, so old and new notices share one vocabulary.
#}

with notices as (
    select record_id, extract(year from notice_date) as retraction_year, reasons
    from {{ source('silver', 'rw_notices') }}
    where nature = 'Retraction' and notice_date is not null
),

exploded as (
    select n.record_id, n.retraction_year, {{ exploded_item() }} as reason
    from notices n
    {{ explode_join('n', 'reasons') }}
)

select
    retraction_year,
    reason,
    count(distinct record_id) as notices
from exploded
group by retraction_year, reason
