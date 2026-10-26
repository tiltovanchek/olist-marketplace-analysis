SELECT u.city
FROM users AS u
JOIN views AS v ON v.user_id = u.user_id
WHERE v.status = 'completed'
GROUP BY u.city
ORDER BY COUNT(*) DESC, u.city ASC
LIMIT 1;