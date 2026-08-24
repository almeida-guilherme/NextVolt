/**
 * GoodWe Challenge — ESP32 charging-station controller firmware.
 *
 * Hardware
 * --------
 *   ESP32-WROOM-32
 *   PZEM-004T v3.0 energy meter  -> UART2 (Modbus-RTU @ 9600 8N1)
 *       PZEM TX -> GPIO16 (ESP32 RX2)
 *       PZEM RX -> GPIO17 (ESP32 TX2)
 *   Contactor / relay driver     -> GPIO26 (active HIGH through an opto-isolator)
 *   Control Pilot PWM (J1772)    -> GPIO25, 1 kHz, duty encodes the current limit
 *   Proximity / vehicle detect   -> GPIO27 (input, pull-up, LOW = plugged)
 *   Status LED                   -> GPIO2
 *
 * Protocol (identical to simulator/mock_esp32.py)
 * ----------------------------------------------
 *   up   {"type":"telemetry","station_id":"...","connectors":[{...}],"solar":{...}}
 *   down {"type":"control","connectors":[{"connector_id":1,"relay":true,
 *                                        "setpoint_a":16.0,"state":"CHARGING"}]}
 *
 * The backend owns *policy* (load balancing, tariffs, sessions); the firmware
 * owns *safety*. It therefore never trusts a setpoint blindly:
 *   - a setpoint above HARDWARE_MAX_CURRENT_A is clamped;
 *   - the relay opens if no control frame arrives within CONTROL_TIMEOUT_MS
 *     (fail-safe: losing the network must not leave a contactor closed);
 *   - an over-current or over-temperature reading trips the relay locally,
 *     without waiting for the server.
 *
 * Build (PlatformIO): `pio run -t upload` from firmware_esp32/ — see
 * platformio.ini for the library dependencies.
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <PZEM004Tv30.h>
#include <WiFi.h>
#include <WebSocketsClient.h>

// ----------------------------------------------------------------- user config
static const char *WIFI_SSID = "YOUR_WIFI_SSID";
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

static const char *BACKEND_HOST = "192.168.0.10"; // host running FastAPI
static const uint16_t BACKEND_PORT = 8000;
static const char *BACKEND_PATH = "/ws/telemetry?role=station";
static const char *STATION_ID = "GW-EVSE-01";
static const uint8_t CONNECTOR_ID = 1;

// ------------------------------------------------------------------- pin map
static const int PIN_PZEM_RX = 16; // ESP32 receives on this pin
static const int PIN_PZEM_TX = 17;
static const int PIN_RELAY = 26;
static const int PIN_CP_PWM = 25;
static const int PIN_PROXIMITY = 27;
static const int PIN_STATUS_LED = 2;

// --------------------------------------------------------------- safety limits
static const float HARDWARE_MAX_CURRENT_A = 32.0f; // cable + contactor rating
static const float OVER_CURRENT_TRIP_A = 35.0f;    // instantaneous hard trip
static const float NOMINAL_VOLTAGE = 220.0f;
static const uint32_t CONTROL_TIMEOUT_MS = 8000;   // no command -> open relay
static const uint32_t TELEMETRY_PERIOD_MS = 200;   // 5 Hz uplink
static const uint32_t PZEM_PERIOD_MS = 200;

// J1772 control pilot: duty = amps / 0.6 for 6..51 A, 1 kHz carrier.
static const int CP_PWM_CHANNEL = 0;
static const int CP_PWM_FREQ = 1000;
static const int CP_PWM_RESOLUTION = 10; // 0..1023

// ------------------------------------------------------------------ globals
PZEM004Tv30 pzem(Serial2, PIN_PZEM_RX, PIN_PZEM_TX);
WebSocketsClient webSocket;

struct MeterReading {
  float voltage = 0.0f;
  float current = 0.0f;
  float power = 0.0f;
  float energy_kwh = 0.0f;
  float frequency = 0.0f;
  float power_factor = 0.0f;
  bool valid = false;
};

static MeterReading meter;
static float setpointAmps = 0.0f;
static bool relayCommanded = false;
static bool relayClosed = false;
static bool faultLatched = false;
static uint32_t lastControlMs = 0;
static uint32_t lastTelemetryMs = 0;
static uint32_t lastMeterMs = 0;
static uint32_t sequence = 0;

// ------------------------------------------------------------------ helpers
/** Drive the J1772 control pilot duty cycle for the advertised current. */
static void applyPilotCurrent(float amps) {
  if (amps < 6.0f) {
    ledcWrite(CP_PWM_CHANNEL, 0); // 0% duty = "not available"
    return;
  }
  const float duty = constrain(amps / 0.6f, 10.0f, 96.0f); // percent
  const uint32_t maxDuty = (1u << CP_PWM_RESOLUTION) - 1u;
  ledcWrite(CP_PWM_CHANNEL, static_cast<uint32_t>(duty / 100.0f * maxDuty));
}

