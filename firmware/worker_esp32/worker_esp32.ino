/*
 * Worker wearable: ESP32 + MAX30102 + ADXL345 + NTC; sends via ESP-NOW.
 * Compact wire keys keep the ESP-NOW packet below 200 bytes. Collector expands
 * them into the Python schema. This sketch reports motion summaries, NOT a
 * validated fall-detection decision.
 */
#include <Arduino.h>
#include <math.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ArduinoJson.h>
#include <string.h>
#include <MAX30105.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL345_U.h>
#include <esp_system.h>
#include "spo2_algorithm.h"

#define ESPNOW_CHANNEL 1 // Must match collector.
const uint8_t COLLECTOR_MAC[6]={0x00,0x00,0x00,0x00,0x00,0x00}; // REPLACE from collector serial output.

// Example ESP32 DevKit I2C/ADC pins. Check the exact board.
static const int PIN_SDA=21, PIN_SCL=22, PIN_NTC=34;
static const uint32_t TX_INTERVAL_MS=5000;
static const uint32_t MOTION_SAMPLE_MS=10; // local motion sampling ~100 Hz; verify with hardware
static const uint32_t IR_CONTACT_THRESHOLD=5000; // tune for the optical breakout/placement
static const uint16_t PPG_BUFFER_SIZE=100;
static const size_t MAX_PACKET_BYTES=200; // conservative margin below ESP-NOW v1 250-byte limit

// Replace with the actual NTC and divider values.
static const float NTC_R0_OHMS=10000.0f, NTC_BETA=3950.0f;
static const float NTC_SERIES_OHMS=10000.0f, NTC_T0_K=298.15f;
static const int NTC_ADC_MAX=4095;
const char *NODE_ID="w1";       // unique/pseudonymous demo node ID
const char *WORKER_ID="w1";
const char *ZONE_ID="z1";       // assigned zone; not a live location

MAX30105 ppg;
Adafruit_ADXL345_Unified adxl=Adafruit_ADXL345_Unified(12345);
uint32_t irBuffer[PPG_BUFFER_SIZE], redBuffer[PPG_BUFFER_SIZE];
uint16_t ppgCount=0;
int32_t latestHr=0, latestSpo2=0;
bool hrValid=false, spo2Valid=false, opticalContact=false;
uint32_t seqNo=0, lastTx=0, bootId=0, lastMotionSample=0;
float accelPeakG=0, accelMinG=1000;
uint16_t accelSamples=0;
bool adxlReady=false, espNowReady=false;

bool setupEspNow() {
  bool allZero=true;
  for (int i=0;i<6;i++) if (COLLECTOR_MAC[i]!=0) allZero=false;
  if (allZero) { Serial.println("Set COLLECTOR_MAC from collector output first."); return false; }
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL,WIFI_SECOND_CHAN_NONE);
  if (esp_now_init()!=ESP_OK) return false;
  esp_now_peer_info_t peer={};
  memcpy(peer.peer_addr,COLLECTOR_MAC,6);
  peer.channel=ESPNOW_CHANNEL;
  peer.ifidx=WIFI_IF_STA;
  peer.encrypt=false;
  return esp_now_add_peer(&peer)==ESP_OK;
}

void readPpgFifo() {
  ppg.check();
  while (ppg.available()) {
    uint32_t ir=ppg.getIR(), red=ppg.getRed();
    opticalContact=(ir>IR_CONTACT_THRESHOLD);
    if (ppgCount<PPG_BUFFER_SIZE) { irBuffer[ppgCount]=ir; redBuffer[ppgCount]=red; ppgCount++; }
    ppg.nextSample();
    if (ppgCount>=PPG_BUFFER_SIZE) {
      int32_t calcSpo2=0, calcHr=0; int8_t validS=0, validH=0;
      maxim_heart_rate_and_oxygen_saturation(irBuffer,PPG_BUFFER_SIZE,redBuffer,&calcSpo2,&validS,&calcHr,&validH);
      spo2Valid=(validS==1) && opticalContact;
      hrValid=(validH==1) && opticalContact;
      if (spo2Valid) latestSpo2=calcSpo2;
      if (hrValid) latestHr=calcHr;
      for (uint16_t i=25;i<PPG_BUFFER_SIZE;i++) { irBuffer[i-25]=irBuffer[i]; redBuffer[i-25]=redBuffer[i]; }
      ppgCount=75;
    }
  }
}

