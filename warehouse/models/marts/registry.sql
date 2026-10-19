{#
  The cross registry answer: one row per paper that any registry flags.

  Flags mean "this source's retraction set contains the paper". For OpenAlex
  and PubMed only the positive set was fetched, so false means "not in that
  set", which covers both "known and fine" and "unknown to that source".
  PMC is the exception: for a linked article in the open data subset its
  is_retracted value is known either way, and null means no linked copy.

  verdict:
    retracted   Retraction Watch says retracted, or it has no record and
                another registry flags the paper
    disputed    Retraction Watch has a record that is not a standing
                retraction (concern, correction, reinstatement) while another
                registry flags the paper as retracted
    concern, corrected, reinstated, ok   Retraction Watch's status, uncontested

  When two Retraction Watch keys resolve to one paper (one record carried the
  DOI, another only the PMID), the more severe status wins and rw_keys > 1
  marks the row so the merge can be audited.
#}

with ids as (
    -- Notices are excluded here and counted in flag_exclusions.
    select * from {{ ref('int_paper_identity') }} where notice_reason is null
),

keys as (
    select
        paper_key,
        max(doi) as doi,
        max(pmid) as pmid,
        max(pmcid) as pmcid,
        max(openalex_id) as openalex_id,
        max(case when source = 'openalex' and source_flag then 1 else 0 end) = 1 as flag_openalex,
        max(case when source = 'pubmed' then 1 else 0 end) = 1 as flag_pubmed,
        max(case when source = 'pmc' then 1 else 0 end) as has_pmc,
        max(case when source = 'pmc' and source_flag then 1 else 0 end) as pmc_retracted
    from ids
    group by paper_key
),

replaced as (
    -- Retraction Watch tags a retraction that was republished in corrected
    -- form "Retract and Replace" (927 notices). A citation to such a paper
    -- after its retraction may be meant for the corrected version.
    select
        paper_key,
        max(case when {{ array_has('reasons', "'Retract and Replace'") }} then 1 else 0 end)
            as retract_and_replace
    from {{ source('silver', 'rw_notices') }}
    where nature = 'Retraction'
    group by paper_key
),

rw_ranked as (
    select
        i.paper_key,
        s.status,
        s.first_retraction_date,
        s.last_retraction_date,
        s.original_date,
        s.n_notices,
        {{ join_sorted('s.natures') }} as natures,
        s.title,
        coalesce(rr.retract_and_replace, 0) as retract_and_replace,
        case s.status
            when 'retracted' then 5 when 'concern' then 4 when 'reinstated' then 3
            when 'corrected' then 2 else 1
        end as severity
    from ids i
    join {{ source('silver', 'rw_paper_status') }} s
      on i.source = 'retraction_watch' and i.source_key = s.paper_key
    left join replaced rr on rr.paper_key = s.paper_key
),

rw as (
    select
        paper_key,
        max(severity) as severity,
        count(*) as rw_keys,
        min(first_retraction_date) as first_retraction_date,
        max(last_retraction_date) as last_retraction_date,
        min(original_date) as original_date,
        sum(n_notices) as n_notices,
        max(natures) as natures,
        max(title) as title,
        max(retract_and_replace) as retract_and_replace
    from rw_ranked
    group by paper_key
),

oa as (
    select
        i.paper_key,
        max(o.title) as title,
        max(o.field) as field,
        max(o.cited_by_count) as cited_by_count
    from ids i
    join {{ source('silver', 'openalex_works') }} o
      on i.source = 'openalex' and i.source_key = o.openalex_id
    group by i.paper_key
),

joined as (
    select
        k.paper_key,
        k.doi,
        k.pmid,
        k.pmcid,
        k.openalex_id,
        coalesce(rw.title, oa.title) as title,
        oa.field,
        oa.cited_by_count,
        case rw.severity
            when 5 then 'retracted' when 4 then 'concern' when 3 then 'reinstated'
            when 2 then 'corrected' when 1 then 'ok'
        end as rw_status,
        rw.rw_keys,
        rw.first_retraction_date,
        rw.last_retraction_date,
        rw.original_date,
        rw.n_notices,
        rw.natures,
        coalesce(rw.retract_and_replace, 0) = 1 as retract_and_replace,
        k.flag_openalex,
        k.flag_pubmed,
        case when k.has_pmc = 1 then k.pmc_retracted = 1 end as flag_pmc
    from keys k
    left join rw on rw.paper_key = k.paper_key
    left join oa on oa.paper_key = k.paper_key
)

select
    *,
    (case when rw_status = 'retracted' then 1 else 0 end)
        + (case when flag_openalex then 1 else 0 end)
        + (case when flag_pubmed then 1 else 0 end)
        + (case when flag_pmc then 1 else 0 end) as n_flagging,
    case
        when rw_status = 'retracted' then 'retracted'
        when rw_status is null then 'retracted'
        when flag_openalex or flag_pubmed or coalesce(flag_pmc, false) then 'disputed'
        else rw_status
    end as verdict
from joined
-- A paper is in the registry if Retraction Watch has a record of it or any
-- other source flags it. A PMC copy that is not flagged and that nothing else
-- mentions under the same key is a linking artifact, not a registry entry.
where rw_status is not null
   or flag_openalex
   or flag_pubmed
   or coalesce(flag_pmc, false)
