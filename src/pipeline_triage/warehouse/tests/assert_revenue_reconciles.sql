-- Completed revenue in the mart must equal completed payments in staging.
with mart as (
    select coalesce(sum(revenue), 0) as total from {{ ref('revenue_daily') }}
),
source as (
    select coalesce(sum(p.amount), 0) as total
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o on o.order_id = p.order_id
    where o.status = 'completed'
)
select mart.total as mart_total, source.total as source_total
from mart, source
where abs(mart.total - source.total) > 0.01
