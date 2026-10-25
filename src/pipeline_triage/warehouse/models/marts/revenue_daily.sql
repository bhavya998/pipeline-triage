select
    order_date,
    count(*) as completed_orders,
    sum(amount_paid) as revenue
from {{ ref('fct_orders') }}
where status = 'completed'
group by order_date
