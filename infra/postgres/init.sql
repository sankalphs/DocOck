-- Runs once, on first initialisation of the docock-pgdata volume.
-- Tables are created by Alembic (Phase 2); this file only guarantees the
-- extensions those migrations assume.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
