/*
 * Mine sensor node: Arduino Mega 2560 + MQ-5/MQ-9 + DHT11 + digital tilt
 * + optional PM driver + SX1278/RA-02 raw LoRa transmitter.
 * Wire keys are compact; collector_esp32.ino expands them for Python.
 * Mega-to-RA-02 SPI/control signals require 5V-to-3.3V level shifting.
 */
#include <Arduino.h>
#include <math.h>
#include <SPI.h>
#include <LoRa.h>
#include <ArduinoJson.h>
#include <DHT.h>

#define LORA_FREQUENCY_HZ 433920000L // User-selected 433.92 MHz; verify local power/duty-cycle/type-approval rules.
#define LORA_SYNC_WORD 0x34
static const int PIN_LORA_CS=10, PIN_LORA_RST=9, PIN_LORA_DIO0=2;
static const int PIN_MQ5=A0, PIN_MQ9=A1, PIN_DHT=4, PIN_TILT=3;
static const int DHT_TYPE=DHT11;
static const uint32_t TX_INTERVAL_MS=6000;
static const size_t MAX_LORA_BYTES=200; // conservative margin below 255-byte LoRa payload max
const char *NODE_ID="m1";
const char *ZONE_ID="z1";

DHT dht(PIN_DHT,DHT_TYPE);
uint32_t seqNo=0,lastTx=0,bootId=0;
bool radioReady=false;

bool setupRadio() {
  if (LORA_FREQUENCY_HZ<=0) {
    Serial.println("Set LORA_FREQUENCY_HZ after checking module band/local rules.");
    return false;
  }
  pinMode(53,OUTPUT); // keep Mega as SPI master
  SPI.begin();
  LoRa.setPins(PIN_LORA_CS,PIN_LORA_RST,PIN_LORA_DIO0);
  if (!LoRa.begin(LORA_FREQUENCY_HZ)) return false;
  LoRa.setSyncWord(LORA_SYNC_WORD);
  LoRa.enableCrc();
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setTxPower(2); // low-power bench setting; verify local limits
  return true;
}

// Replace with the selected PM sensor's documented driver/interface.
bool readParticulate(float &pm25,float &pm10) {
  (void)pm25; (void)pm10;
  return false; // omit PM keys until a real sensor driver is added
}

void transmitSummary() {
  int mq5=analogRead(PIN_MQ5); // raw ADC only; not ppm
  int mq9=analogRead(PIN_MQ9); // raw ADC only; not ppm
  float tempC=dht.readTemperature();
  float humidity=dht.readHumidity();
  bool tiltActive=(digitalRead(PIN_TILT)==LOW); // INPUT_PULLUP; invert for other modules
  float pm25=NAN,pm10=NAN;
  bool pmValid=readParticulate(pm25,pm10);

  StaticJsonDocument<512> doc;
  // Compact packet keys, expanded by collector: v/t/id/b/q/z/up, g5/g9, tc/rh/ti, p25/p10.
  doc["v"]=1; doc["t"]="m"; doc["id"]=NODE_ID;
  doc["b"]=String(bootId,HEX); doc["q"]=seqNo++; doc["z"]=ZONE_ID; doc["up"]=millis();
  doc["g5"]=mq5; doc["g9"]=mq9;
  if (isfinite(tempC)) doc["tc"]=tempC;
  if (isfinite(humidity)) doc["rh"]=humidity;
  doc["ti"]=tiltActive?1:0;
  if (pmValid && isfinite(pm25)) doc["p25"]=pm25;
  if (pmValid && isfinite(pm10)) doc["p10"]=pm10;

  if (doc.overflowed()) { Serial.println("JSON document overflow; LoRa packet rejected"); return; }
  const size_t measured=measureJson(doc); // exact compact JSON byte count, no terminator
  if (measured==0 || measured>MAX_LORA_BYTES) {
    Serial.print("LoRa packet rejected: measured "); Serial.print(measured);
    Serial.print(" bytes; limit "); Serial.println(MAX_LORA_BYTES);
    return;
  }
  char payload[MAX_LORA_BYTES+1];
  const size_t n=serializeJson(doc,payload,sizeof(payload));
  if (n!=measured) { Serial.println("incomplete JSON serialization; LoRa packet rejected"); return; }
  if (LoRa.beginPacket()!=1) { Serial.println("LoRa radio busy; packet rejected"); return; }
  size_t written=LoRa.write((const uint8_t*)payload,n);
  if (written!=n) { LoRa.idle(); Serial.println("incomplete LoRa write; packet discarded without transmission"); return; }
  if (LoRa.endPacket()==1) Serial.printf("sent %u-byte LoRa packet (measured)\n",(unsigned)n);
  else Serial.println("LoRa transmit failed");
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TILT,INPUT_PULLUP);
  dht.begin();
  randomSeed(analogRead(A15));
  bootId=(uint32_t)random(1L,2147483647L);
  radioReady=setupRadio();
  if (!radioReady) Serial.println("LoRa disabled; set frequency/check wiring, then reboot.");
}

void loop() {
  if (radioReady && millis()-lastTx>=TX_INTERVAL_MS) {
    lastTx=millis();
    transmitSummary();
  }
}
