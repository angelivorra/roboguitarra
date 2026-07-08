# Arduino Project Patterns Reference

Common code patterns and project organization strategies for Arduino development.

## Non-Blocking Timing (millis-based)

Avoid `delay()` for timing when the sketch needs to handle multiple tasks. Use `millis()` instead:

```cpp
unsigned long previousMillis = 0;
const unsigned long interval = 1000; // 1 second

void loop() {
  unsigned long currentMillis = millis();

  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    // Action to perform every 'interval' milliseconds
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }

  // Other code runs without blocking
}
```

Use this pattern for: LED blinking, sensor polling, display updates, any periodic task that should not block the main loop.

## State Machine Pattern

Organize complex behavior as discrete states:

```cpp
enum State {
  IDLE,
  READING_SENSOR,
  PROCESSING,
  SENDING_DATA,
  ERROR
};

State currentState = IDLE;

void loop() {
  switch (currentState) {
    case IDLE:
      if (buttonPressed()) {
        currentState = READING_SENSOR;
      }
      break;

    case READING_SENSOR:
      int value = analogRead(A0);
      if (value > 0) {
        currentState = PROCESSING;
      } else {
        currentState = ERROR;
      }
      break;

    case PROCESSING:
      // Process data
      currentState = SENDING_DATA;
      break;

    case SENDING_DATA:
      Serial.println(processedData);
      currentState = IDLE;
      break;

    case ERROR:
      Serial.println(F("Error occurred"));
      currentState = IDLE;
      break;
  }
}
```

## Sensor Reading with Averaging

Smooth noisy analog readings with a running average:

```cpp
const int numReadings = 10;
int readings[numReadings];
int readIndex = 0;
long total = 0;

void setup() {
  for (int i = 0; i < numReadings; i++) {
    readings[i] = 0;
  }
}

int smoothRead(int pin) {
  total -= readings[readIndex];
  readings[readIndex] = analogRead(pin);
  total += readings[readIndex];
  readIndex = (readIndex + 1) % numReadings;
  return total / numReadings;
}
```

## Button Debouncing

Handle mechanical switch bounce:

```cpp
const int buttonPin = 2;
const unsigned long debounceDelay = 50;

int buttonState = HIGH;
int lastButtonState = HIGH;
unsigned long lastDebounceTime = 0;

bool readButton() {
  int reading = digitalRead(buttonPin);

  if (reading != lastButtonState) {
    lastDebounceTime = millis();
  }

  if ((millis() - lastDebounceTime) > debounceDelay) {
    if (reading != buttonState) {
      buttonState = reading;
      if (buttonState == LOW) { // Button pressed (active LOW)
        lastButtonState = reading;
        return true;
      }
    }
  }

  lastButtonState = reading;
  return false;
}
```

## Serial Command Parser

Parse text commands from Serial input:

```cpp
String inputBuffer = "";
bool stringComplete = false;

void setup() {
  Serial.begin(9600);
  inputBuffer.reserve(64);
}

void loop() {
  if (stringComplete) {
    inputBuffer.trim();

    if (inputBuffer.startsWith("LED")) {
      int value = inputBuffer.substring(4).toInt();
      analogWrite(LED_BUILTIN, value);
      Serial.println(F("OK"));
    }
    else if (inputBuffer == "STATUS") {
      Serial.print(F("Uptime: "));
      Serial.println(millis());
    }
    else {
      Serial.println(F("Unknown command"));
    }

    inputBuffer = "";
    stringComplete = false;
  }
}

void serialEvent() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      stringComplete = true;
    } else {
      inputBuffer += c;
    }
  }
}
```

## Multi-File Project Organization

Split large projects into logical modules:

```
SensorStation/
├── SensorStation.ino    # Main sketch, setup() and loop()
├── config.h             # Pin definitions, constants
├── sensors.h            # Sensor function declarations
├── sensors.cpp          # Sensor implementations
├── display.h            # Display function declarations
├── display.cpp          # Display implementations
└── network.h            # Network/communication declarations
```

**config.h** -- centralize pin assignments:
```cpp
#ifndef CONFIG_H
#define CONFIG_H

// Pin assignments
#define TEMP_SENSOR_PIN A0
#define HUMIDITY_PIN A1
#define LED_STATUS_PIN 13
#define BUTTON_PIN 2

// Configuration
#define SERIAL_BAUD 9600
#define READING_INTERVAL 5000

#endif
```

**sensors.h** -- module header:
```cpp
#ifndef SENSORS_H
#define SENSORS_H

#include <Arduino.h>

void initSensors();
float readTemperature();
float readHumidity();

#endif
```

**sensors.cpp** -- module implementation:
```cpp
#include "sensors.h"
#include "config.h"

void initSensors() {
  pinMode(TEMP_SENSOR_PIN, INPUT);
  pinMode(HUMIDITY_PIN, INPUT);
}

float readTemperature() {
  int raw = analogRead(TEMP_SENSOR_PIN);
  return (raw * 5.0 / 1024.0 - 0.5) * 100.0; // TMP36 formula
}

float readHumidity() {
  int raw = analogRead(HUMIDITY_PIN);
  return map(raw, 0, 1023, 0, 100);
}
```

## Interrupt-Driven Input

Use hardware interrupts for time-critical input:

```cpp
volatile bool interruptFlag = false;

void setup() {
  pinMode(2, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(2), onInterrupt, FALLING);
}

void onInterrupt() {
  interruptFlag = true; // Keep ISR short -- just set a flag
}

void loop() {
  if (interruptFlag) {
    interruptFlag = false;
    // Handle the interrupt event here (outside ISR)
    Serial.println(F("Interrupt triggered"));
  }
}
```

**Important**: Keep ISR (Interrupt Service Routine) functions short. Do not use `Serial`, `delay()`, or `millis()` inside ISRs. Set a flag and handle it in `loop()`.

## PWM Fading Pattern

Smooth LED or motor control with PWM:

```cpp
const int pwmPin = 9;
int brightness = 0;
int fadeAmount = 5;

void loop() {
  analogWrite(pwmPin, brightness);
  brightness += fadeAmount;

  if (brightness <= 0 || brightness >= 255) {
    fadeAmount = -fadeAmount;
  }

  delay(30);
}
```

## EEPROM Configuration Storage

Persist settings across power cycles:

```cpp
#include <EEPROM.h>

struct Config {
  int threshold;
  int interval;
  char name[16];
  byte checksum;
};

Config config;

void loadConfig() {
  EEPROM.get(0, config);
  // Validate checksum
  byte calc = calculateChecksum();
  if (config.checksum != calc) {
    // Use defaults
    config.threshold = 512;
    config.interval = 1000;
    strcpy(config.name, "default");
    saveConfig();
  }
}

void saveConfig() {
  config.checksum = calculateChecksum();
  EEPROM.put(0, config);
}

byte calculateChecksum() {
  byte sum = 0;
  byte* p = (byte*)&config;
  for (size_t i = 0; i < sizeof(Config) - 1; i++) {
    sum ^= p[i];
  }
  return sum;
}
```
