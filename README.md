# Octopus Export Optimizer

A standalone local-first decision, analytics, and reporting engine for UK homes with Octopus Energy Agile export tariffs, battery storage, and solar arrays.

Runs independently from Home Assistant. HA remains the control/orchestration layer; this service answers:

- What did I actually earn from export today?
- How does that compare against a fixed export baseline?
- Should I export now, hold battery, or operate normally?
- Is Agile export materially outperforming fixed export over time?

## System Setup

Designed for a real home with:

- **Tariff:** Intelligent Octopus Go (import) + Agile Outgoing (export)
- **Inverter:** Fox ESS KH10.5
- **Battery:** 11.52 kWh
- **Solar array:** 11.96 kWp total across four orientations:
  - 7.14 kWp North
  - 3.06 kWp South
  - 0.88 kWp East
  - 0.88 kWp West

The multi-orientation array means generation spans a longer portion of the day than a south-only system. The optimizer's decision logic accounts for this.

## Architecture

```
Octopus API ──> Tariff Ingester ──> SQLite
Octopus API ──> Meter Ingester  ──> SQLite
HA REST API ──> State Ingester  ──> SQLite
                                      │
                    ┌─────────────────┘
                    v
              Revenue Calculator ──> Revenue Summaries
              Recommendation Engine ──> Recommendations
                    │
                    v
              MQTT Publisher ──> Home Assistant
```

**Key design choices:**
- Pure-function recommendation engine (no I/O, fully testable)
- SQLite for local-first storage (no external DB)
- MQTT for HA integration with auto-discovery
- All tariff/revenue data in UTC half-hour intervals
- Date-effective flat rate baseline (rate changes tracked over time)
- Idempotent ingestion — safe to re-run

## Recommendation States

| State | Meaning |
|---|---|
| `EXPORT_NOW` | High export rate, battery available — discharge for revenue |
| `HOLD_BATTERY` | Better rate coming later — preserve stored energy |
| `NORMAL_SELF_CONSUMPTION` | No special opportunity — operate normally |
| `CHARGE_FOR_LATER_EXPORT` | Cheap import + strong upcoming export — charge battery |
| `INSUFFICIENT_DATA` | Missing tariff or state data — cannot decide |

Every recommendation includes a human-readable explanation, reason code, and the exact input snapshot used to make the decision.

## Quick Start

### Prerequisites

- Python 3.11+
- MQTT broker (e.g. Mosquitto, typically already in HA)
- Octopus Energy API key
- Home Assistant long-lived access token

### Install

```bash
pip install -e ".[dev]"
```

### Configure

```bash
cp config.yaml.example config.yaml
# Edit config.yaml with your API keys, MPAN, serial numbers, etc.
```

### Run

```bash
python -m octopus_export_optimizer
# or with a custom config path:
python -m octopus_export_optimizer -c /path/to/config.yaml
```

### Run Tests

```bash
pytest tests/ -v
```

## Configuration

All thresholds are configurable in `config.yaml`:

| Setting | Default | Description |
|---|---|---|
| `flat_export_rates` | 12.0 p/kWh | Date-effective flat baseline for comparison |
| `export_now_threshold_pence` | 15.0 | Rate above which export is attractive |
| `better_slot_delta_pence` | 3.0 | How much better a future slot must be to hold |
| `look_ahead_hours` | 4.0 | How far ahead to look for better rates |
| `reserve_soc_floor` | 0.20 | Minimum SoC to preserve (fraction) |
| `minimum_soc_for_export` | 0.35 | SoC needed before battery export |
| `battery_capacity_kwh` | 11.52 | Usable battery capacity |
| `allow_import_arbitrage` | false | Enable cheap-charge-then-export strategy |

## MQTT Entities

Published with HA auto-discovery under `octopus_export_optimizer/`:

- Current/best upcoming export rates
- Recommendation state, explanation, reason code, mode
- Today/month revenue (actual, flat baseline, uplift)
- Today/month exported kWh
- Battery SoC, PV power, feed-in power
- Service status and last run timestamp

## Project Structure

```
src/octopus_export_optimizer/
  config/         # Settings and constants
  models/         # Pydantic domain models
  storage/        # SQLite database, migrations, repositories
  ingestion/      # Octopus API client, tariff/meter/HA state ingesters
  calculations/   # Revenue calculator, aggregator, solar profile
  recommendation/ # Rule-based engine, individual rules, types
  publishing/     # MQTT publisher, payload builder
  app.py          # Application bootstrap and scheduler
```

## Future Extensions

- Solar forecast integration (weather API)
- Battery reserve optimisation
- Import arbitrage automation
- Historical backtesting
- Web dashboard
- Control loop integration with HA automations
