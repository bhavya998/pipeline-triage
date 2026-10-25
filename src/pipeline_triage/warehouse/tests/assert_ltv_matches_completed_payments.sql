-- Customer lifetime value must sum to completed-order payments.
with ltv as (
    select coalesce(sum(lifetime_value), 0) as total from {{ ref('dim_customers') }}
),
completed as (
    select coalesce(sum(amount_paid), 0) as total from {{ ref('fct_orders') }} where is_completed
)
select ltv.total as ltv_total, completed.total as completed_total
from ltv, completed
where abs(ltv.total - completed.total) > 0.01
