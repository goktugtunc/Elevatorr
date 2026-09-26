#!/bin/bash
# Creates an extra database used by the test-suite (same credentials).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE traderkirala_test;
EOSQL
