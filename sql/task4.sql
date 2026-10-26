SELECT movie_title
FROM views
WHERE status = 'completed'
GROUP BY movie_title
HAVING COUNT(DISTINCT user_id) >= 2
ORDER BY AVG(duration_min) DESC NULLS LAST,
         COUNT(*) DESC,
         movie_title ASC
LIMIT 1;