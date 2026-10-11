# MineGuard — revised edge-to-core prototype

This ZIP is a **software prototype package** for the updated sensor list and workflow. It preserves the existing forecasting algorithm and transport topology, adds reference-table calibration plumbing, an optional Google Sheets sink, separate Dashboard/Digital Twin apps, InSAR upload/visualization, and worker-data display suppression. It is not a certified mine-safety, medical, geotechnical, or legal fitness-for-work system.

## Requirements captured

- **Mine node:** MQ-5, MQ-7, DHT11, GP2Y1010AU0F dust/PM2.5 sensor, LoRa.
- **Worker wearable:** MAX30102, ADXL345, NTC thermistor.
- **Data flow preserved:** worker ESP32 → collector ESP32 by ESP-NOW; mine node → collector by SX1278/RA-02 LoRa; collector → Python edge by USB serial. The collector receives both links. LoRa is configured at the previously selected 433.92 MHz; verify the radio and country-specific limits.
- **Workflow:** normalize incoming readings → apply only user-supplied software calibration → retain an offline edge buffer → append to Google Sheets when configured → sync status-only worker records and mine-node data to the Core API.
- **Websites:** `dashboard.py` and `digital_twin_app.py` are separate Streamlit entry points on separate ports/URLs. Both include a user InSAR upload and descriptive analysis view.
- **Forecasting:** the existing HistGradientBoosting + trend/fusion forecaster is preserved, as are its 30-second sampling and 15-minute forecast horizon. Feed it calibrated target fields (such as `methane_pct`, `co_ppm`, or `pm25_ug_m3`) only. It is experimental; no detection-rate or safety-performance claim is made.

## Important current-firmware limitations

The sketches are intentionally included **unchanged** from the prior Python-only scope. The current code does not yet stream raw values for every newly specified sensor:

| Sensor | What current packets contain | What is needed for full raw calibration |
|---|---|---|
| MQ-5 | Raw ADC (`mq5_raw`) | User reference gas, units, and measured points |
| MQ-7 | The old collector field is `mq9_raw`/wire key `g9`; Python maps it to `mq7_raw` by default | Confirm MQ-7 is physically connected to the old second channel. Set `MINEGUARD_MAP_LEGACY_MQ9_TO_MQ7=false` if not |
| DHT11 | Digital temperature/humidity outputs | Reference thermometer/hygrometer readings; these are not ADC counts |
| GP2Y1010AU0F | **No readings emitted**; the existing PM reader is a placeholder | Sensor wiring/driver must emit raw ADC/voltage on a future firmware update |
| Legacy tilt channel | Older sketch may still emit `ti`/`tilt_active`; the revised model and sites ignore it because tilt is not in the updated requested list | Confirm whether to retain and support this channel in a later firmware/model revision |
| MAX30102 | Computed heart-rate and SpO₂ estimates | Raw red/IR PPG samples are not transmitted; only those output estimates can be reference-corrected in this Python path |
| ADXL345 | Acceleration magnitude/minimum summary | Raw 3-axis samples are not transmitted; axis calibration needs additional transmitted fields |
| NTC thermistor | Computed temperature in °C | Raw ADC/resistance and actual divider/thermistor parameters are not transmitted |

Therefore this release supplies a **generic calibration engine and blank table**, but it cannot produce a real calibrated value for a channel with no packet field or reference data. No coefficient, gas concentration, fall decision, or work-status threshold has been invented. The raw accelerometer summary is not a validated fall detector.

## Software calibration

Calibration runs in Python after the packet reaches the host. Fill `data/sensor_calibration_template.csv` using this schema:

```text
sensor,source_field,target,output_field,unit,input_value,reference_value
```

Each `(sensor, source_field)` profile needs at least two distinct measured reference pairs; three or more across the intended range are recommended. All anchors in a profile must use the same target/output/unit and have monotonic reference values. The code interpolates only between anchors, preserves the source field, reports status/metadata, and returns `null` outside the measured input range. It does not extrapolate.

Examples of the **kind** of profile to provide (not calibration values):

- MQ-5: `sensor=mq5`, source `mq5_raw`, and an explicitly identified target gas/reference unit.
- MQ-7: `sensor=mq7`, source `mq7_raw`, target gas confirmed by your reference setup (often CO, but you must state it).
- DHT11: separate temperature and humidity profiles against suitable reference instruments.
- GP2Y1010AU0F: `gp2y_raw_adc` against a suitable reference particulate monitor; the sensor output alone is not a certified PM2.5 analyzer.
- MAX30102: only the transmitted HR/SpO₂ estimates can be corrected here; they remain non-medical estimates and need an appropriate comparator/validation.
- ADXL345 and NTC: profiles can be added for fields that are actually transmitted. Full raw/axis calibration needs firmware packet updates.

Apply a filled table to saved CSV/JSONL:

```bash
python calibrate_sensors.py --input raw_packets.jsonl --output calibrated_packets.jsonl --calibration-table data/sensor_calibration.csv
```

Or at the Python edge process:

```bash
python edge_service.py --port COM4 --baud 115200 --core http://127.0.0.1:8000 --calibration-table data/sensor_calibration.csv --sheets
```

Do not enable a calibration table until it contains suitable measured anchors. Until then, raw/derived readings remain explicitly uncalibrated.

## Google Sheets

