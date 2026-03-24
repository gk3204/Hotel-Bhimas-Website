DELETE FROM users WHERE username='admin';
INSERT INTO users (username, password_hash, role) VALUES ('admin', '$2b$12$8/qKV.lWf9JmERWY0UzKDufszsJkmKkX0XwQL/ejtl.B3ZfxRxCZW', 'admin');
SELECT user_id, username, role FROM users WHERE username='admin';
