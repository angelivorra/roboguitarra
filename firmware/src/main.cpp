#include <Arduino.h>

// ============================================================
//  MODO:  1 = calibración guiada traste a traste
//         0 = modo normal (guitarra MIDI USB)
//
//  En modo normal solo se loguean dos eventos del mástil:
//    "Dedo -> traste N"  y  "Dedo fuera".
// ============================================================
#define MODO_CALIBRACION 0

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

const uint8_t VELOCIDAD  = 100;

// Canales MIDI (0..15 en el byte de estado = canales 1..16).
// Las notas se reparten por traste ciclando por estos tres canales:
// trastes contiguos caen en canales distintos, así el solape legato
// suena limpio y quedan preparados para acordes. Ninguno es el 9
// (percusión en General MIDI). El bend y los CC del joystick se
// reemiten en estos mismos canales para que afecten suene donde suene.
const uint8_t CANALES[]    = { 0, 1, 2 };   // = canales MIDI 1, 2 y 3
const uint8_t NUM_CANALES  = sizeof(CANALES) / sizeof(CANALES[0]);

// CC que envía cada dirección del eje A5. FluidSynth los aplica
// de serie: 91 = envío de reverb, 93 = envío de chorus.
const uint8_t CC_DIR_A = 91;
const uint8_t CC_DIR_B = 93;

// Zona muerta alrededor del centro del joystick (cuentas ADC) y
// cadencia máxima de envío de bend/CC.
const int JOY_ZONA_MUERTA = 40;
const unsigned long JOY_INTERVALO_MS = 5;

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

// Antirrebote POR TIEMPO (independiente de la velocidad del bucle).
// SUELTA es deliberadamente lento: al deslizar el dedo, el SoftPot
// pierde contacto unos ms y no queremos un falso "dedo fuera" que
// corte la nota. El cambio de traste NO se confirma: es inmediato
// (la histéresis ya evita el baile), para no saltarnos trastes al
// deslizar.
const unsigned long T_PULSA_MS  = 4;    // confirmar dedo presente
const unsigned long T_SUELTA_MS = 120;  // confirmar dedo fuera (robusto)
                                        // sube si hay falsos "dedo fuera"
                                        // al deslizar; baja si el apagado
                                        // al levantar tarda demasiado.
const unsigned long T_BOTON_MS  = 10;   // antirrebote del botón arcade

// Antirrebote del CAMBIO de traste. Al levantar el dedo, la lectura
// del SoftPot cae hacia 0 y pasa 1-2 ms por la banda de trastes altos:
// exigir que un traste candidato dure T_TRASTE_MS descarta ese
// transitorio (nota espuria alta) sin frenar los slides reales, que se
// demoran mucho más en cada traste. Subir si aún se cuela; bajar si se
// saltan trastes en slides muy rápidos.
const unsigned long T_TRASTE_MS = 8;

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
  bool          pulsado;
  int8_t        traste;        // 0..16 = trastes 1..17, -1 = sin dedo
  int8_t        trastePend;    // traste candidato en curso (antirrebote)
  unsigned long tPulsa;        // ms desde que la lectura supera UMBRAL_PULSA
  unsigned long tSuelta;       // ms desde que cae bajo UMBRAL_SUELTA
  unsigned long tTraste;       // ms desde que apareció el traste candidato
  // Botón arcade
  bool          botonEstado;   // estado confirmado (true = pisado)
  unsigned long tBoton;        // ms desde el último cambio de lectura
  // MIDI
  bool          activa;        // cuerda sonando
  int8_t        notaSonando;   // nota MIDI activa, -1 = ninguna
  uint8_t       canalSonando;  // canal de la nota activa (para el Note Off)
};
EstadoCuerda cuerda[NUM_CUERDAS];

// Estado del joystick
int           joyCentroPitch = 512;
int           joyCentroCC    = 512;
int           joyUltimoBend  = 8192;
uint8_t       joyCCActivo    = 0;     // 0 = ninguno
uint8_t       joyUltimoValor = 0;
unsigned long joyUltimoMs    = 0;

// --- MIDI USB ------------------------------------------------
void notaOn(uint8_t nota, uint8_t canal) {
  midiEventPacket_t ev = { 0x09, (uint8_t)(0x90 | canal), nota, VELOCIDAD };
  MidiUSB.sendMIDI(ev);
}

void notaOff(uint8_t nota, uint8_t canal) {
  midiEventPacket_t ev = { 0x08, (uint8_t)(0x80 | canal), nota, 0 };
  MidiUSB.sendMIDI(ev);
}

// Pitch bend en los tres canales de nota (14 bits: 0..16383, centro 8192).
void enviaBend(int v) {
  for (uint8_t i = 0; i < NUM_CANALES; i++) {
    midiEventPacket_t ev = { 0x0E, (uint8_t)(0xE0 | CANALES[i]),
                             (uint8_t)(v & 0x7F), (uint8_t)((v >> 7) & 0x7F) };
    MidiUSB.sendMIDI(ev);
  }
}

// Control Change en los tres canales de nota.
void enviaCC(uint8_t cc, uint8_t valor) {
  for (uint8_t i = 0; i < NUM_CANALES; i++) {
    midiEventPacket_t ev = { 0x0B, (uint8_t)(0xB0 | CANALES[i]), cc, valor };
    MidiUSB.sendMIDI(ev);
  }
}

// Nota que corresponde al estado actual del dedo en la cuerda c.
uint8_t notaActual(uint8_t c) {
  EstadoCuerda &e = cuerda[c];
  return e.pulsado ? NOTA_AIRE[c] + e.traste + 1  // traste 1..17
                   : NOTA_AIRE[c];                // al aire
}

