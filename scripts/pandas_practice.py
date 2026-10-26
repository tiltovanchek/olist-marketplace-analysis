import pandas as pd

'''
cohort_users = pd.DataFrame({
    "user_id": [1, 2, 3, 4, 5, 6],
    "registered_at": [
        "2026-01-01", "2026-01-01", "2026-01-01",
        "2026-01-02", "2026-01-02", "2026-01-05"
    ]
})

visits = pd.DataFrame([
    (1, "2026-01-01"), (1, "2026-01-08"),
    (2, "2026-01-01"), (2, "2026-01-07"),
    (3, "2026-01-01"), (3, "2026-01-09"),
    (4, "2026-01-02"), (4, "2026-01-09"),
    (5, "2026-01-02"), (5, "2026-01-09"),
    (5, "2026-01-10"),
    (6, "2026-01-05"), (6, "2026-01-10")
], columns=["user_id", "visit_at"])

cohort_users["registered_at"] = pd.to_datetime(
    cohort_users["registered_at"]
)
visits["visit_at"] = pd.to_datetime(visits["visit_at"])

last_complete_day = pd.Timestamp("2026-01-10")

# Дата через семь дней после регистрации
mask = (cohort_users["registered_at"] + pd.Timedelta(days=7)) <= last_complete_day

# Количество дней между визитом и регистрацией после merge
# (joined["visit_at"] - joined["registered_at"]).dt.days

cohort_users = cohort_users[mask]

joined = cohort_users.merge(
    visits,
    on='user_id',
    how='left'
)

print('\ncohort\n', cohort_users)
print('\nvisits\n', visits)
print('\nfull_table\n', joined)

joined['is_7D'] = (
    (joined['visit_at'] - joined['registered_at']).dt.days == 7 
).astype(int)

print('\nfull_table\n', joined)

joined_group = (
    joined.groupby(['user_id', 'registered_at'])
    .agg(
        flag=('is_7D', 'max')
    ).reset_index()
)

print('\nfull_table\n', joined_group)

final_table = (
    joined_group.groupby(['registered_at'])
    .agg(
        cohort=('registered_at', 'size'),
        returned=('flag', 'sum'),
        retention=('flag', 'mean')
    ).reset_index()
)

print('\nfinal_table\n', final_table)
'''

orders = pd.DataFrame({
    "order_id": [101, 102, 103, 201],
    "user_id": [1, 1, 1, 2],
    "paid_at": [
        "2026-01-05 10:00:00",
        "2026-01-03 10:00:00",
        None,
        "2026-01-07 12:00:00",
    ],
})

order_items = pd.DataFrame({
    "order_id": [101, 101, 102, 103, 201, 201],
    "quantity": [1, 2, 1, 1, 2, 1],
    "unit_price": [100, 150, 600, 5000, 100, 100],
})

orders['paid_at'] = pd.to_datetime(orders['paid_at'])

order_items['revenue'] = order_items['quantity'] * order_items['unit_price']

revenues = (
    order_items.groupby(['order_id'])
    .agg(
        order_revenue=('revenue', 'sum')
    ).reset_index()
)

joined = (
    orders.loc[orders['paid_at'].notna()]
    .merge(revenues, how='inner', on='order_id')
    .sort_values(['user_id', 'paid_at', 'order_id'])
    .reset_index(drop=True)
)

joined['cumulative_revenue'] = (
    joined.groupby('user_id')['order_revenue'].cumsum()
)

user_metrics = (
    joined.groupby('user_id')
    .agg(
        orders_cnt=('order_id', 'nunique'),
        revenue=('order_revenue', 'sum'),
        AOV=('order_revenue', 'mean'),
        first_paid_at=('paid_at', 'min'),
        last_paid_at=('paid_at', 'max')
    ).reset_index()
)

print(user_metrics)

print('Total AOV: ', round(user_metrics['revenue'].sum() / user_metrics['orders_cnt'].sum(), 2))
# мы уже оставили только платящих пользователей, так что можно взять данные из user_metrics
print('ARPPU: ', round(user_metrics['revenue'].sum() / user_metrics['orders_cnt'].count(), 2))