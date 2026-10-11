/*
 * MineGuard data collector: ESP-NOW receive (wearable) + SX1278/RA-02 LoRa
 * receive (Mega), then compact-wire-key expansion and newline JSON over USB.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif
#include <SPI.h>
#include <LoRa.h>
#include <ArduinoJson.h>
#include <string.h>
#include <stdio.h>

#define ESPNOW_CHANNEL 1              // Must match worker ESP32.
#define LORA_FREQUENCY_HZ 433920000L  // User-selected 433.92 MHz; verify local power/duty-cycle/type-approval rules.
#define LORA_SYNC_WORD 0x34
static const int PIN_SCK=18, PIN_MISO=19, PIN_MOSI=23;
static const int PIN_CS=5, PIN_RST=14, PIN_DIO0=26;
static const size_t MAX_PACKET_BYTES=200; // firmware refuses larger packets
static const uint8_t LINK_ESPNOW=1, LINK_LORA=2;

struct RxPacket {
  uint16_t len;
  uint8_t link;
  int16_t rssi;
  float snr;
  char sourceMac[18];
  uint8_t payload[MAX_PACKET_BYTES];
};
QueueHandle_t rxQueue=nullptr;
bool loraReady=false;
volatile uint32_t droppedQueuePackets=0;

void macToText(const uint8_t *mac,char *out,size_t size) {
  if (!mac) { snprintf(out,size,"unknown"); return; }
  snprintf(out,size,"%02X:%02X:%02X:%02X:%02X:%02X",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
}
void enqueuePacket(const uint8_t *mac,const uint8_t *data,int len) {
  if (!rxQueue || !data || len<=0 || len>(int)MAX_PACKET_BYTES) return;
  RxPacket p={}; p.len=(uint16_t)len; p.link=LINK_ESPNOW;
  macToText(mac,p.sourceMac,sizeof(p.sourceMac));
  memcpy(p.payload,data,len);
  if (xQueueSend(rxQueue,&p,0)!=pdTRUE) droppedQueuePackets++;
}

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void onEspNowReceive(const esp_now_recv_info_t *info,const uint8_t *data,int len) {
  enqueuePacket(info?info->src_addr:nullptr,data,len);
}
#else
void onEspNowReceive(const uint8_t *mac,const uint8_t *data,int len) {
  enqueuePacket(mac,data,len);
}
#endif

bool setupEspNow() {
  WiFi.mode(WIFI_STA); WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL,WIFI_SECOND_CHAN_NONE);
  if (esp_now_init()!=ESP_OK) return false;
  return esp_now_register_recv_cb(onEspNowReceive)==ESP_OK;
}
bool setupLoRa() {
  if (LORA_FREQUENCY_HZ<=0) { Serial.println("# LoRa off: set legal LORA_FREQUENCY_HZ first"); return false; }
  SPI.begin(PIN_SCK,PIN_MISO,PIN_MOSI,PIN_CS);
  LoRa.setPins(PIN_CS,PIN_RST,PIN_DIO0);
  if (!LoRa.begin(LORA_FREQUENCY_HZ)) return false;
  LoRa.setSyncWord(LORA_SYNC_WORD); LoRa.enableCrc();
  LoRa.setSpreadingFactor(7); LoRa.setSignalBandwidth(125E3);
  return true;
}

void receiveLoRa() {
  if (!loraReady) return;
  int n=LoRa.parsePacket();
  if (n<=0) return;
  if (n>(int)MAX_PACKET_BYTES) { while(LoRa.available()) LoRa.read(); Serial.println("# oversized LoRa packet dropped"); return; }
  RxPacket p={}; p.len=(uint16_t)n; p.link=LINK_LORA;
  p.rssi=(int16_t)LoRa.packetRssi(); p.snr=LoRa.packetSnr();
  snprintf(p.sourceMac,sizeof(p.sourceMac),"lora-node");
  size_t i=0; while(LoRa.available() && i<MAX_PACKET_BYTES) p.payload[i++]=(uint8_t)LoRa.read();
  if (i!=(size_t)n) { Serial.println("# incomplete LoRa packet read dropped"); return; }
  p.len=(uint16_t)i;
  if (p.len && xQueueSend(rxQueue,&p,0)!=pdTRUE) droppedQueuePackets++;
}

void expandCompactPacket(JsonDocument &doc) {
  const char *kind=doc["t"] | "?";
  bool isWorker=(strcmp(kind,"w")==0);
  doc["schema_version"]=doc["v"] | 1;
  doc["node_type"]=isWorker?"worker":"mine";
  doc["node_id"]=doc["id"] | "unknown";
  doc["boot_id"]=doc["b"] | "boot-unknown";
  doc["seq"]=doc["q"] | 0;
  doc["zone_id"]=doc["z"] | "unassigned";
  doc["uptime_ms"]=doc["up"] | 0;
  doc.remove("v"); doc.remove("t"); doc.remove("id"); doc.remove("b");
  doc.remove("q"); doc.remove("z"); doc.remove("up");

  if (isWorker) {
    if (!doc["wid"].isNull()) doc["worker_id"]=doc["wid"];
    if (!doc["hr"].isNull()) doc["heart_rate_bpm"]=doc["hr"];
    if (!doc["o2"].isNull()) doc["spo2_pct"]=doc["o2"];
    if (!doc["nt"].isNull()) doc["ntc_skin_temp_c"]=doc["nt"];
    if (!doc["ag"].isNull()) doc["accel_g"]=doc["ag"];
    if (!doc["mn"].isNull()) doc["accel_min_g"]=doc["mn"];
    if (!doc["ac"].isNull()) doc["motion_samples"]=doc["ac"];
    doc["ppg_quality"]=(doc["pq"] | 0)==1?"contact":"poor_or_no_contact";
    doc.remove("wid"); doc.remove("hr"); doc.remove("o2"); doc.remove("nt");
    doc.remove("ag"); doc.remove("mn"); doc.remove("ac"); doc.remove("pq");
  } else {
    if (!doc["g5"].isNull()) doc["mq5_raw"]=doc["g5"];
    if (!doc["g9"].isNull()) doc["mq9_raw"]=doc["g9"];
    if (!doc["tc"].isNull()) doc["ambient_temp_c"]=doc["tc"];
    if (!doc["rh"].isNull()) doc["humidity_pct"]=doc["rh"];
    if (!doc["ti"].isNull()) doc["tilt_active"]=(doc["ti"].as<int>()!=0);
    if (!doc["p25"].isNull()) doc["pm25_ug_m3"]=doc["p25"];
    if (!doc["p10"].isNull()) doc["pm10_ug_m3"]=doc["p10"];
    doc["pm_valid"]=!doc["p25"].isNull() || !doc["p10"].isNull();
    doc["mq_calibrated"]=false;
    doc.remove("g5"); doc.remove("g9"); doc.remove("tc"); doc.remove("rh");
    doc.remove("ti"); doc.remove("p25"); doc.remove("p10");
  }
}

void outputJsonLine(const RxPacket &p) {
  // 1024-byte document gives room for expanded keys + collector metadata.
  StaticJsonDocument<1024> doc;
  DeserializationError err=deserializeJson(doc,p.payload,p.len);
  if (err) { Serial.print("# malformed compact packet JSON: "); Serial.println(err.c_str()); return; }
  if (doc["t"].isNull() || doc["id"].isNull() || doc["q"].isNull() || doc["z"].isNull() || doc["up"].isNull()) {
    Serial.println("# incomplete compact packet; dropped"); return;
  }
  const char *wireType=doc["t"] | "?";
  if (strcmp(wireType,"w")!=0 && strcmp(wireType,"m")!=0) {
    Serial.println("# unknown compact packet type; dropped"); return;
  }
  expandCompactPacket(doc);
  doc["gateway_id"]="collector_01";
  doc["transport"]=(p.link==LINK_ESPNOW)?"esp_now":"lora";
  doc["gateway_received_ms"]=millis();
  doc["source_mac"]=p.sourceMac;
  if (p.link==LINK_LORA) { doc["rssi_dbm"]=p.rssi; doc["snr_db"]=p.snr; }
  if (doc.overflowed()) { Serial.println("# expanded JSON document overflow; packet rejected"); return; }
  const size_t expected=measureJson(doc);
  if (expected==0) { Serial.println("# empty expanded JSON; packet rejected"); return; }
  const size_t written=serializeJson(doc,Serial);
  if (written!=expected) { Serial.println(); Serial.println("# incomplete USB JSON write; packet rejected"); return; }
  Serial.println(); // Python edge reads one normalized JSON object per line.
}

void setup() {
  Serial.begin(115200); delay(700);
  rxQueue=xQueueCreate(10,sizeof(RxPacket));
  if (!rxQueue) { Serial.println("# RX queue allocation failed"); while(true) delay(1000); }
  WiFi.mode(WIFI_STA); WiFi.disconnect();
  Serial.print("# collector STA MAC: "); Serial.println(WiFi.macAddress());
  Serial.printf("# ESP-NOW channel: %d\n",ESPNOW_CHANNEL);
  if (!setupEspNow()) Serial.println("# ESP-NOW initialization failed");
  loraReady=setupLoRa();
  Serial.println("# collector ready; normalized JSON lines follow");
}
void loop() {
  receiveLoRa();
  RxPacket p;
  while(xQueueReceive(rxQueue,&p,0)==pdTRUE) outputJsonLine(p);
  if (droppedQueuePackets) {
    Serial.printf("# collector queue dropped %lu packet(s)\n",(unsigned long)droppedQueuePackets);
    droppedQueuePackets=0;
  }
}
