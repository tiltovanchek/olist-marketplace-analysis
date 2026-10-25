import pandas as pd

# cohort_users = pd.DataFrame({
#     "user_id": [1, 2, 3, 4, 5, 6],
#     "registered_at": [
#         "2026-01-01", "2026-01-01", "2026-01-01",
#         "2026-01-02", "2026-01-02", "2026-01-05"
#     ]
# })

# print('table', cohort_users)

# visits = pd.DataFrame([
#     (1, "2026-01-01"), (1, "2026-01-08"),
#     (2, "2026-01-01"), (2, "2026-01-07"),
#     (3, "2026-01-01"), (3, "2026-01-09"),
#     (4, "2026-01-02"), (4, "2026-01-09"),
#     (5, "2026-01-02"), (5, "2026-01-09"),
#     (5, "2026-01-10"),
#     (6, "2026-01-05"), (6, "2026-01-10")
# ], columns=["user_id", "visit_at"])

# cohort_users["registered_at"] = pd.to_datetime(
#     cohort_users["registered_at"]
# )
# visits["visit_at"] = pd.to_datetime(visits["visit_at"])

# last_complete_day = pd.Timestamp("2026-01-10")

# # Дата через семь дней после регистрации
# mask = (cohort_users["registered_at"] + pd.Timedelta(days=7)) < last_complete_day

# # Количество дней между визитом и регистрацией после merge
# # (joined["visit_at"] - joined["registered_at"]).dt.days

# cohort_users = cohort_users[mask]

# print('table', cohort_users)


orders = pd.DataFrame({
    "order_id": [101, 102, 103, 104],
    "user_id": [1, 1, 2, 2],
    "paid_at": [
        "2026-01-05 12:00",
        "2026-01-20 15:00",
        None,
        "2026-02-03 10:00"
    ],
    "amount": [1000, 2000, 1500, 3000]
})

users = pd.DataFrame({
    "user_id": [1, 2, 3, 4],
    "city": ["Москва", "Москва", "Санкт-Петербург", "Санкт-Петербург"]
})

print(orders)

orders['paid_at'] = pd.to_datetime(orders['paid_at'])

mask = orders["paid_at"].notna()

paid = orders.loc[mask].copy()


cnt_paid = len(paid)
total_revenue = paid['amount'].sum()
unique_users = paid['user_id'].nunique()

print('cnt_paid: ', cnt_paid)
print('total_revenue: ', total_revenue)
print('unique_users: ', unique_users)
print('mean_cheque: ', paid['amount'].mean())


paid['month'] = paid['paid_at'].dt.to_period('M')

month_revenue = paid.groupby('month')['amount'].sum()
month_unique_users = paid.groupby('month')['user_id'].nunique()

monthly = (
    paid.groupby('month')
    .agg(
        revenue=('amount', 'sum'),
        unique_buyers=('user_id', 'nunique'),
        orders_cnt=('order_id', 'size'),
        average_check=('amount', 'mean')
    )
    .reset_index()
)

joined = users.merge(
    orders,
    on='user_id',
    how='left'
)

joined['is_paid'] = joined['paid_at'].notna().astype(int)
print('\nJoined\n', joined)

help_table = (
    joined.groupby(['user_id', 'city'])
    .agg(
        is_buyer=('is_paid', 'max')
    )
    .reset_index()
)

print('\n1234\n',
    help_table.groupby('city').agg(
    users_cnt=('user_id', 'size'),
    buyer_cnt=('is_buyer', 'sum'),
    CR=('is_buyer', 'mean')
    ).reset_index()
)