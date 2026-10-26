SELECT ROUND(AVG(rating), 2) AS average_rating
FROM reviews
WHERE device = 'tv';