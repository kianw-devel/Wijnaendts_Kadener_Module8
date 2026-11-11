// Module 8: Six-channel rod acquisition + Python-controlled TEC interlock
// PI runs in the Python GUI; this sketch applies its PWM/direction commands.
// Test with TEC power disconnected before energizing the H-bridge.
// Software limits do not replace the existing hardware safety provisions.

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

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

// FRAME carries one coherent command plus metadata from the existing host PI.
// All absent numerical fields are nan; all modes use the SAME DATA layout.
char commandBuffer[240];
int commandLength = 0;
bool commandOverflow = false;
bool commandError = false;
char modeCode[16] = "MANUAL";
bool running = false;
unsigned long segment = 0, commandSequence = 0, lastCommandMs = 0;
const unsigned long COMMAND_TIMEOUT_MS = 4000;
float ta = NAN, tb = NAN, period = NAN, setpoint = NAN;
float feedforward = NAN, controller = NAN, coolingRatio = NAN;
float kp = NAN, ki = NAN, ua = NAN, ub = NAN;

// Freeze the entire row before transmitting. While bytes leave the Uno slowly,
// keep receiving long FRAME commands without overflowing the 64-byte RX buffer.
// A newly received frame can affect the NEXT acquisition, never this frozen row.
void readSerialCommands();
class RecordWriter {
  char buffer[450];
  size_t length=0;
public:
  void begin() { length=0; buffer[0]='\0'; }
  void print(const char *text) {
    while (*text && length<sizeof(buffer)-2) buffer[length++]=*text++;
    buffer[length]='\0';
  }
  void print(const __FlashStringHelper *text) {
    const char *p=(const char *)text; char c;
    while ((c=pgm_read_byte(p++)) && length<sizeof(buffer)-2) buffer[length++]=c;
    buffer[length]='\0';
  }
  void print(char c) { char t[]={c,0};print(t); }
  void print(unsigned long value) { char t[12];ultoa(value,t,10);print(t); }
  void print(int value) { char t[8];itoa(value,t,10);print(t); }
  void print(float value,int digits) { char t[48];dtostrf(value,0,digits,t);print(t); }
  void println(unsigned long value) { print(value);print('\n'); }
  void send() {
    for (size_t i=0;i<length;) {
      readSerialCommands();
      if (Serial.availableForWrite()>0) Serial.write(buffer[i++]);
    }
  }
} record;

bool validMode(const char *mode) {
  return strcmp(mode,"MANUAL")==0 || strcmp(mode,"RAW_SINE")==0 ||
         strcmp(mode,"RAW_HEAT")==0 || strcmp(mode,"FEEDFORWARD")==0 ||
         strcmp(mode,"P_ONLY")==0 || strcmp(mode,"PI")==0;
}

bool number(const char *text, float &result) {
  char *end;
  result = strtod(text, &end);
  return end != text && *end == '\0' && !isinf(result);
}

void acceptCommand() {
  if (strncmp(commandBuffer, "FRAME,", 6) == 0) {
    char *fields[17];
    int count=0;
    char *token=strtok(commandBuffer, ",");
    while (token && count<17) { fields[count++]=token; token=strtok(NULL, ","); }
    if (count!=17 || token || !validMode(fields[2]) ||
        (strcmp(fields[1],"RUNNING")!=0 && strcmp(fields[1],"STOPPED")!=0)) {
      commandError=true; return;
    }
    // Parse into temporary values. Never partially change an active command.
    float v[14];
    for (int i=3; i<17; ++i) {
      if (!number(fields[i],v[i-3])) { commandError=true; return; }
    }
    if (!isfinite(v[0]) || v[0]<0 || floor(v[0])!=v[0] ||
        !isfinite(v[8]) || abs(v[8])>255 || floor(v[8])!=v[8] ||
        !isfinite(v[13]) || v[13]<0 || floor(v[13])!=v[13]) {
      commandError=true; return;
    }
    running=strcmp(fields[1],"RUNNING")==0;
    strncpy(modeCode,fields[2],sizeof(modeCode)-1);
    segment=(unsigned long)v[0];
    ta=v[1];tb=v[2];period=v[3];setpoint=v[4];feedforward=v[5];
    controller=v[6];coolingRatio=v[7];
    int signedCommand=(int)v[8];
    kp=v[9];ki=v[10];ua=v[11];ub=v[12];commandSequence=(unsigned long)v[13];
    direction=signedCommand<0 ? HIGH : LOW;
    pwmCommand=running ? abs(signedCommand) : 0;
    serialControl=true;lastCommandMs=millis();
    return;
  }
  // Legacy manual commands remain accepted, with metadata marked unavailable.
  int requestedPWM;char requestedDirection[5];
  if (sscanf(commandBuffer,"SET PWM %d DIR %4s",&requestedPWM,requestedDirection)==2 &&
      requestedPWM>=0 && requestedPWM<=255 &&
      (strcmp(requestedDirection,"HEAT")==0 || strcmp(requestedDirection,"COOL")==0)) {
    pwmCommand=requestedPWM;direction=strcmp(requestedDirection,"COOL")==0 ? HIGH : LOW;
    running=pwmCommand!=0;strcpy(modeCode,"MANUAL");
    ta=tb=period=setpoint=feedforward=controller=coolingRatio=kp=ki=ua=ub=NAN;
    serialControl=true;lastCommandMs=millis();
  } else commandError=true;
}

