"""Lightweight HTTP API for data export.

Provides CSV and JSON exports of tariff rates, revenue data,
recommendations, and raw database downloads.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from octopus_export_optimizer.storage.database import Database

logger = logging.getLogger(__name__)


class ExportHandler(BaseHTTPRequestHandler):
    """HTTP request handler for data export endpoints."""

    db: Database
    db_path: str

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("API: %s", format % args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        routes = {
            "/api/export/tariffs": self._export_tariffs,
            "/api/export/revenue": self._export_revenue,
            "/api/export/recommendations": self._export_recommendations,
            "/api/export/snapshots": self._export_snapshots,
            "/api/export/commands": self._export_commands,
            "/api/export/database": self._export_database,
            "/api/revenue/daily": self._revenue_daily,
            "/api/status": self._status,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        else:
            self._send_json(404, {"error": "Not found", "endpoints": list(routes.keys())})

    def _parse_dates(self, params: dict) -> tuple[str, str] | None:
        """Parse and validate date range from query params.

        Returns (from_date, to_date) strings, or None if invalid.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        from_date = params.get("from", [f"{today}T00:00:00+00:00"])[0]
        to_date = params.get("to", [f"{today}T23:59:59+00:00"])[0]
        try:
            datetime.fromisoformat(from_date)
            datetime.fromisoformat(to_date)
        except (ValueError, TypeError):
            return None
        return from_date, to_date

    def _export_tariffs(self, params: dict) -> None:
        dates = self._parse_dates(params)
        if dates is None:
            self._send_json(400, {"error": "Invalid date format. Use ISO 8601."})
            return
        from_date, to_date = dates
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT interval_start, interval_end, rate_inc_vat_pence,
                          tariff_type, product_code, provenance
                   FROM tariff_slots
                   WHERE interval_start >= ? AND interval_start <= ?
                   ORDER BY interval_start""",
                (from_date, to_date),
            ).fetchall()

        fmt = params.get("format", ["csv"])[0]
        columns = ["interval_start", "interval_end", "rate_inc_vat_pence",
                    "tariff_type", "product_code", "provenance"]
        self._send_table(rows, columns, fmt, "tariffs")

    def _export_revenue(self, params: dict) -> None:
        dates = self._parse_dates(params)
        if dates is None:
            self._send_json(400, {"error": "Invalid date format. Use ISO 8601."})
            return
        from_date, to_date = dates
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT interval_start, export_kwh, agile_rate_pence,
                          agile_revenue_pence, flat_rate_pence, flat_revenue_pence,
                          uplift_pence
                   FROM revenue_intervals
                   WHERE interval_start >= ? AND interval_start <= ?
                   ORDER BY interval_start""",
                (from_date, to_date),
            ).fetchall()

        fmt = params.get("format", ["csv"])[0]
        columns = ["interval_start", "export_kwh", "agile_rate_pence",
                    "agile_revenue_pence", "flat_rate_pence", "flat_revenue_pence",
                    "uplift_pence"]
        self._send_table(rows, columns, fmt, "revenue")

    def _export_recommendations(self, params: dict) -> None:
        dates = self._parse_dates(params)
        if dates is None:
            self._send_json(400, {"error": "Invalid date format. Use ISO 8601."})
            return
        from_date, to_date = dates
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT r.timestamp, r.state, r.reason_code, r.explanation,
                          r.battery_aware, r.target_max_soc,
                          s.battery_soc_pct, s.current_export_rate_pence,
                          s.best_upcoming_rate_pence, s.exportable_battery_kwh
                   FROM recommendations r
                   LEFT JOIN recommendation_input_snapshots s
                     ON r.input_snapshot_id = s.id
                   WHERE r.timestamp >= ? AND r.timestamp <= ?
                   ORDER BY r.timestamp""",
                (from_date, to_date),
            ).fetchall()

        fmt = params.get("format", ["csv"])[0]
        columns = ["timestamp", "state", "reason_code", "explanation",
                    "battery_aware", "target_max_soc", "battery_soc_pct",
                    "current_export_rate_pence", "best_upcoming_rate_pence",
                    "exportable_battery_kwh"]
        self._send_table(rows, columns, fmt, "recommendations")

    def _export_snapshots(self, params: dict) -> None:
        dates = self._parse_dates(params)
        if dates is None:
            self._send_json(400, {"error": "Invalid date format. Use ISO 8601."})
            return
        from_date, to_date = dates
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT timestamp, battery_soc_pct, pv_power_kw, feed_in_kw,
                          load_power_kw, grid_consumption_kw,
                          battery_charge_kw, battery_discharge_kw
                   FROM ha_state_snapshots
                   WHERE timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp""",
                (from_date, to_date),
            ).fetchall()

        fmt = params.get("format", ["csv"])[0]
        columns = ["timestamp", "battery_soc_pct", "pv_power_kw", "feed_in_kw",
                    "load_power_kw", "grid_consumption_kw",
                    "battery_charge_kw", "battery_discharge_kw"]
        self._send_table(rows, columns, fmt, "snapshots")

    def _export_commands(self, params: dict) -> None:
        dates = self._parse_dates(params)
        if dates is None:
            self._send_json(400, {"error": "Invalid date format. Use ISO 8601."})
            return
        from_date, to_date = dates
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT timestamp, previous_mode, new_mode, target_max_soc,
                          recommendation_state, reason_code, success, error
                   FROM inverter_commands
                   WHERE timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp""",
                (from_date, to_date),
            ).fetchall()

        fmt = params.get("format", ["csv"])[0]
        columns = ["timestamp", "previous_mode", "new_mode", "target_max_soc",
                    "recommendation_state", "reason_code", "success", "error"]
        self._send_table(rows, columns, fmt, "commands")

    def _revenue_daily(self, params: dict) -> None:
        """Return daily revenue summaries as JSON for charting."""
        days = int(params.get("days", ["30"])[0])
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT period_key, total_export_kwh,
                          agile_revenue_pence, flat_revenue_pence,
                          uplift_pence, avg_realised_rate_pence,
                          import_cost_pence, total_import_kwh,
                          net_revenue_pence, true_profit_pence
                   FROM revenue_summaries
                   WHERE period_type = 'day'
                   ORDER BY period_key DESC
                   LIMIT ?""",
                (days,),
            ).fetchall()

        data = [
            {
                "date": r[0],
                "export_kwh": r[1],
                "agile_pence": r[2],
                "flat_pence": r[3],
                "uplift_pence": r[4],
                "avg_rate_pence": r[5],
                "import_cost_pence": r[6],
                "import_kwh": r[7],
                "net_revenue_pence": r[8],
                "true_profit_pence": r[9],
            }
            for r in reversed(rows)  # Oldest first for charting
        ]
        self._send_json(200, {"data": data, "count": len(data)})

    def _export_database(self, params: dict) -> None:
        """Download the raw SQLite database file."""
        db_path = Path(self.db_path)
        if not db_path.exists():
            self._send_json(404, {"error": "Database file not found"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-sqlite3")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="optimizer_{datetime.now(timezone.utc).strftime("%Y%m%d")}.db"',
        )
        self.end_headers()
        with open(db_path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _status(self, params: dict) -> None:
        self._send_json(200, {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _send_table(
        self, rows: list, columns: list[str], fmt: str, name: str
    ) -> None:
        if fmt == "json":
            data = [dict(zip(columns, row)) for row in rows]
            self._send_json(200, {"data": data, "count": len(data)})
        else:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)

            body = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{name}_{datetime.now(timezone.utc).strftime("%Y%m%d")}.csv"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_api_server(
    db: Database,
    db_path: str,
    port: int = 8099,
) -> HTTPServer:
    """Start the export API server in a background thread.

    Returns the server instance (call .shutdown() to stop).
    """
    ExportHandler.db = db
    ExportHandler.db_path = db_path

    server = HTTPServer(("0.0.0.0", port), ExportHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Export API started on port %d", port)
    return server
