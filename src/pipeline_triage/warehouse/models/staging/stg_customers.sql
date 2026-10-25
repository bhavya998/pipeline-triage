select
    cast(customer_id as integer) as customer_id,
    full_name,
    lower(email) as email,
    country,
    cast(created_at as date) as created_at,
    cast(updated_at as timestamp) as updated_at
from {{ ref('raw_customers') }}