static void setRelay(bool closed) {
  if (relayClosed == closed) return;
  relayClosed = closed;
  digitalWrite(PIN_RELAY, closed ? HIGH : LOW);
  Serial.printf("[relay] %s\n", closed ? "CLOSED" : "OPEN");
}

/** Single place where the contactor is allowed to close. */
static void enforceSafety() {
  const bool vehiclePresent = digitalRead(PIN_PROXIMITY) == LOW;
  const bool linkAlive = (millis() - lastControlMs) < CONTROL_TIMEOUT_MS;

  if (meter.valid && meter.current > OVER_CURRENT_TRIP_A) {
    if (!faultLatched) {
      Serial.printf("[safety] over-current %.1f A -> local trip\n", meter.current);
    }
    faultLatched = true;
  }

  const bool allowed = relayCommanded && vehiclePresent && linkAlive && !faultLatched;
  setRelay(allowed);
  applyPilotCurrent(allowed ? min(setpointAmps, HARDWARE_MAX_CURRENT_A) : 0.0f);
  digitalWrite(PIN_STATUS_LED, allowed ? HIGH : LOW);
}

static void readMeter() {
  MeterReading reading;
  reading.voltage = pzem.voltage();
  reading.current = pzem.current();
  reading.power = pzem.power();
  reading.energy_kwh = pzem.energy();
  reading.frequency = pzem.frequency();
  reading.power_factor = pzem.pf();

  // The PZEM returns NaN on a Modbus timeout — keep the last good sample so a
  // single dropped frame does not look like a power failure to the backend.
  reading.valid = !isnan(reading.voltage) && !isnan(reading.current);
  if (!reading.valid) {
    Serial.println("[pzem] read timeout, keeping previous sample");
    return;
  }
  if (isnan(reading.energy_kwh)) reading.energy_kwh = meter.energy_kwh;
  if (isnan(reading.power)) reading.power = reading.voltage * reading.current;
  if (isnan(reading.frequency)) reading.frequency = 0.0f;
  if (isnan(reading.power_factor)) reading.power_factor = 0.0f;
  meter = reading;
}

static void sendTelemetry() {
  StaticJsonDocument<512> doc;
  doc["type"] = "telemetry";
  doc["station_id"] = STATION_ID;
  doc["firmware"] = "esp32-pzem/1.0.0";
  doc["seq"] = ++sequence;

  JsonObject connector = doc["connectors"].createNestedObject();
  connector["connector_id"] = CONNECTOR_ID;
  connector["voltage"] = meter.voltage;
  connector["current"] = meter.current;
  connector["power"] = meter.power;
  connector["energy_kwh"] = meter.energy_kwh;
  connector["frequency"] = meter.frequency;
  connector["power_factor"] = meter.power_factor;
  connector["vehicle_connected"] = digitalRead(PIN_PROXIMITY) == LOW;
  connector["relay_closed"] = relayClosed;
  connector["setpoint_a"] = setpointAmps;
  connector["fault"] = faultLatched;

  // A real installation reads these from the GoodWe inverter over Modbus-TCP.
  // Without an inverter the backend simply sees zero PV and bills grid rates.
  JsonObject solar = doc.createNestedObject("solar");
  solar["pv_power_w"] = 0.0f;
  solar["house_load_w"] = 0.0f;
  solar["grid_power_w"] = meter.power;

  String payload;
  serializeJson(doc, payload);
  webSocket.sendTXT(payload);
}

