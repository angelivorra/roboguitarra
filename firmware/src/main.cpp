#include <Arduino.h>

// ============================================================
//  MODO:  1 = calibración guiada traste a traste
//         0 = modo normal (guitarra MIDI USB)
//
//  DEBUG: 1 = logs completos (latido, eventos, joystick)
//         0 = solo MIDI ON / MIDI OFF
// ============================================================
#define MODO_CALIBRACION 0
#define DEBUG 0

#if DEBUG
  #define DBG(x)   Serial.print(x)
  #define DBGLN(x) Serial.println(x)
#else
  #define DBG(x)
  #define DBGLN(x)
#endif

// --- Hardware -----------------------------------------------
// Preparado para 3 cuerdas: amplía los arrays y NUM_CUERDAS.
const uint8_t NUM_CUERDAS = 1;
const uint8_t NUM_TRASTES = 17;
const uint8_t PIN_SENSOR[NUM_CUERDAS] = { A0 };
const uint8_t PIN_BOTON[NUM_CUERDAS]  = { 4 };   // arcade a GND, INPUT_PULLUP

// Joystick analógico (global, no por cuerda)
const uint8_t PIN_JOY_PITCH = A4;  // eje pitch bend
const uint8_t PIN_JOY_CC    = A5;  // eje CC (reverb / chorus)
const uint8_t PIN_JOY_BTN   = 6;   // pulsador del stick (sin función aún)

// Nota MIDI de cada cuerda al aire. Cuerda 2 de guitarra = Si3.
// Para 3 cuerdas (p. ej. Sol-Si-Mi): { 55, 59, 64 }.
const uint8_t NOTA_AIRE[NUM_CUERDAS] = { 59 };

const uint8_t CANAL_MIDI = 0;    // canal 1
const uint8_t VELOCIDAD  = 100;

// CC que envía cada dirección del eje A5. FluidSynth los aplica
// de serie: 91 = envío de reverb, 93 = envío de chorus.
const uint8_t CC_DIR_A = 91;
const uint8_t CC_DIR_B = 93;

// Zona muerta alrededor del centro del joystick (cuentas ADC) y
// cadencia máxima de envío de bend/CC.
const int JOY_ZONA_MUERTA = 40;
const unsigned long JOY_INTERVALO_MS = 10;

// Tabla de calibración: lectura en el centro de cada traste.
// Todas las cuerdas se conectan igual, así que es común.
const int CALIB[NUM_TRASTES] = {
  998, 916, 828, 756, 681, 613, 551, 503, 438,
  391, 344, 291, 250, 208, 169, 133, 99
};

// --- Umbrales de pulsación ----------------------------------
const int UMBRAL_PULSA  = 55;
const int UMBRAL_SUELTA = 35;

// Histéresis de traste: margen extra sobre la frontera para no
// bailar entre notas.
const int MARGEN_HISTERESIS = 6;

// Muestras consecutivas para confirmar cada evento (~2 ms/muestra).
// SUELTA es deliberadamente lenta (~50 ms): al deslizar el dedo, el
// SoftPot pierde contacto unos ms y no queremos un falso "dedo fuera".
// El cambio de traste NO se confirma: es inmediato (la histéresis ya
// evita el baile entre notas), para no saltarnos trastes al deslizar.
const uint8_t CONFIRMA_PULSA  = 2;
const uint8_t CONFIRMA_SUELTA = 25;
const uint8_t CONFIRMA_BOTON  = 5;   // ~10 ms de antirrebote

// ============================================================
#if MODO_CALIBRACION
// ============================================================

int calib[NUM_CUERDAS][NUM_TRASTES];
const int NUM_MUESTRAS = 30;

int leerEstable(uint8_t pin) {
  long suma = 0;
  for (int i = 0; i < NUM_MUESTRAS; i++) {
    suma += analogRead(pin);
    delay(8);
  }
  return (int)(suma / NUM_MUESTRAS);
}

