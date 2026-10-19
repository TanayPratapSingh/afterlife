{#
  One canonical key per paper, across four registries that use different ids.

  Retraction Watch and OpenAlex carry DOIs; PubMed carries only PMIDs; PMC
  carries PMCIDs and sometimes the others. A PMID is mapped to a DOI through
  whichever source pairs them, in a fixed order of trust: Retraction Watch's
  own pairing first, then OpenAlex, then NCBI's PMC id map. The key is
  `doi:<doi>` whenever any source supplies one, else `pmid:<pmid>`, else the
  source's own id.

  One hop only. A paper whose only link between two registries is a chain of
  two mappings is not merged, and shows up as two rows. That undercounts
  agreement slightly and never overcounts it.

  Notices are not papers. OpenAlex sets is_retracted on retraction notices as
  well as on the papers they retract: of its 136,873 retracted works, 38,161
  have type `retraction`. A row is marked is_notice when its DOI is one that
  Retraction Watch records as a notice DOI, when OpenAlex types it
  `retraction`, or when its PMID is a Retraction Watch notice PMID. Title
  prefixes ("RETRACTED:") are not used: Elsevier renames retracted originals
  that way and Hindawi titles its separate notices the same way.
#}

with notice_dois as (
    select distinct notice_doi as doi from {{ source('silver', 'rw_notices') }}
    where notice_doi is not null
    union
    select distinct doi from {{ source('silver', 'openalex_works') }}
    where type = 'retraction' and doi is not null
),

notice_pmids as (
    select distinct notice_pmid as pmid from {{ source('silver', 'rw_notices') }}
    where notice_pmid is not null
),

pmid_doi_pairs as (
    select pmid, doi, 1 as trust from {{ source('silver', 'rw_paper_status') }}
    where pmid is not null and doi is not null
    union all
    select pmid, doi, 2 from {{ source('silver', 'openalex_works') }}
    where pmid is not null and doi is not null
    union all
    select pmid, doi, 3 from {{ source('silver', 'pmc_ids') }}
    where pmid is not null and doi is not null
),

pmid_best_trust as (
    select pmid, min(trust) as trust from pmid_doi_pairs group by pmid
),

pmid_doi as (
    -- The most trusted pairing; ties broken by the smallest DOI so the
    -- result does not depend on row order.
    select p.pmid, min(p.doi) as doi, count(distinct p.doi) as doi_candidates
    from pmid_doi_pairs p
    join pmid_best_trust b on p.pmid = b.pmid and p.trust = b.trust
    group by p.pmid
),

rw as (
    select
        coalesce('doi:' || s.doi, 'doi:' || m.doi, s.paper_key) as paper_key,
        coalesce(s.doi, m.doi) as doi,
        s.pmid,
        cast(null as {{ dbt.type_string() }}) as pmcid,
        cast(null as {{ dbt.type_string() }}) as openalex_id,
        'retraction_watch' as source,
        s.paper_key as source_key,
        cast(null as boolean) as source_flag
    from {{ source('silver', 'rw_paper_status') }} s
    left join pmid_doi m on s.pmid = m.pmid
),

oa as (
    select
        coalesce('doi:' || o.doi, 'doi:' || m.doi, 'pmid:' || o.pmid, 'openalex:' || o.openalex_id)
            as paper_key,
        coalesce(o.doi, m.doi) as doi,
        o.pmid,
        o.pmcid,
        o.openalex_id,
        'openalex' as source,
        o.openalex_id as source_key,
        o.is_retracted as source_flag
    from {{ source('silver', 'openalex_works') }} o
    left join pmid_doi m on o.pmid = m.pmid
),

pm as (
    select
        coalesce('doi:' || m.doi, 'pmid:' || f.pmid) as paper_key,
        m.doi,
        f.pmid,
        cast(null as {{ dbt.type_string() }}) as pmcid,
        cast(null as {{ dbt.type_string() }}) as openalex_id,
        'pubmed' as source,
        f.pmid as source_key,
        true as source_flag
    from {{ source('silver', 'pubmed_flags') }} f
    left join pmid_doi m on f.pmid = m.pmid
),

pmc as (
    select
        coalesce('doi:' || p.doi, 'doi:' || m.doi, 'pmid:' || p.pmid, 'pmcid:' || p.pmcid)
            as paper_key,
        coalesce(p.doi, m.doi) as doi,
        p.pmid,
        p.pmcid,
        cast(null as {{ dbt.type_string() }}) as openalex_id,
        'pmc' as source,
        p.pmcid as source_key,
        p.is_retracted as source_flag
    from {{ source('silver', 'pmc_metadata') }} p
    left join pmid_doi m on p.pmid = m.pmid
    where p.in_open_data
),

unioned as (
    select * from rw
    union all select * from oa
    union all select * from pm
    union all select * from pmc
)

select
    u.*,
    case
        when u.source = 'openalex' and oa_type.type = 'retraction' then 'openalex_type_retraction'
        when nd.doi is not null and u.source <> 'retraction_watch' then 'rw_notice_doi'
        when np.pmid is not null and u.source <> 'retraction_watch' then 'rw_notice_pmid'
    end as notice_reason
from unioned u
left join notice_dois nd on nd.doi = u.doi
left join notice_pmids np on np.pmid = u.pmid
left join (
    select openalex_id, type from {{ source('silver', 'openalex_works') }}
) oa_type on u.source = 'openalex' and oa_type.openalex_id = u.source_key
