with payments as (
    select
        order_id,
        sum(amount) as amount_paid,
        count(*) as n_payments
    from {{ ref('stg_payments') }}
    group by order_id
)

select
    o.order_id,
    o.customer_id,
    c.country,
    o.order_date,
    o.status,
    o.status = 'completed' as is_completed,
    coalesce(p.amount_paid, 0) as amount_paid,
    coalesce(p.n_payments, 0) as n_payments
from {{ ref('stg_orders') }} o
left join {{ ref('stg_customers') }} c on c.customer_id = o.customer_id
left join payments p on p.order_id = o.order_id
