{#
  Flags that were not counted, and why. Every exclusion the registry makes is
  visible here, so the registry size can be reconciled with each source's
  raw count.
#}

select source, notice_reason, count(*) as rows_excluded
from {{ ref('int_paper_identity') }}
where notice_reason is not null
group by source, notice_reason
