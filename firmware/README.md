# Firmware notes — corrected collector topology

> **Hardware compatibility warning:** the sketches in this Python-focused ZIP are preserved unchanged from the earlier prototype. They still label the second gas channel MQ-9 and leave the particulate reader as a placeholder. They do not stream GP2Y1010AU0F samples or raw MAX30102/ADXL345/NTC data. The Python adapter can rename the old second MQ field to MQ-7 only if MQ-7 is physically connected to that channel; see the project README. Full all-sensor raw calibration requires a separately approved firmware/protocol update.

## Nodes and links

1. **Worker wearable ESP32** reads MAX30102, ADXL345, and NTC, then sends a compact JSON packet to the collector using **ESP-NOW**.
2. **Mine node Arduino Mega 2560** reads MQ-5, MQ-9, DHT11, tilt, and the PM placeholder; it sends JSON over SX1278/RA-02 raw LoRa.
3. **Collector ESP32** receives both ESP-NOW and LoRa packets, adds link metadata, and prints newline-delimited JSON over USB serial to the Python edge computer.

The collector is not the same as the worker wearable board: it needs its own ESP32 and a LoRa radio attached. The collector's built-in Wi-Fi radio handles ESP-NOW; the RA-02 handles LoRa. The corrected setup requires **two LoRa radios total** (Mega transmitter and collector receiver), not three; the worker wearable does not use LoRa. Confirm the exact Mega model before upload—the reported “Mega 2465” may mean Mega 2560, but this is not verified. Water-level, conductivity, and airflow channels are excluded from this prototype.

## Pairing ESP-NOW

1. Flash `collector_esp32/collector_esp32.ino` and open Serial Monitor at 115200 baud.
2. Copy the printed `collector STA MAC` address.
3. Put that MAC into `COLLECTOR_MAC` in `worker_esp32/worker_esp32.ino`.
4. Set the same `ESPNOW_CHANNEL` in the worker and collector sketches. Keep the collector disconnected from an unrelated Wi-Fi access point for this simple fixed-channel demo.

ESP-NOW and Wi-Fi use the ESP32's 2.4 GHz radio; if later connecting the collector to Wi-Fi, the ESP-NOW channel must be compatible with the access point. The LoRa radio is separate.

## Configure LoRa

The Mega and collector sketches are configured to the user-selected **433.92 MHz** (`433920000L`), within the reported module tuning range of 410–525 MHz. For India, the WPC low-power rules list 433.05–434.79 MHz, but a frequency match alone does not establish compliance: verify the applicable ERP/power, duty-cycle, equipment-type approval, antenna, and other conditions for the exact radio and deployment. See the [official WPC rule](https://eservices.dot.gov.in/sites/default/files/circular-notifications/the-use-of-low-power-radio-frequency-devices-in-the-frequency-band-433.05-to-434.79-mhz-exemption-from-license-rules-2022.pdf). If deployed elsewhere, verify that country's requirements. Use the same frequency, sync word, spreading factor, and bandwidth on both radios. The worker sketch does not use LoRa.

The RA-02 is a **3.3 V device**. The Arduino Mega 2560 is 5 V logic: use suitable level shifting for SPI/control signals and a stable 3.3 V supply. Do not connect Mega 5 V logic directly to RA-02 pins. Collector ESP32 example pins: SCK=18, MISO=19, MOSI=23, CS=5, RESET=14, DIO0=26. Mega example: CS=10, RESET=9, DIO0=2; hardware SPI on 50/51/52 and SS=53 or ICSP header. Check your exact boards.

## Worker sketch pin/sensor notes

Example ESP32 DevKit I2C pins: SDA=21, SCL=22; NTC ADC=34. Change to match the actual board. Install SparkFun MAX3010x and Adafruit ADXL345/Unified libraries. Add SparkFun/Maxim's `spo2_algorithm.h` to the sketch folder if required by the installed library. MAX30102 readings are motion/contact sensitive and not medical diagnoses.

NTC `R0`, beta, and series resistance are placeholders. Set these from the exact thermistor and divider circuit. The code assumes 3.3 V -> fixed resistor -> ADC -> NTC -> GND.

The worker sets ADXL345 output data rate to 100 Hz and polls locally with a 10 ms target, then reports peak/minimum acceleration and sample count about every five seconds. The rate is an implementation target, not measured/validated here. The sketch does **not** classify falls; do not describe it as a fall detector.

## Mine-node sensor notes

- MQ-5/MQ-9 are sent as raw ADC values with `mq_calibrated=false`. `methane_pct` and `co_ppm` are omitted until a documented site calibration is added. Do not treat raw values as ppm or methane percentage.
- DHT11 is used for ambient temperature and humidity.
- Tilt is assumed to be a digital switch on D3 using `INPUT_PULLUP`; invert or replace the reader for another module.
- PM is a stub returning unavailable until the exact PM sensor is selected and its documented driver is added.

## Packet and Python handoff

Compact-key map (collector expansion is in `collector_esp32.ino`):

| Wire key | Normalized field | Notes |
|---|---|---|
| `v`, `t`, `id`, `b`, `q`, `z`, `up` | schema/type, node/boot/sequence/zone, uptime | Common metadata |
| `wid`, `hr`, `o2`, `nt`, `ag`, `mn`, `ac`, `pq` | worker ID, heart rate, SpO₂ estimate, NTC temp, acceleration peak/min/sample count, PPG-contact flag | Worker only; unavailable sensor values are omitted |
| `g5`, `g9`, `tc`, `rh`, `ti`, `p25`, `p10` | MQ-5/MQ-9 raw ADC, ambient temp, humidity, tilt, PM2.5/PM10 | Mine only; PM keys are omitted until an actual reader is implemented; raw MQ stays uncalibrated |

The worker/Mega send compact JSON keys with a 200-byte firmware cap, safely below the LoRa 255-byte payload ceiling. Before send, each sketch checks ArduinoJson document overflow, measures the serialized JSON via `measureJson()`, serializes into a bounded buffer, and verifies the exact byte count. The Mega checks `LoRa.write()` completion and does not call `endPacket()` on an incomplete write. The sketches log the measured bytes on successful transmission. The collector rejects packets over 200 bytes or malformed/incomplete compact objects, expands compact keys, and uses `StaticJsonDocument<1024>` for the normalized object. Actual bytes seen over air must still be recorded during hardware tests; these sketches have not been compiled or tested on boards here.

The collector prints a normalized JSON object plus `transport`, `gateway_id`, receive uptime, and (for LoRa) RSSI/SNR as one serial line. Startup/debug lines begin with `#`; `edge_service.py` skips non-JSON lines. The Python host adds UTC `received_at` and stores records locally before syncing to FastAPI. JSONL replay preserves supplied `event_time` separately; trend charts and forecasting use it when available, falling back to collector receive time for live packets that lack a synchronized clock. The forecaster-training script also auto-prefers `event_time`, then `timestamp_utc`, then `received_at` unless you set `--time-column`.

The current topology has one LoRa transmitter (the Mega) and a separate ESP-NOW wearable link; the earlier two-LoRa-senders collision estimate does not apply to this one-Mega setup. There are still no LoRa acknowledgements/retries or application-layer encryption. Multiple future mine LoRa transmitters would need a deliberate airtime/collision plan. Bench-test and measure packet lengths/RSSI/SNR before relying on the link.
