-- Run this script in MySQL as an administrator (for example, root).
-- CHANGE THE PASSWORD below to a strong, unique password before running this file.
CREATE DATABASE IF NOT EXISTS SGIC_DMS
  CHARACTER SET utf8 COLLATE utf8_general_ci;
CREATE USER IF NOT EXISTS 'sgic_app'@'localhost' IDENTIFIED BY 'CHANGE_THIS_TO_A_LONG_RANDOM_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, CREATE VIEW, SHOW VIEW, TRIGGER ON SGIC_DMS.* TO 'sgic_app'@'localhost';
-- If you change the username or password above, update database.ini to match.
-- This account is scoped to SGIC_DMS; do not configure the application to use MySQL root.