// Canal MIDI de la nota actual: un canal por traste, ciclando por
// CANALES. Al aire = índice 0; traste t (0..16) = índice t+1.
uint8_t canalActual(uint8_t c) {
  EstadoCuerda &e = cuerda[c];
  uint8_t idx = e.pulsado ? (uint8_t)(e.traste + 1) : 0;
  return CANALES[idx % NUM_CANALES];
}

// Suena `nueva` y apaga la anterior (legato: primero on, luego off).
// El Note Off del anterior sale por SU canal, no por el nuevo.
void cambiaNota(EstadoCuerda &e, uint8_t nueva, uint8_t canal) {
  int8_t  antNota  = e.notaSonando;
  uint8_t antCanal = e.canalSonando;
  notaOn(nueva, canal);
  if (antNota >= 0 && !(antNota == (int8_t)nueva && antCanal == canal))
    notaOff(antNota, antCanal);
  e.notaSonando  = nueva;
  e.canalSonando = canal;
  MidiUSB.flush();
}

void apagaCuerda(EstadoCuerda &e) {
  if (e.notaSonando >= 0) {
    notaOff(e.notaSonando, e.canalSonando);
    MidiUSB.flush();
  }
  e.notaSonando = -1;
  e.activa = false;
}

// --- Detección de traste --------------------------------------
// Mediana de 3 lecturas: rechaza picos sueltos del SoftPot sin
// apenas latencia (~0,3 ms), para no bailar de traste por ruido.
int leerSuavizado(uint8_t pin) {
  int a = analogRead(pin);
  int b = analogRead(pin);
  int c = analogRead(pin);
  return max(min(a, b), min(max(a, b), c));
}

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
    //          pulsado traste trastePend tPulsa tSuelta tTraste boton tBoton activa nota canal
    cuerda[c] = { false, -1,    -1,        0,     0,      0,      false, 0,    false, -1,  0 };
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

void loop() {
  unsigned long ahora = millis();

  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    EstadoCuerda &e = cuerda[c];

    // ---------- 1. Sensor: seguir el dedo ----------
    int valor = leerSuavizado(PIN_SENSOR[c]);

    if (!e.pulsado) {
      // Esperando dedo: confirmar presencia durante T_PULSA_MS.
      if (valor > UMBRAL_PULSA) {
        if (e.tPulsa == 0) e.tPulsa = ahora;
        if (ahora - e.tPulsa >= T_PULSA_MS) {
          e.pulsado = true;
          e.traste  = trasteCrudo(valor);
          e.trastePend = e.traste;   // arranca el antirrebote coherente
          e.tPulsa = e.tSuelta = 0;
          Serial.print(F("Dedo -> traste "));
          Serial.println(e.traste + 1);
          // Dedo sobre cuerda activa al aire: liga a la nota del traste
          if (e.activa) cambiaNota(e, notaActual(c), canalActual(c));
        }
      } else {
        e.tPulsa = 0;
      }

    } else {
      if (valor < UMBRAL_SUELTA) {
        // Posible "dedo fuera": confirmar durante T_SUELTA_MS (largo)
        // para que un microcorte del SoftPot al deslizar no cuente.
        if (e.tSuelta == 0) e.tSuelta = ahora;
        if (ahora - e.tSuelta >= T_SUELTA_MS) {
          e.pulsado = false;
          e.traste = -1;
          e.tPulsa = e.tSuelta = 0;
          Serial.println(F("Dedo fuera"));
          // Levantar el dedo apaga la nota.
          if (e.activa) apagaCuerda(e);
        }
      } else {
        e.tSuelta = 0;

        // Cambio de traste con antirrebote corto: un traste candidato
        // debe sostenerse T_TRASTE_MS antes de disparar la nota. Así el
        // transitorio brevísimo al levantar el dedo (barrido hacia
        // trastes altos) no cuela una nota espuria, y los slides reales
        // (que se demoran más en cada traste) sí pasan.
        int8_t t = trasteConHisteresis(valor, e.traste);
        if (t == e.traste) {
          e.trastePend = t;                 // estable en el traste actual
        } else if (t != e.trastePend) {
          e.trastePend = t;                 // nuevo candidato: arranca reloj
          e.tTraste = ahora;
        } else if (ahora - e.tTraste >= T_TRASTE_MS) {
          e.traste = t;                     // candidato sostenido: comprometer
          Serial.print(F("Dedo -> traste "));
          Serial.println(t + 1);
          // Deslizar con cuerda activa: nueva nota on, anterior off
          if (e.activa) cambiaNota(e, notaActual(c), canalActual(c));
        }
      }
    }

    // ---------- 2. Botón arcade: disparar la cuerda ----------
    bool lecturaBoton = (digitalRead(PIN_BOTON[c]) == LOW);
    if (lecturaBoton != e.botonEstado) {
      if (e.tBoton == 0) e.tBoton = ahora;
      if (ahora - e.tBoton >= T_BOTON_MS) {
        e.botonEstado = lecturaBoton;
        e.tBoton = 0;
        if (e.botonEstado) {
          // Flanco de pisada: activa la cuerda y dispara la nota
          // (con dedo = nota del traste; sin dedo = cuerda al aire)
          e.activa = true;
          cambiaNota(e, notaActual(c), canalActual(c));
        }
        // Soltar el botón no hace nada: la nota sigue hasta que se
        // levante el dedo del sensor.
      }
    } else {
      e.tBoton = 0;
    }
  }

  // ---------- 3. Joystick: pitch bend + CC ----------
  procesaJoystick();

  // Sin delay: el bucle muestrea el mástil lo más rápido posible para
  // captar el deslizamiento. El antirrebote va por tiempo (millis) y
  // el joystick se autolimita a JOY_INTERVALO_MS.
}

#endif