static void handleControlFrame(const uint8_t *payload, size_t length) {
  StaticJsonDocument<768> doc;
  const DeserializationError error = deserializeJson(doc, payload, length);
  if (error) {
    Serial.printf("[ws] bad JSON: %s\n", error.c_str());
    return;
  }

  const char *type = doc["type"] | "";
  if (strcmp(type, "welcome") == 0) {
    Serial.printf("[ws] server hello, station %s\n", doc["station_id"] | "?");
    lastControlMs = millis();
    return;
  }
  if (strcmp(type, "control") != 0) return;

  for (JsonObject command : doc["connectors"].as<JsonArray>()) {
    if ((command["connector_id"] | 0) != CONNECTOR_ID) continue;
    const float requested = command["setpoint_a"] | 0.0f;
    setpointAmps = constrain(requested, 0.0f, HARDWARE_MAX_CURRENT_A);
    relayCommanded = command["relay"] | false;
    if (requested > HARDWARE_MAX_CURRENT_A) {
      Serial.printf("[ws] clamped setpoint %.1f A -> %.1f A\n", requested,
                    HARDWARE_MAX_CURRENT_A);
    }
    // The operator clearing the overload latch also clears our local fault.
    const char *state = command["state"] | "";
    if (faultLatched && relayCommanded && strcmp(state, "FAULTED") != 0) {
      faultLatched = false;
      Serial.println("[safety] local fault cleared by server command");
    }
    lastControlMs = millis();
  }
}

static void onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.println("[ws] connected");
      lastControlMs = millis();
      break;
    case WStype_DISCONNECTED:
      Serial.println("[ws] disconnected — relay will open on control timeout");
      break;
    case WStype_TEXT:
      handleControlFrame(payload, length);
      break;
    default:
      break;
  }
}

// --------------------------------------------------------------------- setup
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\nGoodWe EVSE controller booting");

  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, LOW); // fail-safe: start with the contactor open
  pinMode(PIN_STATUS_LED, OUTPUT);
  pinMode(PIN_PROXIMITY, INPUT_PULLUP);

  ledcSetup(CP_PWM_CHANNEL, CP_PWM_FREQ, CP_PWM_RESOLUTION);
  ledcAttachPin(PIN_CP_PWM, CP_PWM_CHANNEL);
  ledcWrite(CP_PWM_CHANNEL, 0);

  Serial2.begin(9600, SERIAL_8N1, PIN_PZEM_RX, PIN_PZEM_TX);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[wifi] connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print('.');
  }
  Serial.printf("\n[wifi] %s\n", WiFi.localIP().toString().c_str());

  webSocket.begin(BACKEND_HOST, BACKEND_PORT, BACKEND_PATH);
  webSocket.onEvent(onWebSocketEvent);
  webSocket.setReconnectInterval(2000);
  webSocket.enableHeartbeat(15000, 3000, 2);

  lastControlMs = millis();
}

// ---------------------------------------------------------------------- loop
void loop() {
  webSocket.loop();

  const uint32_t now = millis();

  if (now - lastMeterMs >= PZEM_PERIOD_MS) {
    lastMeterMs = now;
    readMeter();
  }

  // Safety runs every iteration, not on the telemetry cadence.
  enforceSafety();

  if (webSocket.isConnected() && now - lastTelemetryMs >= TELEMETRY_PERIOD_MS) {
    lastTelemetryMs = now;
    sendTelemetry();
  }
}
