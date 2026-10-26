SELECT COUNT(*) AS completed_views_count
FROM views
WHERE status = 'completed'
  AND duration_min >= 90;