void readSerialCommands() {
  while (Serial.available()>0) {
    char c=Serial.read();
    if (c=='\n' || c=='\r') {
      if (commandLength && !commandOverflow) {
        commandBuffer[commandLength]='\0';acceptCommand();
      } else if (commandOverflow) commandError=true;
      commandLength=0;commandOverflow=false;
    } else if (!commandOverflow && commandLength<(int)sizeof(commandBuffer)-1) {
      commandBuffer[commandLength++]=c;
    } else commandOverflow=true;
  }
}

// Average 1000 thermistor ADC readings
float averageThermistorADC(int pin) {

  float totalADC = 0;

  for (int i = 0; i < numSamples; i++) {
    totalADC += analogRead(pin);
    // Service serial while averaging: a FRAME exceeds Uno's 64-byte RX buffer.
    if (i % 16 == 0) readSerialCommands();
  }

  return totalADC / numSamples;
}


// ADC -> voltage
float adcToVoltage(float averageADC) {

  return averageADC * Vref / 1023.0;
}


// Voltage -> resistance
float voltageToResistance(float voltage) {

  if (voltage <= 0 || voltage >= Vref) return NAN;
  return fixedResistance * voltage / (Vref - voltage);
}


// Resistance -> Celsius
float resistanceToCelsius(float resistance) {

  if (!isfinite(resistance) || resistance <= 0) return NAN;
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
  Serial.println(F("DATA,time_ms,run_state,control_mode,step,Ta_C,Tb_C,period_s,setpoint_C,feedforward_pwm,TEC_C,T1_C,T2_C,T3_C,T4_C,T5_C,controller_pwm,heating_to_cooling_ratio,signed_pwm,safety,kp,ki,ua_pwm,ub_pwm,command_seq"));
}


void loop() {

  // Read commands from Python GUI
  readSerialCommands();
  if (commandError) { Serial.println(F("ERROR rejected malformed command")); commandError=false; }

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
  unsigned long acquisitionMs = millis();
  bool commandTimedOut = running && acquisitionMs-lastCommandMs > COMMAND_TIMEOUT_MS;
  if (commandTimedOut) { pwmCommand=0;running=false; }

  // A1 is now a rod thermistor, so it cannot be a potentiometer fallback.
  // pwmCommand starts at zero and stays there until a valid Python command.

  // ---------- Software safety check ----------

  bool safetyShutdown = false;

  if (!isfinite(temperatureC) || temperatureC <= LOW_TEMP_LIMIT ||
      temperatureC >= HIGH_TEMP_LIMIT || (isfinite(temperaturesC[1]) && temperaturesC[1]>=56.0)) {

    // Temperature is outside the safe range:
    // force BOTH H-bridge PWM outputs to zero.
    analogWrite(pwmPin9, 0);
    analogWrite(pwmPin10, 0);

    safetyShutdown = true;
    pwmCommand=0;running=false;

  } else {

    // Break before make: never leave both bridge direction channels energized.
    analogWrite(pwmPin9,0);analogWrite(pwmPin10,0);
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


  // One unmodified DATA record pairs all measurements with the frame active at
  // acquisition completion. Python's NEXT control decision is a later frame.
  // Print nan explicitly for inapplicable fields; never change column count.
  record.begin();
  record.print(F("DATA,"));record.print(acquisitionMs);record.print(',');
  record.print(safetyShutdown ? F("SAFETY") : commandTimedOut ? F("TIMEOUT") : running ? F("RUNNING") : F("STOPPED"));
  record.print(',');record.print(modeCode);record.print(',');record.print(segment);
  csvNumber(ta);csvNumber(tb);csvNumber(period);csvNumber(setpoint);csvNumber(feedforward);
  for (int channel=0;channel<numThermistors;++channel) csvNumber(temperaturesC[channel]);
  csvNumber(controller);csvNumber(coolingRatio);record.print(',');
  record.print(safetyShutdown || commandTimedOut ? 0 : (direction==HIGH ? -pwmCommand : pwmCommand));
  record.print(',');record.print(safetyShutdown ? F("SHUTDOWN") : commandTimedOut ? F("TIMEOUT") : F("OK"));
  csvNumber(kp);csvNumber(ki);csvNumber(ua);csvNumber(ub);
  record.print(',');record.println(commandSequence);
  record.send();
}

void csvNumber(float value) {
  record.print(',');
  if (!isfinite(value)) record.print(F("nan"));
  else record.print(value,6);
}