void setup() {
  Serial.begin(9600);
  while (!Serial);

  Serial.println(F("=== CALIBRACION DEL MASTIL ==="));
  Serial.println(F("Pon el dedo en el CENTRO de cada traste"));
  Serial.println(F("cuando te lo pida, y mantenlo hasta el OK."));
  Serial.println();

  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    Serial.print(F("--- CUERDA "));
    Serial.print(c + 1);
    Serial.print(F(" (A"));
    Serial.print(c);
    Serial.println(F(") ---"));

    for (uint8_t t = 0; t < NUM_TRASTES; t++) {
      Serial.print(F("  Traste "));
      if (t + 1 < 10) Serial.print(' ');
      Serial.print(t + 1);
      Serial.print(F(": pon el dedo..."));

      while (analogRead(PIN_SENSOR[c]) < UMBRAL_PULSA) delay(10);
      delay(80);

      calib[c][t] = leerEstable(PIN_SENSOR[c]);

      Serial.print(F(" OK ("));
      Serial.print(calib[c][t]);
      Serial.println(F(") - suelta"));

      while (analogRead(PIN_SENSOR[c]) > UMBRAL_SUELTA) delay(10);
      delay(150);
    }
    Serial.println();
  }

  Serial.println(F("========  RESULTADO CSV  ========"));
  Serial.print(F("traste"));
  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    Serial.print(F(",cuerda"));
    Serial.print(c + 1);
  }
  Serial.println();
  for (uint8_t t = 0; t < NUM_TRASTES; t++) {
    Serial.print(t + 1);
    for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
      Serial.print(',');
      Serial.print(calib[c][t]);
    }
    Serial.println();
  }
  Serial.println(F("============  FIN  =============="));
}

void loop() {}

// ============================================================
#else
// ============================================================

#include <MIDIUSB.h>

int FRONTERA[NUM_TRASTES - 1];

struct EstadoCuerda {
  // Sensor
  bool    pulsado;
  int8_t  traste;          // 0..16 = trastes 1..17, -1 = sin dedo
  uint8_t cuentaPulsa;
  uint8_t cuentaSuelta;
  // Botón arcade
  bool    botonEstado;     // estado confirmado (true = pisado)
  uint8_t cuentaBoton;
  // MIDI
  bool    activa;          // cuerda sonando
  int8_t  notaSonando;     // nota MIDI activa, -1 = ninguna
};
EstadoCuerda cuerda[NUM_CUERDAS];

// Estado del joystick
int           joyCentroPitch = 512;
int           joyCentroCC    = 512;
int           joyUltimoBend  = 8192;
uint8_t       joyCCActivo    = 0;     // 0 = ninguno
uint8_t       joyUltimoValor = 0;
bool          joyBtnEstado   = false;
uint8_t       joyCuentaBtn   = 0;
unsigned long joyUltimoMs    = 0;

// --- MIDI USB ------------------------------------------------
void notaOn(uint8_t nota) {
  midiEventPacket_t ev = { 0x09, (uint8_t)(0x90 | CANAL_MIDI), nota, VELOCIDAD };
  MidiUSB.sendMIDI(ev);
  Serial.print(F("MIDI ON  "));
  Serial.println(nota);
}

void notaOff(uint8_t nota) {
  midiEventPacket_t ev = { 0x08, (uint8_t)(0x80 | CANAL_MIDI), nota, 0 };
  MidiUSB.sendMIDI(ev);
  Serial.print(F("MIDI OFF "));
  Serial.println(nota);
}

void enviaBend(int v) {
  // 14 bits: 0..16383, centro 8192
  midiEventPacket_t ev = { 0x0E, (uint8_t)(0xE0 | CANAL_MIDI),
                           (uint8_t)(v & 0x7F), (uint8_t)((v >> 7) & 0x7F) };
  MidiUSB.sendMIDI(ev);
}

void enviaCC(uint8_t cc, uint8_t valor) {
  midiEventPacket_t ev = { 0x0B, (uint8_t)(0xB0 | CANAL_MIDI), cc, valor };
  MidiUSB.sendMIDI(ev);
}

// Nota que corresponde al estado actual del dedo en la cuerda c.
uint8_t notaActual(uint8_t c) {
  EstadoCuerda &e = cuerda[c];
  return e.pulsado ? NOTA_AIRE[c] + e.traste + 1  // traste 1..17
                   : NOTA_AIRE[c];                // al aire
}

// Suena `nueva` y apaga la anterior (legato: primero on, luego off).
void cambiaNota(EstadoCuerda &e, uint8_t nueva) {
  int8_t anterior = e.notaSonando;
  notaOn(nueva);
  if (anterior >= 0 && anterior != (int8_t)nueva) notaOff(anterior);
  e.notaSonando = nueva;
  MidiUSB.flush();
}

void apagaCuerda(EstadoCuerda &e) {
  if (e.notaSonando >= 0) {
    notaOff(e.notaSonando);
    MidiUSB.flush();
  }
  e.notaSonando = -1;
  e.activa = false;
}

// --- Detección de traste --------------------------------------
int8_t trasteCrudo(int valor) {
  for (uint8_t i = 0; i < NUM_TRASTES - 1; i++) {
    if (valor > FRONTERA[i]) return i;
  }
  return NUM_TRASTES - 1;
}