The edge service locally buffers all telemetry, but Sheets mode is deliberately gated: `--sheets` requires a populated `--calibration-table`, and an individual packet is queued to Sheets only when a configured reference profile matches a source field present in that packet. The Sheet row preserves raw source values beside calibrated outputs and calibration status. Out-of-range readings remain visible as raw inputs with a `null` calibrated value and `out_of_range` metadata; they are not extrapolated. A channel with no matching profile is not made calibrated just because another channel has a profile, and a record with no applicable profile stays local (it may still follow the existing Core path). Populate reference pairs for each channel you intend to upload. Because the current firmware does not transmit GP2Y raw data, that channel cannot be calibrated or uploaded until firmware support is added. The service uses the service-account API and never includes credentials in this ZIP. Configure the following in the host environment or secret manager (do not commit the key):

```text
GOOGLE_SERVICE_ACCOUNT_FILE=/secure/path/service-account.json
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_MINE_RANGE=mine_data!A:K
GOOGLE_SHEETS_WORKER_RANGE=worker_private!A:K
```

Create two worksheets with the same 11-column header (the connector adds it if a tab exists but is empty): `record_key,node_type,node_id,boot_id,seq,event_time,received_at,zone_id,work_status,payload_json,calibration_json`. Mine records with an applicable profile go to `mine_data`; **worker packets with an applicable worker profile go to `worker_private`** because sensor values must stay off the websites. A table containing only mine profiles will not queue worker packets. Lock down Sheet sharing and service-account access: that worker tab contains sensitive values. The websites and public Core read endpoints do not expose individual worker sensor values; they show anonymous counts from the approved `work_status` field only. The edge buffer still has raw packets locally.

If Sheets is unavailable, the edge queue remains pending for retry. This append connector uses `record_key` for audit/deduplication, but Google Sheets itself is not a transactional time-series database; use a proper secured database for production/high-volume collection.

## Worker status privacy

Both websites suppress worker sensor values, IDs, zones, and history. They show aggregate counts in these categories only:

- `good_to_work` → **Good**
- `needs_attention` → **Needs attention**
- `not_fit` (legacy `not_well_to_work` also accepted) → **Not fit**
- Missing/unknown → **Not assessed**

The dashboards accept only an approved upstream `work_status`; they do not calculate fitness from heart rate, SpO₂, temperature, or motion. The current firmware does not send `work_status`, so it remains **Not assessed** until a qualified/approved upstream process supplies that field. These labels must not be treated as a medical diagnosis or legal fitness-for-work determination.

The Core stores only the allowlisted status for new worker records and omits worker history, alerts, and locations from its general read endpoints. The edge PC and restricted worker Sheet still contain sensitive telemetry; there is no encryption/authentication or retention management in this prototype.

## InSAR upload and analysis

Both separate apps have an upload control for **already-processed** displacement data:

- CSV with `longitude,latitude,displacement_mm`; optional `date,zone_id`.
- Point GeoJSON with displacement properties.
- Georeferenced displacement GeoTIFF (requires optional `rasterio`, install `requirements-insar.txt`).

The app shows a point map, descriptive displacement statistics, and a date trend when timestamps are present. Raw Sentinel-1 SLC/interferogram processing is not included. No user-provided dataset is persisted or uploaded to Google Sheets by this visualizer; each site analyzes the upload in its current session. It does not classify stability, set a hazard threshold, or trigger evacuation. Provide surveyed mine boundaries/coordinates before overlaying it with a site plan.

## Architecture and slide criteria

The package is organized around the four-layer blueprint: current sensors/reference data → local Python edge buffer and models → Core/API/store plus schematic twin → Dashboard and Twin applications. The edge service retains store-and-forward behavior. The control-room alert UI sorts mine alerts by severity and shows the supplied explanation and any explicitly configured mitigation action. The audit tab exports recorded mine alert events to CSV.

The screenshots also describe **future target capabilities**. This release does not claim LoRaWAN/private-5G integration, a rugged in-pit AI gateway, a wearable/cab display, an evacuation route, vehicle detection, validated alerts, fused seismic/fleet analytics, or automated emissions/ESG reports. The listed sensors do not provide vehicle, fuel, energy, or emissions data. No binary “EVACUATE” or “VEHICLE APPROACHING” command is emitted without the needed input sensors, approved rule, and alarm hardware. Forecast quality must be tested on labeled field data; false-alarm and early-detection goals in the slides are not established results.

## Run as two separate URLs locally

Install Python 3.10+ dependencies:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Terminal 1 — Core API:

```bash
uvicorn core_service:app --host 0.0.0.0 --port 8000
```

Terminal 2 — Dashboard (`http://localhost:8501`):

```bash
CORE_URL=http://127.0.0.1:8000 streamlit run dashboard.py --server.address 0.0.0.0 --server.port 8501
```

Terminal 3 — Digital Twin (`http://localhost:8502`):

```bash
CORE_URL=http://127.0.0.1:8000 streamlit run digital_twin_app.py --server.address 0.0.0.0 --server.port 8502
```

A `docker-compose.yml` is included to run Core, Dashboard, and Twin on separate ports. Publishing two public URLs still requires a hosting account/domain and secret configuration. Do not expose the unauthenticated prototype Core API to the public internet.

## Forecasting and validation

`forecasting.py` and the HistGradientBoosting/trend/fusion pipeline are preserved. They continue to expect calibrated target fields and the existing 15-minute horizon; the algorithm is not evidence of safety performance. Use only calibrated/approved channels for forecasts and limits. The `FORECAST_LIMITS` and `HARD_LIMITS` dictionaries remain empty.

Run tests:

```bash
python -m unittest discover -s tests -v
```

Tests cover reference interpolation/range handling, legacy MQ field mapping, edge queueing/API minimization, dashboard privacy, InSAR parsing, Google Sheets tab separation, forecasting event-time behavior, and compact firmware packet fixtures. They use synthetic-only values and do not validate sensors, firmware timing, radios, medical metrics, InSAR interpretation, or field safety.
