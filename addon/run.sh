#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

# Read config from HA add-on options
CONFIG_PATH="/data/options.json"

# Generate config.yaml from add-on options
bashio::log.info "Generating config.yaml from add-on options..."

OCTOPUS_API_KEY=$(bashio::config 'octopus_api_key')
OCTOPUS_ACCOUNT=$(bashio::config 'octopus_account_number')
EXPORT_MPAN=$(bashio::config 'export_mpan')
EXPORT_SERIAL=$(bashio::config 'export_serial')
IMPORT_MPAN=$(bashio::config 'import_mpan')
IMPORT_SERIAL=$(bashio::config 'import_serial')
EXPORT_PRODUCT=$(bashio::config 'export_product_code')
EXPORT_TARIFF=$(bashio::config 'export_tariff_code')
IMPORT_PRODUCT=$(bashio::config 'import_product_code')
IMPORT_TARIFF=$(bashio::config 'import_tariff_code')
BATTERY_CAPACITY=$(bashio::config 'battery_capacity_kwh')
FLAT_RATE=$(bashio::config 'flat_export_rate_pence')
EXPORT_THRESHOLD=$(bashio::config 'export_now_threshold_pence')

# HA Supervisor provides the token and URL automatically
HA_TOKEN="${SUPERVISOR_TOKEN}"
HA_URL="http://supervisor/core"

# MQTT from HA services API
if bashio::services.available "mqtt"; then
    MQTT_HOST=$(bashio::services mqtt "host")
    MQTT_PORT=$(bashio::services mqtt "port")
    MQTT_USER=$(bashio::services mqtt "username")
    MQTT_PASS=$(bashio::services mqtt "password")
else
    MQTT_HOST="localhost"
    MQTT_PORT="1883"
    MQTT_USER=""
    MQTT_PASS=""
fi

cat > /data/config.yaml << YAML
octopus:
  api_key: "${OCTOPUS_API_KEY}"
  account_number: "${OCTOPUS_ACCOUNT}"
  export_mpan: "${EXPORT_MPAN}"
  export_serial: "${EXPORT_SERIAL}"
  import_mpan: "${IMPORT_MPAN}"
  import_serial: "${IMPORT_SERIAL}"
  export_product_code: "${EXPORT_PRODUCT}"
  export_tariff_code: "${EXPORT_TARIFF}"
  import_product_code: "${IMPORT_PRODUCT}"
  import_tariff_code: "${IMPORT_TARIFF}"

battery:
  capacity_kwh: ${BATTERY_CAPACITY}
  round_trip_efficiency: 0.90

solar:
  panels:
    - orientation: 0
      tilt: 35
      kwp: 7.14
    - orientation: 180
      tilt: 35
      kwp: 3.06
    - orientation: 90
      tilt: 35
      kwp: 0.88
    - orientation: 270
      tilt: 35
      kwp: 0.88

thresholds:
  flat_export_rates:
    - rate_pence: ${FLAT_RATE}
      effective_from: "2024-01-01"
  export_now_threshold_pence: ${EXPORT_THRESHOLD}
  better_slot_delta_pence: 3.0
  look_ahead_hours: 4
  reserve_soc_floor: 0.20
  minimum_soc_for_export: 0.35
  high_soc_hold_threshold: 0.80
  minimum_meaningful_export_kw: 0.1
  battery_round_trip_efficiency: 0.90
  allow_import_arbitrage: false
  cheap_import_threshold_pence: 7.5
  data_freshness_limit_minutes: 60

home_assistant:
  url: "${HA_URL}"
  token: "${HA_TOKEN}"
  entity_ids:
    battery_soc: "sensor.battery_soc"
    pv_power: "sensor.pv_power"
    feed_in_power: "sensor.feed_in"
    load_power: "sensor.load_power"
    grid_consumption: "sensor.grid_consumption"
    battery_charge_power: "sensor.battery_charge"
    battery_discharge_power: "sensor.battery_discharge"
    work_mode: "select.work_mode"
    max_soc: "number.max_soc"
    min_soc: "number.min_soc"
    force_charge_power: "number.force_charge_power"
    force_discharge_power: "number.force_discharge_power"

mqtt:
  broker: "${MQTT_HOST}"
  port: ${MQTT_PORT}
  username: "${MQTT_USER}"
  password: "${MQTT_PASS}"
  topic_prefix: "octopus_export_optimizer"
  ha_discovery_prefix: "homeassistant"

db_path: "/data/optimizer.db"
log_level: "INFO"
YAML

bashio::log.info "Starting Octopus Export Optimizer..."
exec python -m octopus_export_optimizer -c /data/config.yaml