int8_t trasteConHisteresis(int valor, int8_t actual) {
  if (actual < 0) return trasteCrudo(valor);
  int limSup = (actual == 0)               ? 1024
               : FRONTERA[actual - 1] + MARGEN_HISTERESIS;
  int limInf = (actual == NUM_TRASTES - 1) ? 0
               : FRONTERA[actual] - MARGEN_HISTERESIS;
  if (valor <= limSup && valor >= limInf) return actual;
  return trasteCrudo(valor);
}

// --- Joystick --------------------------------------------------
// Cada JOY_INTERVALO_MS: eje A4 -> pitch bend proporcional a la
// desviación (centro = sin bend); eje A5 -> CC91 hacia un lado,
// CC93 hacia el otro, valor 0 al volver al centro.
void procesaJoystick() {
  unsigned long ahora = millis();
  if (ahora - joyUltimoMs < JOY_INTERVALO_MS) return;
  joyUltimoMs = ahora;
  bool huboEnvio = false;

  // ---- Eje pitch (A4) ----
  int desv = analogRead(PIN_JOY_PITCH) - joyCentroPitch;
  int bend = 8192;
  if (desv > JOY_ZONA_MUERTA) {
    bend = 8192 + (int)((long)(desv - JOY_ZONA_MUERTA) * 8191
                        / (1023 - joyCentroPitch - JOY_ZONA_MUERTA));
  } else if (desv < -JOY_ZONA_MUERTA) {
    bend = 8192 - (int)((long)(-desv - JOY_ZONA_MUERTA) * 8192
                        / (joyCentroPitch - JOY_ZONA_MUERTA));
  }
  bend = constrain(bend, 0, 16383);
  if (bend != joyUltimoBend) {
    enviaBend(bend);
    joyUltimoBend = bend;
    huboEnvio = true;
  }

  // ---- Eje CC (A5) ----
  desv = analogRead(PIN_JOY_CC) - joyCentroCC;
  uint8_t cc = 0;
  int val = 0;
  if (desv > JOY_ZONA_MUERTA) {
    cc  = CC_DIR_A;
    val = (int)((long)(desv - JOY_ZONA_MUERTA) * 127
                / (1023 - joyCentroCC - JOY_ZONA_MUERTA));
  } else if (desv < -JOY_ZONA_MUERTA) {
    cc  = CC_DIR_B;
    val = (int)((long)(-desv - JOY_ZONA_MUERTA) * 127
                / (joyCentroCC - JOY_ZONA_MUERTA));
  }
  val = constrain(val, 0, 127);

  if (cc != joyCCActivo) {
    // Cambio de dirección o vuelta al centro: apaga el CC anterior
    if (joyCCActivo != 0) {
      enviaCC(joyCCActivo, 0);
      huboEnvio = true;
    }
    joyCCActivo    = cc;
    joyUltimoValor = 255;  // fuerza el primer envío del nuevo CC
  }
  if (cc != 0 && (uint8_t)val != joyUltimoValor) {
    enviaCC(cc, (uint8_t)val);
    joyUltimoValor = (uint8_t)val;
    huboEnvio = true;
  }

  // ---- Botón del stick (D6): sin función, solo log DEBUG ----
  bool b = (digitalRead(PIN_JOY_BTN) == LOW);
  if (b != joyBtnEstado) {
    if (++joyCuentaBtn >= CONFIRMA_BOTON) {
      joyBtnEstado = b;
      joyCuentaBtn = 0;
      DBG(F("JOY BTN "));
      DBGLN(joyBtnEstado ? F("PISADO") : F("soltado"));
    }
  } else {
    joyCuentaBtn = 0;
  }

  if (huboEnvio) MidiUSB.flush();
}

void setup() {
  Serial.begin(115200);
  // No bloqueamos esperando al monitor: el MIDI debe funcionar
  // aunque nadie esté mirando el serie.

  for (uint8_t i = 0; i < NUM_TRASTES - 1; i++) {
    FRONTERA[i] = (CALIB[i] + CALIB[i + 1]) / 2;
  }
  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    pinMode(PIN_BOTON[c], INPUT_PULLUP);
    cuerda[c] = { false, -1, 0, 0, false, 0, false, -1 };
  }
  pinMode(PIN_JOY_BTN, INPUT_PULLUP);

  // Centro real del joystick: promedio con el stick en reposo.
  long s1 = 0, s2 = 0;
  for (uint8_t i = 0; i < 16; i++) {
    s1 += analogRead(PIN_JOY_PITCH);
    s2 += analogRead(PIN_JOY_CC);
    delay(5);
  }
  joyCentroPitch = (int)(s1 / 16);
  joyCentroCC    = (int)(s2 / 16);

  delay(1500);
  Serial.println(F("Roboguitarra lista (modo normal, 17 trastes)"));
}

