"""Application bootstrap, scheduler, and job orchestration."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import date, datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from octopus_export_optimizer.calculations.aggregator import Aggregator
from octopus_export_optimizer.calculations.revenue_calculator import RevenueCalculator
from octopus_export_optimizer.calculations.revenue_estimator import estimate_revenue
from octopus_export_optimizer.calculations.solar_profile import SolarProfile
from octopus_export_optimizer.config.settings import AppSettings
from octopus_export_optimizer.control.evening_reserve import (
    calculate_reserve_soc,
    get_rolling_avg_evening_load,
)
from octopus_export_optimizer.control.inverter_controller import InverterController
from octopus_export_optimizer.ingestion.ha_state_ingester import HaStateIngester
from octopus_export_optimizer.ingestion.meter_ingester import MeterIngester
from octopus_export_optimizer.ingestion.octopus_client import OctopusClient
from octopus_export_optimizer.ingestion.tariff_ingester import TariffIngester
from octopus_export_optimizer.publishing.mqtt_publisher import MqttPublisher
from octopus_export_optimizer.recommendation.engine import RecommendationEngine
from octopus_export_optimizer.storage.command_repo import CommandRepo
from octopus_export_optimizer.storage.database import Database
from octopus_export_optimizer.storage.ha_state_repo import HaStateRepo
from octopus_export_optimizer.storage.job_repo import JobRepo
from octopus_export_optimizer.storage.meter_repo import MeterRepo
from octopus_export_optimizer.storage.recommendation_repo import RecommendationRepo
from octopus_export_optimizer.storage.revenue_repo import RevenueRepo
from octopus_export_optimizer.storage.tariff_repo import TariffRepo

logger = logging.getLogger(__name__)


class Application:
    """Main application: wires all components and runs scheduled jobs."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._stop_event = threading.Event()

        # Storage
        self.db = Database(settings.db_path)
        self.tariff_repo = TariffRepo(self.db)
        self.meter_repo = MeterRepo(self.db)
        self.ha_state_repo = HaStateRepo(self.db)
        self.revenue_repo = RevenueRepo(self.db)
        self.recommendation_repo = RecommendationRepo(self.db)
        self.job_repo = JobRepo(self.db)
        self.command_repo = CommandRepo(self.db)

        # Ingestion
        self.octopus_client: OctopusClient | None = None
        self.tariff_ingester: TariffIngester | None = None
        self.meter_ingester: MeterIngester | None = None
        self.ha_state_ingester: HaStateIngester | None = None

        # Calculations
        self.revenue_calculator = RevenueCalculator(settings.thresholds)
        self.aggregator = Aggregator()
        self.solar_profile = SolarProfile(settings.solar)

        # Recommendation
        self.engine = RecommendationEngine(
            settings.thresholds, settings.battery, settings.inverter_control
        )

        # Inverter control
        self.inverter_controller: InverterController | None = None

        # Publishing
        self.mqtt_publisher: MqttPublisher | None = None

        # Scheduler
        self.scheduler = BackgroundScheduler()

    def run(self) -> None:
        """Start the application. Blocks until interrupted."""
        self._setup_logging()
        logger.info("Starting Octopus Export Optimizer")

        self.db.connect()
        self._init_components()
        self._schedule_jobs()
        self.scheduler.start()

        # Run initial jobs immediately
        self._run_safe("initial_tariff_ingest", self.job_ingest_tariffs)
        self._run_safe("initial_meter_ingest", self.job_ingest_meter_data)
        self._run_safe("initial_ha_poll", self.job_poll_ha_state)
        self._run_safe("initial_calculate", self.job_calculate_revenue)
        self._run_safe("initial_recommend", self.job_generate_recommendation)
        self._run_safe("initial_publish", self.job_publish_to_ha)

        # Handle shutdown signals
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("Optimizer running. Press Ctrl+C to stop.")
        self._stop_event.wait()

        self._shutdown()

    def _init_components(self) -> None:
        """Initialise external-facing components."""
        if self.settings.octopus:
            self.octopus_client = OctopusClient(self.settings.octopus)
            self.tariff_ingester = TariffIngester(
                self.octopus_client, self.tariff_repo, self.job_repo
            )
            self.meter_ingester = MeterIngester(
                self.octopus_client, self.meter_repo, self.job_repo
            )

        if self.settings.home_assistant.token.get_secret_value():
            self.ha_state_ingester = HaStateIngester(
                self.settings.home_assistant, self.ha_state_repo
            )

        # Inverter control
        if (
            self.settings.inverter_control.enabled
            and self.settings.home_assistant.token.get_secret_value()
        ):
            self.inverter_controller = InverterController(
                self.settings.home_assistant,
                self.settings.inverter_control,
                self.command_repo,
            )
            logger.info("Inverter control enabled")

        if self.settings.mqtt.broker:
            self.mqtt_publisher = MqttPublisher(self.settings.mqtt)
            try:
                self.mqtt_publisher.connect()
                # Subscribe to control topics
                if self.inverter_controller:
                    self.mqtt_publisher.subscribe_kill_switch(
                        self.inverter_controller.set_auto_control
                    )
                    self.mqtt_publisher.subscribe_buffer(
                        self.inverter_controller.set_extra_buffer
                    )
            except Exception as e:
                logger.warning("Could not connect to MQTT broker: %s", e)
                self.mqtt_publisher = None

    def _schedule_jobs(self) -> None:
        """Register all periodic jobs with the scheduler."""
        sched = self.settings.schedule

        if self.tariff_ingester:
            self.scheduler.add_job(
                lambda: self._run_safe("tariff_ingest", self.job_ingest_tariffs),
                "interval",
                minutes=sched.tariff_ingestion_minutes,
                id="tariff_ingest",
            )
        if self.meter_ingester:
            self.scheduler.add_job(
                lambda: self._run_safe("meter_ingest", self.job_ingest_meter_data),
                "interval",
                minutes=sched.meter_ingestion_minutes,
                id="meter_ingest",
            )
        if self.ha_state_ingester:
            self.scheduler.add_job(
                lambda: self._run_safe("ha_poll", self.job_poll_ha_state),
                "interval",
                seconds=sched.ha_poll_seconds,
                id="ha_poll",
            )

        self.scheduler.add_job(
            lambda: self._run_safe("calculate_revenue", self.job_calculate_revenue),
            "interval",
            minutes=sched.revenue_calculation_minutes,
            id="calculate_revenue",
        )
        self.scheduler.add_job(
            lambda: self._run_safe("recommendation", self.job_generate_recommendation),
            "interval",
            seconds=sched.recommendation_seconds,
            id="recommendation",
        )
        self.scheduler.add_job(
            lambda: self._run_safe("aggregate", self.job_aggregate_summaries),
            "interval",
            minutes=sched.aggregation_minutes,
            id="aggregate",
        )
        self.scheduler.add_job(
            lambda: self._run_safe("publish", self.job_publish_to_ha),
            "interval",
            seconds=sched.publish_seconds,
            id="publish",
        )

    # ── Scheduled Jobs ──────────────────────────────────────────

    def job_ingest_tariffs(self) -> None:
        """Fetch and store tariff rates."""
        if not self.tariff_ingester:
            return
        self.tariff_ingester.ingest_export_rates()
        self.tariff_ingester.ingest_import_rates()

    def job_ingest_meter_data(self) -> None:
        """Fetch and store meter interval data."""
        if not self.meter_ingester:
            return
        self.meter_ingester.ingest_export_data()
        self.meter_ingester.ingest_import_data()

    def job_poll_ha_state(self) -> None:
        """Poll Home Assistant for current device state."""
        if not self.ha_state_ingester:
            return
        self.ha_state_ingester.poll()

    def job_calculate_revenue(self) -> None:
        """Calculate revenue for intervals that have both meter and tariff data."""
        now = datetime.now(timezone.utc)
        # Look back 72 hours to catch delayed meter data
        start, end = self.aggregator.rolling_boundaries(3, now)
        meters = self.meter_repo.get_export_intervals(start, end)
        tariffs = self.tariff_repo.get_export_rates(start, end)

        intervals = self.revenue_calculator.calculate_batch(meters, tariffs)
        if intervals:
            self.revenue_repo.upsert_intervals(intervals)
            logger.info("Calculated revenue for %d intervals", len(intervals))

    def job_generate_recommendation(self) -> None:
        """Generate a recommendation from current state."""
        now = datetime.now(timezone.utc)

        current_export = self.tariff_repo.get_current_export_rate(now)
        upcoming = self.tariff_repo.get_upcoming_export_rates(
            now, self.settings.thresholds.look_ahead_hours
        )
        upcoming_12h = self.tariff_repo.get_upcoming_export_rates(now, 12.0)
        current_import = self.tariff_repo.get_current_import_rate(now)
        ha_state = self.ha_state_repo.get_latest()
        remaining_gen = self.solar_profile.remaining_generation_factor(now)

        # Calculate dynamic evening reserve if controller is active
        reserve: float | None = None
        if self.inverter_controller:
            extra_buffer = self.inverter_controller.extra_buffer_kwh
            avg_load = get_rolling_avg_evening_load(
                self.ha_state_repo,
                now,
                default_kw=self.settings.inverter_control.default_evening_load_kw,
            )
            reserve = calculate_reserve_soc(
                now,
                self.settings.inverter_control.cheap_rate_start_hour,
                avg_load,
                extra_buffer,
                self.settings.battery.capacity_kwh,
            )

        snapshot = self.engine.build_snapshot(
            now=now,
            current_export=current_export,
            upcoming_exports=upcoming,
            current_import=current_import,
            ha_state=ha_state,
            remaining_generation=remaining_gen,
            minimum_soc_override=reserve,
        )
        recommendation = self.engine.evaluate(snapshot, upcoming_12h)
        self.recommendation_repo.save(recommendation, snapshot)

        # Execute inverter control
        if self.inverter_controller:
            self._run_safe(
                "inverter_control",
                lambda: self.inverter_controller.execute(recommendation),
            )

    def job_aggregate_summaries(self) -> None:
        """Aggregate revenue into day/month summaries."""
        now = datetime.now(timezone.utc)
        today = date.today()

        # Today
        day_start, day_end = self.aggregator.day_boundaries(today)
        day_intervals = self.revenue_repo.get_intervals(day_start, day_end)
        day_summary = self.aggregator.aggregate(
            day_intervals, "day", today.isoformat()
        )
        self.revenue_repo.upsert_summary(day_summary)

        # This month
        month_start, month_end = self.aggregator.month_boundaries(
            today.year, today.month
        )
        month_intervals = self.revenue_repo.get_intervals(month_start, month_end)
        month_key = f"{today.year}-{today.month:02d}"
        month_summary = self.aggregator.aggregate(
            month_intervals, "month", month_key
        )
        self.revenue_repo.upsert_summary(month_summary)

        # Rolling 7d and 30d
        for days, period_type in [(7, "rolling_7d"), (30, "rolling_30d")]:
            r_start, r_end = self.aggregator.rolling_boundaries(days, now)
            r_intervals = self.revenue_repo.get_intervals(r_start, r_end)
            r_summary = self.aggregator.aggregate(
                r_intervals, period_type, f"last_{days}d"
            )
            self.revenue_repo.upsert_summary(r_summary)

    def job_publish_to_ha(self) -> None:
        """Publish all current state to Home Assistant via MQTT."""
        if not self.mqtt_publisher:
            return

        now = datetime.now(timezone.utc)
        today = date.today()

        current_export = self.tariff_repo.get_current_export_rate(now)
        upcoming = self.tariff_repo.get_upcoming_export_rates(
            now, self.settings.thresholds.look_ahead_hours
        )
        best_upcoming = (
            max(upcoming, key=lambda s: s.rate_inc_vat_pence) if upcoming else None
        )
        current_import = self.tariff_repo.get_current_import_rate(now)

        self.mqtt_publisher.publish_rates(current_export, best_upcoming, current_import)

        rec = self.recommendation_repo.get_latest()
        self.mqtt_publisher.publish_recommendation(rec)

        today_summary = self.revenue_repo.get_summary("day", today.isoformat())
        month_key = f"{today.year}-{today.month:02d}"
        month_summary = self.revenue_repo.get_summary("month", month_key)

        # Estimate today's revenue from HA feed-in data when settlement
        # data hasn't arrived yet (typically ~24h delay from Octopus).
        today_is_estimated = False
        if today_summary is None or today_summary.total_export_kwh == 0:
            day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
            day_snapshots = self.ha_state_repo.get_by_range(day_start, now)
            day_tariffs = self.tariff_repo.get_export_rates(day_start, now)
            estimate = estimate_revenue(
                day_snapshots, day_tariffs, self.settings.thresholds, now,
            )
            if estimate.export_kwh > 0:
                from octopus_export_optimizer.models.revenue import RevenueSummary

                today_summary = RevenueSummary(
                    period_type="day",
                    period_key=today.isoformat(),
                    total_export_kwh=estimate.export_kwh,
                    agile_revenue_pence=estimate.agile_revenue_pence,
                    flat_revenue_pence=estimate.flat_revenue_pence,
                    uplift_pence=estimate.uplift_pence,
                    avg_realised_rate_pence=(
                        estimate.agile_revenue_pence / estimate.export_kwh
                        if estimate.export_kwh > 0
                        else 0.0
                    ),
                    intervals_above_flat=0,
                    total_intervals=0,
                    calculated_at=now,
                )
                today_is_estimated = True
                logger.debug(
                    "Using estimated today revenue: %.1fp from %.2f kWh (%d snapshots)",
                    estimate.agile_revenue_pence,
                    estimate.export_kwh,
                    estimate.snapshot_count,
                )

        self.mqtt_publisher.publish_revenue(
            today_summary, month_summary, today_is_estimated=today_is_estimated,
        )

        ha_state = self.ha_state_repo.get_latest()
        self.mqtt_publisher.publish_ha_state(ha_state)

        self.mqtt_publisher.publish_service_status(now)

        # Publish inverter control state
        if self.inverter_controller:
            last_cmd = self.command_repo.get_latest()
            last_cmd_info = None
            if last_cmd:
                last_cmd_info = (
                    f"{last_cmd.new_mode} at "
                    f"{last_cmd.timestamp.strftime('%H:%M:%S')} "
                    f"({last_cmd.reason_code})"
                )
            # Calculate current evening reserve for display
            avg_load = get_rolling_avg_evening_load(
                self.ha_state_repo,
                now,
                default_kw=self.settings.inverter_control.default_evening_load_kw,
            )
            reserve = calculate_reserve_soc(
                now,
                self.settings.inverter_control.cheap_rate_start_hour,
                avg_load,
                self.inverter_controller.extra_buffer_kwh,
                self.settings.battery.capacity_kwh,
            )
            self.mqtt_publisher.publish_control_state(
                auto_control_enabled=self.inverter_controller.auto_control_enabled,
                extra_buffer_kwh=self.inverter_controller.extra_buffer_kwh,
                commanded_mode=(
                    self.inverter_controller.last_commanded_mode.value
                    if self.inverter_controller.last_commanded_mode
                    else None
                ),
                evening_reserve_soc=reserve,
                last_command_info=last_cmd_info,
            )

    # ── Lifecycle ────────────────────────────────────────────────

    def _run_safe(self, name: str, fn: callable) -> None:
        """Run a job with error handling."""
        try:
            fn()
        except Exception:
            logger.exception("Job '%s' failed", name)

    def _signal_handler(self, signum: int, frame: object) -> None:
        logger.info("Shutdown signal received")
        self._stop_event.set()

    def _shutdown(self) -> None:
        """Clean up resources."""
        logger.info("Shutting down...")
        self.scheduler.shutdown(wait=False)
        if self.mqtt_publisher:
            self.mqtt_publisher.disconnect()
        if self.inverter_controller:
            self.inverter_controller.close()
        if self.octopus_client:
            self.octopus_client.close()
        if self.ha_state_ingester:
            self.ha_state_ingester.close()
        self.db.close()
        logger.info("Shutdown complete")

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.settings.log_level),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
