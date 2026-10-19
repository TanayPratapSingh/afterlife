{#
  Time from publication to retraction, per Retraction Watch retraction notice.

  Only records with both dates are measured. A notice dated before its paper
  (a data entry error, or a retraction of a preprint before formal
  publication) is kept with negative_lag = true and excluded from summaries,
  so the summary never silently absorbs bad dates.
#}

select
    n.record_id,
    n.paper_key,
    n.original_date,
    n.notice_date,
    {{ days_between('n.original_date', 'n.notice_date') }} as lag_days,
    extract(year from n.notice_date) as retraction_year,
    extract(year from n.original_date) as original_year,
    n.publisher,
    {{ days_between('n.original_date', 'n.notice_date') }} < 0 as negative_lag
from {{ source('silver', 'rw_notices') }} n
where n.nature = 'Retraction'
  and n.notice_date is not null
  and n.original_date is not null
