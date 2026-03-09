#!/usr/bin/env bash
set -e

# Read config from HA add-on options using jq
CONFIG_PATH="/data/options.json"

echo "Generating config.yaml from add-on options..."

OCTOPUS_API_KEY=$(jq -r '.octopus_api_key' "$CONFIG_PATH")
OCTOPUS_ACCOUNT=$(jq -r '.octopus_account_number' "$CONFIG_PATH")
EXPORT_MPAN=$(jq -r '.export_mpan' "$CONFIG_PATH")
EXPORT_SERIAL=$(jq -r '.export_serial' "$CONFIG_PATH")
IMPORT_MPAN=$(jq -r '.import_mpan' "$CONFIG_PATH")
IMPORT_SERIAL=$(jq -r '.import_serial' "$CONFIG_PATH")
EXPORT_PRODUCT=$(jq -r '.export_product_code' "$CONFIG_PATH")
EXPORT_TARIFF=$(jq -r '.export_tariff_code' "$CONFIG_PATH")
IMPORT_PRODUCT=$(jq -r '.import_product_code' "$CONFIG_PATH")
IMPORT_TARIFF=$(jq -r '.import_tariff_code' "$CONFIG_PATH")
BATTERY_CAPACITY=$(jq -r '.battery_capacity_kwh' "$CONFIG_PATH")
FLAT_RATE=$(jq -r '.flat_export_rate_pence' "$CONFIG_PATH")
EXPORT_THRESHOLD=$(jq -r '.export_now_threshold_pence' "$CONFIG_PATH")

# HA Supervisor provides the token and URL automatically
HA_TOKEN="${SUPERVISOR_TOKEN}"
HA_URL="http://supervisor/core"

# MQTT from Supervisor services API
MQTT_HOST=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" http://supervisor/services/mqtt 2>/dev/null | jq -r '.data.host // "localhost"')
MQTT_PORT=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" http://supervisor/services/mqtt 2>/dev/null | jq -r '.data.port // 1883')
MQTT_USER=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" http://supervisor/services/mqtt 2>/dev/null | jq -r '.data.username // empty')
MQTT_PASS=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" http://supervisor/services/mqtt 2>/dev/null | jq -r '.data.password // empty')

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

echo "Starting Octopus Export Optimizer..."
exec python -m octopus_export_optimizer -c /data/config.yaml