void sampleMotion() {
  uint32_t now=millis();
  if (!adxlReady || now-lastMotionSample<MOTION_SAMPLE_MS) return;
  lastMotionSample=now;
  sensors_event_t event;
  adxl.getEvent(&event);
  float mag=sqrtf(event.acceleration.x*event.acceleration.x+
                  event.acceleration.y*event.acceleration.y+
                  event.acceleration.z*event.acceleration.z);
  float g=mag/9.80665f;
  if (g>accelPeakG) accelPeakG=g;
  if (g<accelMinG) accelMinG=g;
  accelSamples++;
}

float readNtcC() {
  int raw=analogRead(PIN_NTC);
  if (raw<=0 || raw>=NTC_ADC_MAX) return NAN;
  // Assumes 3.3V -> fixed resistor -> ADC node -> NTC -> GND.
  float rNtc=NTC_SERIES_OHMS*(float)raw/(float)(NTC_ADC_MAX-raw);
  if (rNtc<=0) return NAN;
  float invT=(1.0f/NTC_T0_K)+logf(rNtc/NTC_R0_OHMS)/NTC_BETA;
  return (1.0f/invT)-273.15f;
}

void transmitSummary() {
  float ntcC=readNtcC();
  StaticJsonDocument<512> doc;
  // Compact wire schema: v/version, t/type, id, wid/worker, b/boot, q/seq, z/zone, up/uptime.
  doc["v"]=1; doc["t"]="w"; doc["id"]=NODE_ID; doc["wid"]=WORKER_ID;
  doc["b"]=String(bootId,HEX); doc["q"]=seqNo++; doc["z"]=ZONE_ID; doc["up"]=millis();
  if (hrValid && opticalContact) doc["hr"]=latestHr;
  if (spo2Valid && opticalContact) doc["o2"]=latestSpo2;
  if (isfinite(ntcC)) doc["nt"]=ntcC;
  if (adxlReady && accelSamples>0) { doc["ag"]=accelPeakG; doc["mn"]=accelMinG; doc["ac"]=accelSamples; }
  doc["pq"]=opticalContact?1:0;

  if (doc.overflowed()) { Serial.println("JSON document overflow; ESP-NOW packet rejected"); return; }
  const size_t measured=measureJson(doc); // exact compact JSON byte count, excluding terminator
  if (measured==0 || measured>MAX_PACKET_BYTES) {
    Serial.printf("ESP-NOW packet rejected: measured %u bytes (limit %u)\n",(unsigned)measured,(unsigned)MAX_PACKET_BYTES);
    return;
  }
  char payload[MAX_PACKET_BYTES+1];
  const size_t n=serializeJson(doc,payload,sizeof(payload));
  if (n!=measured) { Serial.println("incomplete JSON serialization; packet rejected"); return; }
  esp_err_t result=esp_now_send(COLLECTOR_MAC,(const uint8_t*)payload,n);
  if (result!=ESP_OK) Serial.printf("ESP-NOW send error: %d\n",(int)result);
  else Serial.printf("sent %u-byte ESP-NOW packet (measured)\n",(unsigned)n);
  accelPeakG=0; accelMinG=1000; accelSamples=0;
}

void setup() {
  Serial.begin(115200); delay(500);
  bootId=esp_random();
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_NTC,ADC_11db);
  Wire.begin(PIN_SDA,PIN_SCL);
  if (!ppg.begin(Wire,I2C_SPEED_FAST)) { Serial.println("MAX30102 not found"); while(true) delay(1000); }
  ppg.setup(60,4,2,100,411,4096);
  ppg.setPulseAmplitudeRed(0x0A); ppg.setPulseAmplitudeIR(0x1F); ppg.setPulseAmplitudeGreen(0);
  adxlReady=adxl.begin();
  if (adxlReady) {
    adxl.setRange(ADXL345_RANGE_4_G);
    adxl.setDataRate(ADXL345_DATARATE_100_HZ);
  }
  else Serial.println("ADXL345 not found; motion summary unavailable");
  espNowReady=setupEspNow();
  if (!espNowReady) Serial.println("ESP-NOW disabled; set collector MAC/channel and reboot");
}

void loop() {
  readPpgFifo();
  sampleMotion();
  if (espNowReady && millis()-lastTx>=TX_INTERVAL_MS) {
    lastTx=millis();
    transmitSummary();
  }
}