#if DEBUG
// Latido de depuración: valor crudo y estado cada 500 ms.
unsigned long ultimoLatido = 0;

void latido() {
  if (millis() - ultimoLatido < 500) return;
  ultimoLatido = millis();
  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    EstadoCuerda &e = cuerda[c];
    Serial.print(F("[c"));
    Serial.print(c + 1);
    Serial.print(F("] valor="));
    Serial.print(analogRead(PIN_SENSOR[c]));
    Serial.print(F(" boton="));
    Serial.print(digitalRead(PIN_BOTON[c]) == LOW ? F("PISADO") : F("suelto"));
    Serial.print(F(" pulsado="));
    Serial.print(e.pulsado);
    Serial.print(F(" traste="));
    Serial.print(e.traste + 1);
    Serial.print(F(" activa="));
    Serial.print(e.activa);
    Serial.print(F(" nota="));
    Serial.print(e.notaSonando);
    Serial.print(F(" | joy pitch="));
    Serial.print(analogRead(PIN_JOY_PITCH));
    Serial.print(F(" cc="));
    Serial.print(analogRead(PIN_JOY_CC));
    Serial.print(F(" bend="));
    Serial.print(joyUltimoBend);
    Serial.print(F(" ccActivo="));
    Serial.println(joyCCActivo);
  }
}
#else
void latido() {}
#endif

void loop() {
  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    EstadoCuerda &e = cuerda[c];

    // ---------- 1. Sensor: seguir el dedo ----------
    int valor = analogRead(PIN_SENSOR[c]);

    if (!e.pulsado) {
      if (valor > UMBRAL_PULSA) {
        if (++e.cuentaPulsa >= CONFIRMA_PULSA) {
          e.pulsado = true;
          e.traste  = trasteCrudo(valor);
          e.cuentaPulsa = e.cuentaSuelta = 0;
          DBG(F("PULSA c1 traste "));
          DBG(e.traste + 1);
          DBG(F(" (valor="));
          DBG(valor);
          DBGLN(F(")"));
          // Dedo sobre cuerda activa al aire: liga a la nota del traste
          if (e.activa) cambiaNota(e, notaActual(c));
        }
      } else {
        e.cuentaPulsa = 0;
      }

    } else {
      if (valor < UMBRAL_SUELTA) {
        if (++e.cuentaSuelta >= CONFIRMA_SUELTA) {
          e.pulsado = false;
          e.traste = -1;
          e.cuentaPulsa = e.cuentaSuelta = 0;
          DBGLN(F("SUELTA c1"));
          // Quitar el dedo desactiva la cuerda
          if (e.activa) apagaCuerda(e);
        }
      } else {
        e.cuentaSuelta = 0;

        // Cambio de traste inmediato: cada lectura que caiga en otro
        // traste (superando la histéresis) dispara la nota nueva.
        int8_t t = trasteConHisteresis(valor, e.traste);
        if (t != e.traste) {
          e.traste = t;
          DBG(F("CAMBIO c1 traste "));
          DBGLN(t + 1);
          // Deslizar con cuerda activa: nueva nota on, anterior off
          if (e.activa) cambiaNota(e, notaActual(c));
        }
      }
    }

    // ---------- 2. Botón arcade: disparar la cuerda ----------
    bool lecturaBoton = (digitalRead(PIN_BOTON[c]) == LOW);
    if (lecturaBoton != e.botonEstado) {
      if (++e.cuentaBoton >= CONFIRMA_BOTON) {
        e.botonEstado = lecturaBoton;
        e.cuentaBoton = 0;
        DBG(F("BOTON c1 "));
        DBGLN(e.botonEstado ? F("PISADO") : F("soltado"));
        if (e.botonEstado) {
          // Flanco de pisada: activa la cuerda y dispara la nota
          // (con dedo = nota del traste; sin dedo = cuerda al aire)
          e.activa = true;
          cambiaNota(e, notaActual(c));
        }
        // Soltar el botón no hace nada: la nota sigue hasta
        // que se levante el dedo del sensor.
      }
    } else {
      e.cuentaBoton = 0;
    }
  }

  // ---------- 3. Joystick: pitch bend + CC ----------
  procesaJoystick();

  latido();
  delay(2);
}

#endif
