"""Demo mode — runs the full pipeline with synthetic data.

No API keys, no MQTT, no Home Assistant required.
Generates realistic sample tariff, meter, and battery data
then runs the entire calculation and recommendation pipeline.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import date, datetime, timedelta, timezone

from octopus_export_optimizer.calculations.aggregator import Aggregator
from octopus_export_optimizer.calculations.revenue_calculator import RevenueCalculator
from octopus_export_optimizer.calculations.solar_profile import SolarProfile
from octopus_export_optimizer.config.settings import AppSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.meter import MeterInterval
from octopus_export_optimizer.models.tariff import TariffSlot
from octopus_export_optimizer.recommendation.engine import RecommendationEngine
from octopus_export_optimizer.storage.database import Database
from octopus_export_optimizer.storage.ha_state_repo import HaStateRepo
from octopus_export_optimizer.storage.meter_repo import MeterRepo
from octopus_export_optimizer.storage.recommendation_repo import RecommendationRepo
from octopus_export_optimizer.storage.revenue_repo import RevenueRepo
from octopus_export_optimizer.storage.tariff_repo import TariffRepo

logger = logging.getLogger(__name__)


def generate_agile_export_rates(
    start: datetime, hours: int = 48
) -> list[TariffSlot]:
    """Generate realistic Agile export rates.

    Models the typical Agile pattern:
    - Low overnight (2-6p)
    - Rising morning (8-12p)
    - Peak midday/afternoon (12-25p, occasionally higher)
    - Evening shoulder (8-15p)
    - Occasional negative rates in early hours
    """
    slots = []
    now = datetime.now(timezone.utc)
    num_slots = hours * 2  # half-hour slots

    for i in range(num_slots):
        slot_start = start + timedelta(minutes=30 * i)
        hour = slot_start.hour + slot_start.minute / 60.0

        # Base rate curve (pence/kWh)
        if 0 <= hour < 4:
            base = 3.0 + random.gauss(0, 1.5)
        elif 4 <= hour < 7:
            base = 5.0 + random.gauss(0, 2.0)
        elif 7 <= hour < 10:
            base = 10.0 + random.gauss(0, 3.0)
        elif 10 <= hour < 14:
            base = 18.0 + random.gauss(0, 5.0)
        elif 14 <= hour < 16:
            base = 15.0 + random.gauss(0, 4.0)
        elif 16 <= hour < 19:
            base = 22.0 + random.gauss(0, 6.0)  # Evening peak
        elif 19 <= hour < 21:
            base = 12.0 + random.gauss(0, 3.0)
        else:
            base = 6.0 + random.gauss(0, 2.0)

        # Occasional spikes/dips
        if random.random() < 0.05:
            base *= random.uniform(1.5, 2.5)  # Price spike
        if random.random() < 0.03:
            base = random.uniform(-5.0, 0.0)  # Negative rate

        rate = round(max(-10.0, base), 2)

        slots.append(TariffSlot(
            interval_start=slot_start,
            interval_end=slot_start + timedelta(minutes=30),
            rate_inc_vat_pence=rate,
            tariff_type="export",
            product_code="DEMO-AGILE-EXPORT",
            provenance="actual" if slot_start < now else "published",
            fetched_at=now,
        ))

    return slots


def generate_import_rates(
    start: datetime, hours: int = 48
) -> list[TariffSlot]:
    """Generate Intelligent Octopus Go import rates.

    Off-peak (23:30-05:30): 7.5p
    Peak: variable ~25-35p
    """
    slots = []
    now = datetime.now(timezone.utc)

    for i in range(hours * 2):
        slot_start = start + timedelta(minutes=30 * i)
        hour = slot_start.hour + slot_start.minute / 60.0

        if 23.5 <= hour or hour < 5.5:
            rate = 7.5
        else:
            rate = 28.0 + random.gauss(0, 3.0)

        slots.append(TariffSlot(
            interval_start=slot_start,
            interval_end=slot_start + timedelta(minutes=30),
            rate_inc_vat_pence=round(rate, 2),
            tariff_type="import",
            product_code="DEMO-INTELLI-GO",
            provenance="actual" if slot_start < now else "published",
            fetched_at=now,
        ))

    return slots


def generate_export_meter_data(
    start: datetime, hours: int = 24, solar_profile: SolarProfile | None = None
) -> list[MeterInterval]:
    """Generate realistic export meter data based on solar profile."""
    intervals = []
    now = datetime.now(timezone.utc)

    for i in range(hours * 2):
        slot_start = start + timedelta(minutes=30 * i)

        if solar_profile:
            # Use solar profile for generation estimate
            gen_kw = solar_profile.estimated_generation_kw(
                slot_start, cloud_factor=random.uniform(0.4, 1.0)
            )
        else:
            hour = slot_start.hour
            if 6 <= hour <= 20:
                gen_kw = random.uniform(0.5, 8.0) * math.sin(
                    math.pi * (hour - 6) / 14
                )
            else:
                gen_kw = 0.0

        # Assume ~1.5kW average load, export is generation minus load
        load_kw = random.uniform(0.8, 2.5)
        export_kw = max(0.0, gen_kw - load_kw)
        export_kwh = export_kw * 0.5  # half-hour interval

        intervals.append(MeterInterval(
            interval_start=slot_start,
            interval_end=slot_start + timedelta(minutes=30),
            kwh=round(export_kwh, 3),
            direction="export",
            fetched_at=now,
        ))

    return intervals


def run_demo() -> None:
    """Run the full optimizer pipeline with synthetic demo data."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = AppSettings()
    random.seed(42)  # Reproducible demo data

    print()
    print("=" * 70)
    print("  OCTOPUS EXPORT OPTIMIZER - DEMO MODE")
    print("=" * 70)
    print()
    print("Running full pipeline with synthetic data.")
    print("No API keys or external services required.")
    print()

    # Use in-memory database for demo
    db = Database(":memory:")
    db.connect()

    tariff_repo = TariffRepo(db)
    meter_repo = MeterRepo(db)
    ha_state_repo = HaStateRepo(db)
    revenue_repo = RevenueRepo(db)
    recommendation_repo = RecommendationRepo(db)

    solar_profile = SolarProfile(settings.solar)
    revenue_calc = RevenueCalculator(settings.thresholds)
    aggregator = Aggregator()
    engine = RecommendationEngine(settings.thresholds, settings.battery)

    now = datetime.now(timezone.utc)
    yesterday_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ── Step 1: Generate and store tariff data ──────────────────
    print("Step 1: Generating tariff data...")
    export_rates = generate_agile_export_rates(yesterday_start, hours=48)
    import_rates = generate_import_rates(yesterday_start, hours=48)
    tariff_repo.upsert_slots(export_rates)
    tariff_repo.upsert_slots(import_rates)
    print(f"  Stored {len(export_rates)} export rate slots")
    print(f"  Stored {len(import_rates)} import rate slots")

    # ── Step 2: Generate and store meter data ───────────────────
    print("\nStep 2: Generating export meter data (yesterday)...")
    meter_data = generate_export_meter_data(
        yesterday_start, hours=24, solar_profile=solar_profile
    )
    meter_repo.upsert_intervals(meter_data)
    total_export = sum(m.kwh for m in meter_data)
    print(f"  Stored {len(meter_data)} meter intervals")
    print(f"  Total exported yesterday: {total_export:.2f} kWh")

    # ── Step 3: Generate HA state snapshot ──────────────────────
    print("\nStep 3: Simulating current Home Assistant state...")
    ha_state = HaStateSnapshot(
        timestamp=now,
        battery_soc_pct=68.0,
        pv_power_kw=solar_profile.estimated_generation_kw(now, cloud_factor=0.7),
        feed_in_kw=max(0.0, solar_profile.estimated_generation_kw(now, cloud_factor=0.7) - 1.5),
        load_power_kw=1.5,
        grid_consumption_kw=0.0,
        battery_charge_kw=0.5,
        battery_discharge_kw=0.0,
        work_mode="Feed-in First",
        max_soc=100.0,
        min_soc=10.0,
    )
    ha_state_repo.insert(ha_state)
    print(f"  Battery SoC: {ha_state.battery_soc_pct:.0f}%")
    print(f"  PV Power: {ha_state.pv_power_kw:.2f} kW")
    print(f"  Feed-in: {ha_state.feed_in_kw:.2f} kW")
    print(f"  Load: {ha_state.load_power_kw:.2f} kW")

    # ── Step 4: Calculate revenue ───────────────────────────────
    print("\nStep 4: Calculating revenue...")
    yesterday_end = today_start
    meters = meter_repo.get_export_intervals(yesterday_start, yesterday_end)
    tariffs = tariff_repo.get_export_rates(yesterday_start, yesterday_end)
    revenue_intervals = revenue_calc.calculate_batch(meters, tariffs)
    revenue_repo.upsert_intervals(revenue_intervals)
    print(f"  Calculated revenue for {len(revenue_intervals)} intervals")

    # ── Step 5: Aggregate ───────────────────────────────────────
    print("\nStep 5: Aggregating revenue summaries...")
    yesterday = (now - timedelta(days=1)).date()
    day_start, day_end = aggregator.day_boundaries(yesterday)
    day_intervals = revenue_repo.get_intervals(day_start, day_end)
    day_summary = aggregator.aggregate(day_intervals, "day", yesterday.isoformat())
    revenue_repo.upsert_summary(day_summary)

    # ── Step 6: Generate recommendation ─────────────────────────
    print("\nStep 6: Generating recommendation...")
    current_export = tariff_repo.get_current_export_rate(now)
    upcoming = tariff_repo.get_upcoming_export_rates(
        now, settings.thresholds.look_ahead_hours
    )
    current_import = tariff_repo.get_current_import_rate(now)
    remaining_gen = solar_profile.remaining_generation_factor(now)

    snapshot = engine.build_snapshot(
        now=now,
        current_export=current_export,
        upcoming_exports=upcoming,
        current_import=current_import,
        ha_state=ha_state,
        remaining_generation=remaining_gen,
    )
    recommendation = engine.evaluate(snapshot)
    recommendation_repo.save(recommendation, snapshot)

    # ── Output Results ──────────────────────────────────────────
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)

    print()
    print("-- CURRENT RATES -------------------------------------------------")
    if current_export:
        print(f"  Export rate:  {current_export.rate_inc_vat_pence:.1f}p/kWh")
        print(f"  Valid:        {current_export.interval_start.strftime('%H:%M')} - {current_export.interval_end.strftime('%H:%M')} UTC")
    else:
        print("  Export rate:  (no data)")

    if current_import:
        print(f"  Import rate:  {current_import.rate_inc_vat_pence:.1f}p/kWh")

    if upcoming:
        best = max(upcoming, key=lambda s: s.rate_inc_vat_pence)
        print(f"  Best upcoming: {best.rate_inc_vat_pence:.1f}p/kWh at {best.interval_start.strftime('%H:%M')} UTC")

    print()
    print("-- RECOMMENDATION ------------------------------------------------")
    print(f"  State:       {recommendation.state.value}")
    print(f"  Reason:      {recommendation.reason_code.value}")
    print(f"  Mode:        {'Battery-aware' if recommendation.battery_aware else 'Tariff-only'}")
    print(f"  Explanation: {recommendation.explanation}")

    print()
    print("-- YESTERDAY'S REVENUE -------------------------------------------")
    print(f"  Exported:      {day_summary.total_export_kwh:.2f} kWh")
    print(f"  Agile revenue: {day_summary.agile_revenue_pence:.1f}p (GBP {day_summary.agile_revenue_gbp:.2f})")
    print(f"  Flat baseline: {day_summary.flat_revenue_pence:.1f}p (GBP {day_summary.flat_revenue_gbp:.2f})")
    print(f"  Uplift:        {day_summary.uplift_pence:.1f}p (GBP {day_summary.uplift_gbp:.2f})")
    if day_summary.total_export_kwh > 0:
        print(f"  Avg rate:      {day_summary.avg_realised_rate_pence:.1f}p/kWh")
    print(f"  Intervals above flat: {day_summary.intervals_above_flat}/{day_summary.total_intervals}")

    print()
    print("-- BATTERY STATE -------------------------------------------------")
    soc_frac = ha_state.battery_soc_pct / 100.0
    current_kwh = soc_frac * settings.battery.capacity_kwh
    reserve_kwh = settings.thresholds.reserve_soc_floor * settings.battery.capacity_kwh
    exportable = max(0, current_kwh - reserve_kwh)
    headroom = settings.battery.capacity_kwh - current_kwh
    print(f"  SoC:           {ha_state.battery_soc_pct:.0f}% ({current_kwh:.1f} kWh)")
    print(f"  Exportable:    {exportable:.1f} kWh (above {settings.thresholds.reserve_soc_floor:.0%} reserve)")
    print(f"  Headroom:      {headroom:.1f} kWh")

    print()
    print("-- SOLAR PROFILE -------------------------------------------------")
    gen_now = solar_profile.estimated_generation_kw(now)
    print(f"  Current est:   {gen_now:.2f} kW (clear sky)")
    print(f"  Remaining gen: {remaining_gen:.0%} of day remaining")
    print(f"  Array: 7.14kWp N + 3.06kWp S + 0.88kWp E + 0.88kWp W")

    # Show a mini rate chart for the next few hours
    print()
    print("-- UPCOMING EXPORT RATES -----------------------------------------")
    if upcoming:
        for slot in upcoming[:8]:
            bar_len = max(0, int(slot.rate_inc_vat_pence / 2))
            bar = "#" * bar_len
            marker = " << NOW" if slot.interval_start <= now < slot.interval_end else ""
            print(f"  {slot.interval_start.strftime('%H:%M')} | {slot.rate_inc_vat_pence:6.1f}p |{bar}{marker}")

    print()
    print("=" * 70)
    print("  Demo complete. In production, this data comes from the Octopus API,")
    print("  Home Assistant, and runs continuously on a schedule.")
    print("=" * 70)
    print()

    db.close()
