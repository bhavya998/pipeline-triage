select
    payment_id,
    cast(order_id as integer) as order_id,
    method,
    cast(amount as decimal(12, 2)) as amount
from {{ ref('raw_payments') }}
