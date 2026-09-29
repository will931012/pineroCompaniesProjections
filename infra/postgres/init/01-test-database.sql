-- Runs once when the local volume is first created.
-- Integration tests truncate every table in this database; never point them at `pinero`.
CREATE DATABASE pinero_test OWNER pinero;
