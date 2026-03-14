"""Revenue calculation service."""

from __future__ import annotations

from datetime import datetime, timezone

from octopus_export_optimizer.config.settings import ThresholdSettings
from octopus_export_optimizer.models.meter import MeterInterval
from octopus_export_optimizer.models.revenue import ImportCostInterval, RevenueInterval
from octopus_export_optimizer.models.tariff import TariffSlot


class RevenueCalculator:
    """Calculates per-interval revenue and flat-rate counterfactual.

    Revenue is calculated by joining metered export data with
    the actual Agile rate for that interval. The flat counterfactual
    uses the date-effective flat rate that was active at the time.
    """

    def __init__(self, thresholds: ThresholdSettings) -> None:
        self.thresholds = thresholds

    def calculate_interval(
        self,
        meter: MeterInterval,
        tariff: TariffSlot,
        now: datetime | None = None,
    ) -> RevenueInterval:
        """Calculate revenue for a single half-hour interval.

        Args:
            meter: Metered export data for the interval.
            tariff: Agile export rate for the interval.
            now: Override for calculated_at timestamp.
        """
        agile_revenue = meter.kwh * tariff.rate_inc_vat_pence
        flat_rate = self.thresholds.get_flat_rate_for_date(
            meter.interval_start.date()
        )
        flat_revenue = meter.kwh * flat_rate
        uplift = agile_revenue - flat_revenue

        return RevenueInterval(
            interval_start=meter.interval_start,
            export_kwh=meter.kwh,
            agile_rate_pence=tariff.rate_inc_vat_pence,
            agile_revenue_pence=round(agile_revenue, 4),
            flat_rate_pence=flat_rate,
            flat_revenue_pence=round(flat_revenue, 4),
            uplift_pence=round(uplift, 4),
            calculated_at=now or datetime.now(timezone.utc),
            flat_export_kwh=round(meter.kwh, 4),
        )

    def calculate_batch(
        self,
        meters: list[MeterInterval],
        tariffs: list[TariffSlot],
    ) -> list[RevenueInterval]:
        """Calculate revenue for a batch of intervals.

        Joins meters and tariffs by interval_start. Intervals
        without a matching tariff are skipped.
        """
        tariff_map = {t.interval_start: t for t in tariffs}
        now = datetime.now(timezone.utc)
        results = []

        for meter in meters:
            tariff = tariff_map.get(meter.interval_start)
            if tariff is None:
                continue
            results.append(
                self.calculate_interval(meter, tariff, now=now)
            )

        return results

    def calculate_import_cost_batch(
        self,
        meters: list[MeterInterval],
        tariffs: list[TariffSlot],
    ) -> list[ImportCostInterval]:
        """Calculate import cost for a batch of intervals.

        Joins import meters and import tariffs by interval_start.
        Intervals without a matching tariff are skipped.
        """
        tariff_map = {t.interval_start: t for t in tariffs}
        now = datetime.now(timezone.utc)
        results = []

        for meter in meters:
            tariff = tariff_map.get(meter.interval_start)
            if tariff is None:
                continue
            cost = meter.kwh * tariff.rate_inc_vat_pence
            results.append(
                ImportCostInterval(
                    interval_start=meter.interval_start,
                    import_kwh=meter.kwh,
                    import_rate_pence=tariff.rate_inc_vat_pence,
                    import_cost_pence=round(cost, 4),
                    calculated_at=now,
                )
            )

        return results
