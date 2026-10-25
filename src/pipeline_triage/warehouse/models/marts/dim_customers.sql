select
    c.customer_id,
    c.full_name,
    c.email,
    c.country,
    min(o.order_date) as first_order_date,
    count(o.order_id) as n_orders,
    coalesce(sum(case when o.is_completed then o.amount_paid end), 0) as lifetime_value
from {{ ref('stg_customers') }} c
left join {{ ref('fct_orders') }} o on o.customer_id = c.customer_id
group by 1, 2, 3, 4
