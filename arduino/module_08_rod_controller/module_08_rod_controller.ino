// Module 7: Six-channel rod acquisition + Python-controlled TEC interlock
// PI runs in the Python GUI; this sketch applies its PWM/direction commands.
// Test with TEC power disconnected before energizing the H-bridge.
// Software limits do not replace the existing hardware safety provisions.

#include <stdio.h>
#include <string.h>

// ---------- Pins ----------
// A0 is the TEC-base feedback/safety sensor. A1-A5 only measure the rod.
// Wire each channel with the same thermistor-divider circuit as Module 6.
const int thermistorPin = A0;
const int thermistorPins[] = {thermistorPin, A1, A2, A3, A4, A5};
const int numThermistors = sizeof(thermistorPins) / sizeof(thermistorPins[0]);

const int pwmPin9 = 9;
const int pwmPin10 = 10;
const int directionPin = 11;

// ---------- Thermistor constants ----------
const float Vref = 5.0;
const float fixedResistance = 100.0; // kOhm
const float R0 = 100.0;              // kOhm at 25 C
const float T0 = 298.15;             // 25 C in Kelvin
const float beta = 4540.0;           // K

// Average 1000 raw thermistor measurements
const int numSamples = 1000;

// ---------- Software safety ----------
const float LOW_TEMP_LIMIT = 5.0;    // degrees C
const float HIGH_TEMP_LIMIT = 60.0;  // degrees C

// ---------- Control variables ----------
int pwmCommand = 0;
int direction = LOW;
bool serialControl = false;

char commandBuffer[40];
int commandLength = 0;


// Read commands sent by Python GUI
void readSerialCommands() {

  while (Serial.available() > 0) {

    char incomingCharacter = Serial.read();

    if (incomingCharacter == '\n' || incomingCharacter == '\r') {

      if (commandLength == 0) {
        continue;
      }

      commandBuffer[commandLength] = '\0';

      int requestedPWM;
      char requestedDirection[5];

      int valuesRead = sscanf(
          commandBuffer,
          "SET PWM %d DIR %4s",
          &requestedPWM,
          requestedDirection
      );

      if (valuesRead == 2 &&
          requestedPWM >= 0 &&
          requestedPWM <= 255) {

        if (strcmp(requestedDirection, "HEAT") == 0) {
          direction = LOW;

        } else if (strcmp(requestedDirection, "COOL") == 0) {
          direction = HIGH;

        } else {
          commandLength = 0;
          continue;
        }

        pwmCommand = requestedPWM;
        serialControl = true;
      }

      commandLength = 0;

    } else if (commandLength < sizeof(commandBuffer) - 1) {

      commandBuffer[commandLength] = incomingCharacter;
      commandLength++;
    }
  }
}


// Average 1000 thermistor ADC readings
float averageThermistorADC(int pin) {

  float totalADC = 0;

  for (int i = 0; i < numSamples; i++) {
    totalADC += analogRead(pin);
  }

  return totalADC / numSamples;
}


// ADC -> voltage
float adcToVoltage(float averageADC) {

  return averageADC * Vref / 1023.0;
}


// Voltage -> resistance
float voltageToResistance(float voltage) {

  return fixedResistance * voltage / (Vref - voltage);
}


// Resistance -> Celsius
float resistanceToCelsius(float resistance) {

  float temperatureK =
      1.0 / ((1.0 / T0) +
      (1.0 / beta) * log(resistance / R0));

  return temperatureK - 273.15;
}


void setup() {

  Serial.begin(9600);

  pinMode(pwmPin9, OUTPUT);
  pinMode(pwmPin10, OUTPUT);
  pinMode(directionPin, INPUT);

  // Safe startup: TEC OFF
  analogWrite(pwmPin9, 0);
  analogWrite(pwmPin10, 0);

  // Print column names once; later DATA lines contain measurements only.
  Serial.println("DATA,time_s,tec_C,rod1_C,rod2_C,rod3_C,rod4_C,rod5_C,pwm,heat_cool,setpoint_C,safety");
}


void loop() {

  // Read commands from Python GUI
  readSerialCommands();


  // ---------- Measure temperature ----------

  // Read the six channels sequentially. Each channel gets numSamples readings,
  // averaged before the SAME Module 6 conversion chain is applied.
  // Only temperaturesC[0] (A0) is used for feedback and safety.
  float temperaturesC[numThermistors];
  for (int channel = 0; channel < numThermistors; channel++) {
    float averageADC = averageThermistorADC(thermistorPins[channel]);
    float voltage = adcToVoltage(averageADC);
    float resistance = voltageToResistance(voltage);
    temperaturesC[channel] = resistanceToCelsius(resistance);
  }
  float temperatureC = temperaturesC[0];

  // Timestamp AFTER acquiring all six temperatures, in Arduino elapsed seconds.
  float timeSeconds = millis() / 1000.0;

  // A1 is now a rod thermistor, so it cannot be a potentiometer fallback.
  // pwmCommand starts at zero and stays there until a valid Python command.

  // ---------- Software safety check ----------

  bool safetyShutdown = false;

  if (temperatureC <= LOW_TEMP_LIMIT || temperatureC >= HIGH_TEMP_LIMIT) {

    // Temperature is outside the safe range:
    // force BOTH H-bridge PWM outputs to zero.
    analogWrite(pwmPin9, 0);
    analogWrite(pwmPin10, 0);

    safetyShutdown = true;

  } else {

    // Normal operation

    if (direction == HIGH) {

      // Cooling
      analogWrite(pwmPin9, pwmCommand);
      analogWrite(pwmPin10, 0);

    } else {

      // Heating
      analogWrite(pwmPin9, 0);
      analogWrite(pwmPin10, pwmCommand);
    }
  }


  // ---------- Serial measurement ----------
  // One complete CSV record per acquisition, in the startup header's order.
  // Rod sensors never affect the H-bridge command or the A0 safety limit.
  Serial.print("DATA,");
  Serial.print(timeSeconds, 2);
  for (int channel = 0; channel < numThermistors; channel++) {
    Serial.print(',');
    Serial.print(temperaturesC[channel], 2);
  }
  Serial.print(',');

  // Report actual applied PWM: the interlock forces it to zero on shutdown.
  if (safetyShutdown) {
    Serial.print(0);
  } else {
    Serial.print(pwmCommand);
  }
  Serial.print(',');
  Serial.print(direction == LOW ? 1 : 0);  // 1 = heating, 0 = cooling

  // The unchanged SET PWM ... DIR ... protocol carries no setpoint.
  // Python owns PI and its setpoint; nan explicitly means unavailable here.
  Serial.print(",nan,");
  Serial.println(safetyShutdown ? "SHUTDOWN" : "OK");

  // Diagnostics must not begin with DATA.
  if (safetyShutdown) {
    Serial.println("SAFETY SHUTDOWN ACTIVE");
  }
}
