{#
  How far the four registries agree, measured fairly.

  A registry can only flag a paper it could know about. PubMed indexes papers
  with a PMID; PMC holds only its open data subset; OpenAlex covers works with
  a DOI or PMID; Retraction Watch aims at every retraction anywhere. So recall
  is computed per source over the papers that source was eligible to flag,
  against the union of all flags as the reference. The union is not ground truth; it is the most
  generous reading of "somebody says this is retracted".
#}

with r as (
    select * from {{ ref('registry') }}
    where verdict in ('retracted', 'disputed')
),

eligibility as (
    select
        paper_key,
        rw_status = 'retracted' as rw_flag,
        flag_openalex,
        flag_pubmed,
        flag_pmc,
        true as eligible_rw,
        doi is not null or pmid is not null as eligible_openalex,
        pmid is not null as eligible_pubmed,
        flag_pmc is not null as eligible_pmc
    from r
)

select 'retraction_watch' as source,
    sum(case when eligible_rw then 1 else 0 end) as eligible,
    sum(case when eligible_rw and coalesce(rw_flag, false) then 1 else 0 end) as flagged
from eligibility
union all
select 'openalex',
    sum(case when eligible_openalex then 1 else 0 end),
    sum(case when eligible_openalex and flag_openalex then 1 else 0 end)
from eligibility
union all
select 'pubmed',
    sum(case when eligible_pubmed then 1 else 0 end),
    sum(case when eligible_pubmed and flag_pubmed then 1 else 0 end)
from eligibility
union all
select 'pmc',
    sum(case when eligible_pmc then 1 else 0 end),
    sum(case when eligible_pmc and flag_pmc then 1 else 0 end)
from eligibility
