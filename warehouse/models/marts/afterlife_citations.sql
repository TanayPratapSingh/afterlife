{#
  Citations a paper received in the years after it was retracted.

  Year granularity, and conservative: a citation counts as "after" only if
  its year is strictly greater than the retraction year, so citations in the
  retraction year itself are never attributed to the afterlife. OpenAlex
  counts_by_year starts at var('citation_window_start'); for papers retracted
  before that year the post retraction count is a lower bound, and
  window_complete is false. retract_and_replace marks papers republished in
  corrected form; their later citations may be meant for the corrected version.
#}

with retracted as (
    select paper_key, openalex_id, first_retraction_date, retract_and_replace,
           extract(year from first_retraction_date) as retraction_year
    from {{ ref('registry') }}
    where verdict = 'retracted'
      and openalex_id is not null
      and first_retraction_date is not null
),

cites as (
    select openalex_id, year, cited_by_count
    from {{ source('silver', 'openalex_citations') }}
)

select
    r.paper_key,
    r.openalex_id,
    r.retraction_year,
    coalesce(sum(case when c.year > r.retraction_year then c.cited_by_count end), 0)
        as citations_after,
    coalesce(sum(case when c.year <= r.retraction_year then c.cited_by_count end), 0)
        as citations_through_retraction_year,
    r.retraction_year + 1 >= {{ var('citation_window_start') }} as window_complete,
    r.retract_and_replace
from retracted r
left join cites c on c.openalex_id = r.openalex_id
group by r.paper_key, r.openalex_id, r.retraction_year, r.retract_and_replace
