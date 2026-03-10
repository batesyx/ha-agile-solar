"""Application configuration via Pydantic Settings."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings


class OctopusApiSettings(BaseModel):
    """Octopus Energy API credentials and tariff identifiers."""

    api_key: SecretStr
    account_number: str
    export_mpan: str
    export_serial: str
    import_mpan: str = ""
    import_serial: str = ""
    export_product_code: str = "AGILE-OUTGOING-19-05-13"
    export_tariff_code: str = "E-1R-AGILE-OUTGOING-19-05-13-B"
    import_product_code: str = "INTELLI-BB-VAR-23-03-01"
    import_tariff_code: str = "E-1R-INTELLI-BB-VAR-23-03-01-B"


class BatterySettings(BaseModel):
    """Battery system parameters."""

    capacity_kwh: float = 11.52
    round_trip_efficiency: float = 0.90


class PanelArray(BaseModel):
    """A single solar panel array with orientation, tilt, and capacity."""

    orientation: float  # degrees from north, clockwise (0=N, 90=E, 180=S, 270=W)
    tilt: float = 35.0  # degrees from horizontal
    kwp: float


class SolarSettings(BaseModel):
    """Solar array configuration."""

    panels: list[PanelArray] = [
        PanelArray(orientation=0, tilt=35, kwp=7.14),
        PanelArray(orientation=180, tilt=35, kwp=3.06),
        PanelArray(orientation=90, tilt=35, kwp=0.88),
        PanelArray(orientation=270, tilt=35, kwp=0.88),
    ]
    latitude: float = 52.0
    longitude: float = -1.0


class FlatRateConfig(BaseModel):
    """A flat export rate effective for a date range.

    Used to calculate counterfactual revenue: "what would this export
    have earned at the fixed rate that was available at the time?"
    """

    rate_pence: float = 12.0
    effective_from: date = date(2024, 1, 1)
    effective_to: date | None = None  # None means ongoing/current


class ThresholdSettings(BaseModel):
    """Decision thresholds and strategy parameters."""

    flat_export_rates: list[FlatRateConfig] = [FlatRateConfig()]
    export_now_threshold_pence: float = 15.0
    better_slot_delta_pence: float = 3.0
    look_ahead_hours: float = 4.0
    reserve_soc_floor: float = 0.20
    minimum_soc_for_export: float = 0.35
    high_soc_hold_threshold: float = 0.80
    minimum_meaningful_export_kw: float = 0.1
    battery_round_trip_efficiency: float = 0.90
    allow_import_arbitrage: bool = False
    cheap_import_threshold_pence: float = 7.5
    data_freshness_limit_minutes: float = 60.0

    def get_flat_rate_for_date(self, target_date: date) -> float:
        """Return the flat export rate in pence that was effective on the given date."""
        for rate_config in sorted(
            self.flat_export_rates, key=lambda r: r.effective_from, reverse=True
        ):
            if target_date >= rate_config.effective_from:
                if rate_config.effective_to is None or target_date <= rate_config.effective_to:
                    return rate_config.rate_pence
        # Fallback to the earliest configured rate
        if self.flat_export_rates:
            return self.flat_export_rates[0].rate_pence
        return 12.0


class InverterControlSettings(BaseModel):
    """Inverter control parameters."""

    enabled: bool = False
    min_command_interval_seconds: int = 300
    cheap_rate_start_hour: float = 23.5  # 23:30
    cheap_rate_end_hour: float = 5.5  # 05:30
    high_export_threshold_for_full_charge: float = 20.0  # p/kWh
    default_evening_load_kw: float = 1.2
    fallback_on_insufficient_data: str = "self_use"  # "self_use" or "none"
    full_charge_lead_time_hours: float = 1.5  # hours before peak to raise max_soc to 100%
    export_planner_enabled: bool = False  # Enable multi-slot export planning
    max_discharge_kw: float = 5.0  # ~24A, under 25A continuous target


class HaEntityIds(BaseModel):
    """Home Assistant entity IDs for the Fox ESS inverter."""

    battery_soc: str = "sensor.fox_ess_battery_soc"
    pv_power: str = "sensor.fox_ess_pv_power"
    feed_in_power: str = "sensor.fox_ess_feed_in_power"
    load_power: str = "sensor.fox_ess_load_power"
    grid_consumption: str = "sensor.fox_ess_grid_consumption"
    battery_charge_power: str = "sensor.fox_ess_bat_charge_power"
    battery_discharge_power: str = "sensor.fox_ess_bat_discharge_power"
    work_mode: str = "sensor.fox_ess_work_mode"
    max_soc: str = "number.fox_ess_max_soc"
    min_soc: str = "number.fox_ess_min_soc"
    force_charge_power: str = "number.fox_ess_force_charge_power"
    force_discharge_power: str = "number.fox_ess_force_discharge_power"


class HaSettings(BaseModel):
    """Home Assistant connection settings."""

    url: str = "http://homeassistant.local:8123"
    token: SecretStr = SecretStr("")
    entity_ids: HaEntityIds = HaEntityIds()
    poll_interval_seconds: int = 60


class MqttSettings(BaseModel):
    """MQTT broker connection settings."""

    broker: str = "localhost"
    port: int = 1883
    username: str = ""
    password: SecretStr = SecretStr("")
    topic_prefix: str = "octopus_export_optimizer"
    ha_discovery_prefix: str = "homeassistant"


class ScheduleSettings(BaseModel):
    """Job scheduling intervals."""

    tariff_ingestion_minutes: int = 30
    meter_ingestion_minutes: int = 30
    ha_poll_seconds: int = 60
    revenue_calculation_minutes: int = 30
    recommendation_seconds: int = 60
    aggregation_minutes: int = 15
    publish_seconds: int = 60


class AppSettings(BaseSettings):
    """Root application settings.

    Can be loaded from environment variables (OCTO_ prefix),
    or from a config.yaml file.
    """

    model_config = {"env_prefix": "OCTO_", "env_nested_delimiter": "__"}

    octopus: OctopusApiSettings | None = None
    battery: BatterySettings = BatterySettings()
    solar: SolarSettings = SolarSettings()
    thresholds: ThresholdSettings = ThresholdSettings()
    home_assistant: HaSettings = HaSettings()
    mqtt: MqttSettings = MqttSettings()
    inverter_control: InverterControlSettings = InverterControlSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    db_path: str = "data/optimizer.db"
    backup_dir: str = "/share/octopus_optimizer_backups"
    backup_retention_days: int = 7
    api_port: int = 8099
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def from_yaml(cls, path: str | Path) -> AppSettings:
        """Load settings from a YAML configuration file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data or {})

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> AppSettings:
        """Load settings from YAML file if it exists, falling back to env vars."""
        if config_path and Path(config_path).exists():
            return cls.from_yaml(config_path)

        for candidate in ["config.yaml", "config.yml"]:
            if Path(candidate).exists():
                return cls.from_yaml(candidate)

        return cls()
