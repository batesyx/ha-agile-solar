"""Application bootstrap, scheduler, and job orchestration."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from octopus_export_optimizer.calculations.aggregator import Aggregator
from octopus_export_optimizer.calculations.revenue_calculator import RevenueCalculator
from octopus_export_optimizer.calculations.charge_planner import build_charge_plan
from octopus_export_optimizer.calculations.export_planner import build_export_plan
from octopus_export_optimizer.models.export_plan import ExportPlan
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
from octopus_export_optimizer.api.server import start_api_server
from octopus_export_optimizer.storage.backup import create_backup
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
        self._current_export_plan: ExportPlan | None = None

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

        # Start export API server
        self._api_server = start_api_server(
            self.db, self.settings.db_path, self.settings.api_port,
        )

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
        # Slot-boundary triggers: pre-position inverter before slot change,
        # confirm after. Agile slots change at :00 and :30.
        self.scheduler.add_job(
            lambda: self._run_safe("recommendation", self.job_generate_recommendation),
            "cron",
            minute="29,59",
            second=50,
            id="recommendation_pre_slot",
        )
        self.scheduler.add_job(
            lambda: self._run_safe("recommendation", self.job_generate_recommendation),
            "cron",
            minute="0,30",
            second=5,
            id="recommendation_post_slot",
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
        self.scheduler.add_job(
            lambda: self._run_safe("backup", self.job_backup_database),
            "cron",
            hour=2,
            minute=0,
            id="backup",
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
        # Look back to start of month to cover any gaps (meter data may arrive late)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = month_start
        end = now

        # Export revenue
        meters = self.meter_repo.get_export_intervals(start, end)
        tariffs = self.tariff_repo.get_export_rates(start, end)
        intervals = self.revenue_calculator.calculate_batch(meters, tariffs)
        if intervals:
            self.revenue_repo.upsert_intervals(intervals)
            logger.info("Calculated revenue for %d export intervals", len(intervals))

        # Import cost
        import_meters = self.meter_repo.get_import_intervals(start, end)
        import_tariffs = self.tariff_repo.get_import_rates(start, end)
        import_intervals = self.revenue_calculator.calculate_import_cost_batch(
            import_meters, import_tariffs
        )
        if import_intervals:
            self.revenue_repo.upsert_import_cost_intervals(import_intervals)
            logger.info("Calculated import cost for %d intervals", len(import_intervals))

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

        # Compute tariff data age for freshness check
        tariff_data_age: float | None = None
        latest_slot = self.tariff_repo.get_latest_export_slot()
        if latest_slot:
            tariff_data_age = (now - latest_slot.fetched_at).total_seconds() / 60.0

        # HA state freshness check — treat stale data as unavailable
        freshness_limit = self.settings.thresholds.data_freshness_limit_minutes
        if ha_state:
            ha_age = (now - ha_state.timestamp).total_seconds() / 60.0
            if ha_age > freshness_limit:
                logger.warning(
                    "HA state is %.0f minutes old (limit %.0f), treating as unavailable",
                    ha_age, freshness_limit,
                )
                ha_state = None

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

        # Calculate solar-aware overnight charge target
        overnight_target: float | None = None
        self._current_overnight_target = None
        self._solar_forecast_kwh: float | None = None
        ic = self.settings.inverter_control
        if ic.solar_overnight_enabled:
            target_date = (
                (now + timedelta(days=1)).date()
                if now.hour >= 16
                else now.date()
            )

            seasonal_max = (
                ic.solar_months_max_soc_pct
                if target_date.month in range(3, 10)
                else ic.winter_max_soc_pct
            )

            # Check solar forecast — skip overnight reduction if poor forecast
            forecast_entity = (
                self.settings.home_assistant.entity_ids.solar_forecast_tomorrow
                if now.hour >= 16
                else self.settings.home_assistant.entity_ids.solar_forecast_today
            )
            solar_forecast_kwh = self.ha_state_ingester.get_solar_forecast_kwh(
                forecast_entity
            )
            self._solar_forecast_kwh = solar_forecast_kwh

            if (
                solar_forecast_kwh is not None
                and solar_forecast_kwh < ic.solar_forecast_minimum_kwh
            ):
                overnight_target = seasonal_max
                logger.info(
                    "Overnight target: %.0f%% (forecast %.1f kWh < %.1f kWh minimum)",
                    seasonal_max * 100,
                    solar_forecast_kwh,
                    ic.solar_forecast_minimum_kwh,
                )
            else:
                # Rate-based overnight target calculation
                solar_start = datetime(
                    target_date.year, target_date.month, target_date.day,
                    11, 0, tzinfo=timezone.utc,
                )
                solar_end = datetime(
                    target_date.year, target_date.month, target_date.day,
                    16, 0, tzinfo=timezone.utc,
                )
                solar_hour_rates = self.tariff_repo.get_export_rates(
                    solar_start, solar_end
                )

                if solar_hour_rates:
                    from octopus_export_optimizer.calculations.overnight_target import (
                        calculate_overnight_charge_target,
                    )
                    ot_result = calculate_overnight_charge_target(
                        solar_hour_rates=solar_hour_rates,
                        night_import_rate_pence=self.settings.thresholds.cheap_import_threshold_pence,
                        battery_capacity_kwh=self.settings.battery.capacity_kwh,
                        minimum_overnight_soc_pct=ic.minimum_overnight_soc_pct,
                        seasonal_max_soc_pct=seasonal_max,
                        solar_charge_kwh_per_slot=ic.solar_charge_kwh_per_slot,
                    )
                    if ot_result:
                        overnight_target = ot_result.target_soc_pct
                        self._current_overnight_target = ot_result
                        logger.info(
                            "Overnight target: %.0f%% (%d solar slots, "
                            "%.1fp est. savings, %.1f kWh headroom)",
                            ot_result.target_soc_pct * 100,
                            ot_result.solar_opportunity_slots,
                            ot_result.estimated_savings_pence,
                            ot_result.headroom_kwh,
                        )
                else:
                    # No rate data — still apply seasonal cap
                    overnight_target = seasonal_max
                    logger.info(
                        "Overnight target: %.0f%% (seasonal baseline, no rate data)",
                        seasonal_max * 100,
                    )

        snapshot = self.engine.build_snapshot(
            now=now,
            current_export=current_export,
            upcoming_exports=upcoming,
            current_import=current_import,
            ha_state=ha_state,
            remaining_generation=remaining_gen,
            minimum_soc_override=reserve,
            tariff_data_age_minutes=tariff_data_age,
            overnight_charge_target_pct=overnight_target,
        )
        # Build export plan if enabled and there's exportable energy
        export_plan = None
        if (
            self.settings.inverter_control.export_planner_enabled
            and snapshot.exportable_battery_kwh
            and snapshot.exportable_battery_kwh > 0
        ):
            export_plan = build_export_plan(
                now=now,
                upcoming_slots=upcoming_12h,
                exportable_kwh=snapshot.exportable_battery_kwh,
                export_threshold_pence=self.settings.thresholds.export_now_threshold_pence,
                max_discharge_kw=self.settings.inverter_control.max_discharge_kw,
                battery_capacity_kwh=self.settings.battery.capacity_kwh,
                round_trip_efficiency=self.settings.battery.round_trip_efficiency,
            )

        self._current_export_plan = export_plan
        if export_plan:
            logger.info(
                "Export plan: %d slots, %.2f kWh at %.3f kW",
                len(export_plan.planned_slots),
                export_plan.total_planned_kwh,
                export_plan.discharge_kw,
            )

        # Build charge plan: identify low-rate solar windows for charging
        charge_plan = None
        if (
            self.settings.inverter_control.export_planner_enabled
            and snapshot.battery_headroom_kwh
            and snapshot.battery_headroom_kwh > 0.1
        ):
            charge_plan = build_charge_plan(
                now=now,
                upcoming_slots=upcoming_12h,
                export_plan=export_plan,
                battery_headroom_kwh=snapshot.battery_headroom_kwh,
                round_trip_efficiency=self.settings.battery.round_trip_efficiency,
                export_threshold_pence=self.settings.thresholds.export_now_threshold_pence,
                solar_charge_kwh_per_slot=ic.solar_charge_kwh_per_slot,
            )

        self._current_charge_plan = charge_plan
        if charge_plan:
            logger.info(
                "Charge plan: %d slots, breakeven %.1fp (storing for %.1fp discharge)",
                len(charge_plan.charging_slots),
                charge_plan.breakeven_rate_pence,
                charge_plan.target_discharge_rate_pence,
            )

        recommendation = self.engine.evaluate(
            snapshot, upcoming_12h, export_plan=export_plan, charge_plan=charge_plan
        )
        self.recommendation_repo.save(recommendation, snapshot)
        self._last_target_charge_kw = recommendation.target_charge_kw
        logger.info(
            "Snapshot: soc=%.0f%%, export_rate=%s, pv=%.1fkW, exportable=%.2fkWh | "
            "target_max_soc=%s, charge_kw=%s, discharge_kw=%s",
            snapshot.battery_soc_pct or 0,
            snapshot.current_export_rate_pence,
            snapshot.pv_power_kw or 0,
            snapshot.exportable_battery_kwh or 0,
            recommendation.target_max_soc,
            recommendation.target_charge_kw,
            recommendation.target_discharge_kw,
        )

        # Execute inverter control
        if self.inverter_controller:
            self._run_safe(
                "inverter_control",
                lambda: self.inverter_controller.execute(recommendation),
            )

    def job_backup_database(self) -> None:
        """Create a backup of the SQLite database."""
        create_backup(
            self.settings.db_path,
            self.settings.backup_dir,
            self.settings.backup_retention_days,
        )

    def job_aggregate_summaries(self) -> None:
        """Aggregate revenue into day/month summaries."""
        now = datetime.now(timezone.utc)
        today = date.today()

        # Today + recent days (rebuilds any missing day summaries)
        for days_ago in range(min(7, today.day), -1, -1):
            target_date = today - timedelta(days=days_ago)
            day_start, day_end = self.aggregator.day_boundaries(target_date)
            day_intervals = self.revenue_repo.get_intervals(day_start, day_end)
            day_import = self.revenue_repo.get_import_cost_intervals(
                day_start, day_end
            )
            day_summary = self.aggregator.aggregate(
                day_intervals, "day", target_date.isoformat(),
                import_cost_intervals=day_import,
            )
            if day_summary.total_intervals > 0 or target_date == today:
                self.revenue_repo.upsert_summary(day_summary)

        # This month
        month_start, month_end = self.aggregator.month_boundaries(
            today.year, today.month
        )
        month_intervals = self.revenue_repo.get_intervals(month_start, month_end)
        month_import = self.revenue_repo.get_import_cost_intervals(month_start, month_end)
        month_key = f"{today.year}-{today.month:02d}"
        month_summary = self.aggregator.aggregate(
            month_intervals, "month", month_key,
            import_cost_intervals=month_import,
        )
        self.revenue_repo.upsert_summary(month_summary)

        # Rolling 7d and 30d
        for days, period_type in [(7, "rolling_7d"), (30, "rolling_30d")]:
            r_start, r_end = self.aggregator.rolling_boundaries(days, now)
            r_intervals = self.revenue_repo.get_intervals(r_start, r_end)
            r_import = self.revenue_repo.get_import_cost_intervals(r_start, r_end)
            r_summary = self.aggregator.aggregate(
                r_intervals, period_type, f"last_{days}d",
                import_cost_intervals=r_import,
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

        # Publish today's full rate schedule for charting
        day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        day_end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)
        today_export_rates = self.tariff_repo.get_export_rates(day_start, day_end)
        today_import_rates = self.tariff_repo.get_import_rates(day_start, day_end)
        self.mqtt_publisher.publish_rate_schedule(
            today_export_rates,
            today_import_rates if today_import_rates else None,
            now,
        )

        # Publish forward-looking rate schedule (now → 48h)
        upcoming_end = now + timedelta(hours=48)
        upcoming_export = self.tariff_repo.get_export_rates(now, upcoming_end)
        upcoming_import = self.tariff_repo.get_import_rates(now, upcoming_end)
        planned_starts: dict[str, tuple[float, float]] | None = None
        if self._current_export_plan:
            planned_starts = {
                s.interval_start.isoformat(): (s.discharge_kw, s.expected_kwh)
                for s in self._current_export_plan.planned_slots
            }
        charging_starts: set[str] | None = None
        charge_plan = getattr(self, "_current_charge_plan", None)
        if charge_plan:
            charging_starts = {
                s.interval_start.isoformat()
                for s in charge_plan.charging_slots
            }
        self.mqtt_publisher.publish_upcoming_rate_schedule(
            upcoming_export,
            upcoming_import if upcoming_import else None,
            now,
            planned_starts=planned_starts,
            charging_starts=charging_starts,
        )

        rec = self.recommendation_repo.get_latest()
        self.mqtt_publisher.publish_recommendation(rec)
        self.mqtt_publisher.publish_export_plan(rec, self._current_export_plan, now)
        self.mqtt_publisher.publish_overnight_target(
            getattr(self, "_current_overnight_target", None),
            solar_forecast_kwh=getattr(self, "_solar_forecast_kwh", None),
            forecast_minimum_kwh=self.settings.inverter_control.solar_forecast_minimum_kwh,
            charge_power_kw=getattr(self, "_last_target_charge_kw", None),
        )
        self.mqtt_publisher.publish_charge_plan(
            getattr(self, "_current_charge_plan", None)
        )

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
            day_import_tariffs = self.tariff_repo.get_import_rates(day_start, now)
            estimate = estimate_revenue(
                day_snapshots, day_tariffs, self.settings.thresholds, now,
                import_tariff_slots=day_import_tariffs if day_import_tariffs else None,
            )
            if estimate.export_kwh > 0:
                from octopus_export_optimizer.models.revenue import RevenueSummary

                flat_rate = self.settings.thresholds.get_flat_rate_for_date(today)
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
                    flat_export_kwh=estimate.flat_export_kwh,
                    avg_flat_rate_pence=flat_rate,
                    import_cost_pence=estimate.import_cost_pence,
                    total_import_kwh=estimate.import_kwh,
                    net_revenue_pence=estimate.net_revenue_pence,
                    charging_opportunity_cost_pence=estimate.charging_opportunity_cost_pence,
                    true_profit_pence=estimate.true_profit_pence,
                )
                today_is_estimated = True
                # Persist estimated summary so daily history chart has data
                self.revenue_repo.upsert_summary(today_summary)
                logger.debug(
                    "Using estimated today revenue: %.1fp from %.2f kWh (%d snapshots)",
                    estimate.agile_revenue_pence,
                    estimate.export_kwh,
                    estimate.snapshot_count,
                )

                # Update month summary to include today's estimated data
                if month_summary is None or month_summary.total_export_kwh == 0:
                    # No settled month data — use today's estimate as the month
                    month_summary = RevenueSummary(
                        period_type="month",
                        period_key=month_key,
                        total_export_kwh=today_summary.total_export_kwh,
                        agile_revenue_pence=today_summary.agile_revenue_pence,
                        flat_revenue_pence=today_summary.flat_revenue_pence,
                        uplift_pence=today_summary.uplift_pence,
                        avg_realised_rate_pence=today_summary.avg_realised_rate_pence,
                        intervals_above_flat=0,
                        total_intervals=0,
                        calculated_at=now,
                        flat_export_kwh=today_summary.flat_export_kwh,
                        avg_flat_rate_pence=today_summary.avg_flat_rate_pence,
                        import_cost_pence=today_summary.import_cost_pence,
                        total_import_kwh=today_summary.total_import_kwh,
                        net_revenue_pence=today_summary.net_revenue_pence,
                        charging_opportunity_cost_pence=today_summary.charging_opportunity_cost_pence,
                        true_profit_pence=today_summary.true_profit_pence,
                    )
                else:
                    # Settled month data exists — add today's estimate on top
                    m = month_summary
                    t = today_summary
                    combined_kwh = m.total_export_kwh + t.total_export_kwh
                    combined_agile = m.agile_revenue_pence + t.agile_revenue_pence
                    combined_flat = m.flat_revenue_pence + t.flat_revenue_pence
                    combined_import = m.import_cost_pence + t.import_cost_pence
                    combined_import_kwh = m.total_import_kwh + t.total_import_kwh
                    combined_opp = m.charging_opportunity_cost_pence + t.charging_opportunity_cost_pence
                    combined_net = combined_agile - combined_import
                    month_summary = month_summary.model_copy(update={
                        "total_export_kwh": round(combined_kwh, 4),
                        "agile_revenue_pence": round(combined_agile, 4),
                        "flat_revenue_pence": round(combined_flat, 4),
                        "uplift_pence": round(combined_agile - combined_flat, 4),
                        "avg_realised_rate_pence": round(
                            combined_agile / combined_kwh if combined_kwh > 0 else 0.0, 2
                        ),
                        "import_cost_pence": round(combined_import, 4),
                        "total_import_kwh": round(combined_import_kwh, 4),
                        "net_revenue_pence": round(combined_net, 4),
                        "charging_opportunity_cost_pence": round(combined_opp, 4),
                        "true_profit_pence": round(combined_net - combined_opp, 4),
                        "flat_export_kwh": round(combined_kwh, 4),
                        "avg_flat_rate_pence": t.avg_flat_rate_pence or m.avg_flat_rate_pence,
                        "calculated_at": now,
                    })

        self.mqtt_publisher.publish_revenue(
            today_summary, month_summary, today_is_estimated=today_is_estimated,
        )

        # Publish rolling summaries
        rolling_7d = self.revenue_repo.get_summary("rolling_7d", "last_7d")
        rolling_30d = self.revenue_repo.get_summary("rolling_30d", "last_30d")
        self.mqtt_publisher.publish_rolling_revenue(rolling_7d, rolling_30d)

        daily_history = self.revenue_repo.get_daily_summaries(30)
        self.mqtt_publisher.publish_daily_revenue_history(daily_history)

        monthly_history = self.revenue_repo.get_monthly_summaries(12)
        self.mqtt_publisher.publish_monthly_revenue_history(monthly_history)

        ha_state = self.ha_state_repo.get_latest()
        self.mqtt_publisher.publish_ha_state(ha_state)

        self.mqtt_publisher.publish_service_status(now)

        # Publish data freshness / health
        latest_slot = self.tariff_repo.get_latest_export_slot()
        tariff_age = (
            (now - latest_slot.fetched_at).total_seconds() / 60.0
            if latest_slot else None
        )
        ha_age = (
            (now - ha_state.timestamp).total_seconds() / 60.0
            if ha_state else None
        )
        self.mqtt_publisher.publish_data_freshness(
            tariff_age, ha_age, self.settings.thresholds.data_freshness_limit_minutes,
        )

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
                evening_reserve_soc=(
                    max(self.settings.thresholds.reserve_soc_floor, reserve)
                    if reserve is not None
                    else None
                ),
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
        self.scheduler.shutdown(wait=True)
        if hasattr(self, "_api_server"):
            self._api_server.shutdown()
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
