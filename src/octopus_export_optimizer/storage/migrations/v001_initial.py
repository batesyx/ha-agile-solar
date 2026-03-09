"""Initial database schema — v001."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Create the initial tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tariff_slots (
            interval_start TEXT NOT NULL,
            interval_end TEXT NOT NULL,
            rate_inc_vat_pence REAL NOT NULL,
            tariff_type TEXT NOT NULL CHECK(tariff_type IN ('export', 'import')),
            product_code TEXT NOT NULL,
            provenance TEXT NOT NULL DEFAULT 'published'
                CHECK(provenance IN ('actual', 'published', 'forecast')),
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (interval_start, tariff_type)
        );

        CREATE INDEX IF NOT EXISTS idx_tariff_slots_type_start
            ON tariff_slots(tariff_type, interval_start);

        CREATE TABLE IF NOT EXISTS meter_intervals (
            interval_start TEXT NOT NULL,
            interval_end TEXT NOT NULL,
            kwh REAL NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('export', 'import')),
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (interval_start, direction)
        );

        CREATE INDEX IF NOT EXISTS idx_meter_intervals_direction_start
            ON meter_intervals(direction, interval_start);

        CREATE TABLE IF NOT EXISTS ha_state_snapshots (
            timestamp TEXT NOT NULL PRIMARY KEY,
            battery_soc_pct REAL,
            pv_power_kw REAL,
            feed_in_kw REAL,
            load_power_kw REAL,
            grid_consumption_kw REAL,
            battery_charge_kw REAL,
            battery_discharge_kw REAL,
            work_mode TEXT,
            max_soc REAL,
            min_soc REAL,
            force_charge_power_kw REAL,
            force_discharge_power_kw REAL
        );

        CREATE TABLE IF NOT EXISTS flat_rate_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rate_pence REAL NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT
        );

        CREATE TABLE IF NOT EXISTS revenue_intervals (
            interval_start TEXT NOT NULL PRIMARY KEY,
            export_kwh REAL NOT NULL,
            agile_rate_pence REAL NOT NULL,
            agile_revenue_pence REAL NOT NULL,
            flat_rate_pence REAL NOT NULL,
            flat_revenue_pence REAL NOT NULL,
            uplift_pence REAL NOT NULL,
            calculated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_revenue_intervals_start
            ON revenue_intervals(interval_start);

        CREATE TABLE IF NOT EXISTS revenue_summaries (
            period_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            total_export_kwh REAL NOT NULL,
            agile_revenue_pence REAL NOT NULL,
            flat_revenue_pence REAL NOT NULL,
            uplift_pence REAL NOT NULL,
            avg_realised_rate_pence REAL NOT NULL,
            intervals_above_flat INTEGER NOT NULL,
            total_intervals INTEGER NOT NULL,
            calculated_at TEXT NOT NULL,
            PRIMARY KEY (period_type, period_key)
        );

        CREATE TABLE IF NOT EXISTS recommendation_input_snapshots (
            id TEXT NOT NULL PRIMARY KEY,
            timestamp TEXT NOT NULL,
            battery_soc_pct REAL,
            current_export_rate_pence REAL,
            best_upcoming_rate_pence REAL,
            best_upcoming_slot_start TEXT,
            upcoming_rates_count INTEGER DEFAULT 0,
            current_import_rate_pence REAL,
            solar_estimate_kw REAL,
            feed_in_kw REAL,
            pv_power_kw REAL,
            load_power_kw REAL,
            battery_charge_kw REAL,
            battery_discharge_kw REAL,
            remaining_generation_heuristic REAL,
            exportable_battery_kwh REAL,
            battery_headroom_kwh REAL
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            timestamp TEXT NOT NULL PRIMARY KEY,
            state TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            explanation TEXT NOT NULL,
            battery_aware INTEGER NOT NULL DEFAULT 0,
            valid_until TEXT,
            input_snapshot_id TEXT NOT NULL,
            FOREIGN KEY (input_snapshot_id)
                REFERENCES recommendation_input_snapshots(id)
        );

        CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT NOT NULL PRIMARY KEY,
            job_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK(status IN ('running', 'success', 'failed')),
            records_processed INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_job_runs_type_started
            ON job_runs(job_type, started_at);
    """)
