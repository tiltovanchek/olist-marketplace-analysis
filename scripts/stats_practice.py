import scipy.stats as st
from statsmodels.stats.proportion import proportions_ztest, confint_proportions_2indep

def s_2(p, n):
    return n * p * (1 - p) / (n - 1)

def v(s_2, n):
    return s_2 ** 2 / n

def var_bern(p, n):
    return p * (1 - p) / n 

def nu()



ALPHA = 0.05

users_A = 15000
users_B = 15000

buyers_A = 1500
buyers_B = 1620

CR_A = buyers_A * 100 / users_A
CR_B = buyers_B * 100 / users_B
        
abs_diff = round(CR_B - CR_A, 2)
relative_diff = round((CR_B - CR_A) * 100 / CR_A, 2)

p_val = proportions_ztest(count=[buyers_B, buyers_A], nobs=[15000, 15000], value=0, alternative='two-sided')[1]
CI = confint_proportions_2indep(count1=buyers_B, nobs1=users_B, count2=buyers_A, nobs2=users_A, method='wald', compare='diff', alpha=0.05)
print(f'CR_A: {CR_A}, CR_B: {CR_B}')
print(f'abs_diff: {abs_diff}, relative_diff: {relative_diff}')
print('p-value: ', round(p_val, 2))
print(f'CI: ({round(CI[0], 3)}, {round(CI[1], 3)})')

print(f'var_A: {var_bern(p=CR_A * 0.01, n=users_A):.8f}')
print(f'var_B: {var_bern(p=CR_B * 0.01, n=users_B):.8f}')

