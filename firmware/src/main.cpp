#include <Arduino.h>
#include <MIDIUSB.h>
#include <EEPROM.h>
#include <string.h>
#include <ctype.h>

// ============================================================
//  Roboguitarra - firmware (mástil MIDI USB, 17 trastes)
//
//  MODO_CALIBRACION 1 = al arrancar lanza la calibración guiada
//                       y la guarda en EEPROM; luego funciona normal.
//                   0 = modo normal (lee la calibración de EEPROM).
//
//  Se puede recalibrar en caliente sin reflashear con el comando
//  serie "CALIBRAR" (ver parser de comandos abajo).
//
//  Logs del mástil: "SUENA canal C traste T" al empezar una nota y
//  "CALLA canal C (dedo fuera)" al apagarla al levantar el dedo.
// ============================================================
#define MODO_CALIBRACION 0

// --- Hardware -----------------------------------------------
// Cuerda 1 = 1ª de guitarra (Mi agudo), 2 = 2ª (Si), 3 = 3ª (Sol).
const uint8_t NUM_CUERDAS = 3;
const uint8_t NUM_TRASTES = 17;
const uint8_t PIN_SENSOR[NUM_CUERDAS] = { A1, A2, A3 };
const uint8_t PIN_BOTON[NUM_CUERDAS]  = { 5, 4, 3 };  // arcade a GND, INPUT_PULLUP

// Joystick analógico (global, no por cuerda)
const uint8_t PIN_JOY_PITCH = A4;  // eje pitch bend
const uint8_t PIN_JOY_CC    = A5;  // eje CC (reverb / chorus)
const uint8_t PIN_JOY_BTN   = 6;   // pulsador del stick (sin función aún)

// Nota MIDI de cada cuerda al aire: 1ª = Mi4 (64), 2ª = Si3 (59), 3ª = Sol3 (55).
const uint8_t NOTA_AIRE[NUM_CUERDAS] = { 64, 59, 55 };

const uint8_t VELOCIDAD  = 100;

// Canales MIDI (0..15 en el byte de estado = canales 1..16).
// UN CANAL POR CUERDA: la cuerda c emite en CANALES[c]. Así cada
// cuerda es independiente (su propio pitch bend, sus notas) y dos
// cuerdas pueden tocar la misma nota sin que el Note Off de una apague
// la de la otra. El bend y los CC del joystick se reemiten en TODOS
// estos canales. Ninguno es el 9 (percusión en General MIDI).
// Debe haber al menos un canal por cuerda (NUM_CANALES >= NUM_CUERDAS).
const uint8_t CANALES[]    = { 0, 1, 2 };   // cuerda 1->canal 1, 2->2, 3->3
const uint8_t NUM_CANALES  = sizeof(CANALES) / sizeof(CANALES[0]);

// CC del eje A5: controla el efecto robot, bipolar con centro en 64 (limpio).
// Igual que el knob "robot" de la web: un lado = frequency shifter, el otro =
// bitcrusher. El servidor lo intercepta y lo mapea a set_robot(); FluidSynth no
// lo consume. Debe coincidir con ROBOT_CC en config.py. CC 20 no se usa en GM.
const uint8_t CC_ROBOT = 20;
const uint8_t CC_ROBOT_CENTRO = 64;  // valor "limpio" (centro del joystick)

// Joystick: zona muerta (cuentas ADC) y cadencia de envío.
const int          JOY_ZONA_MUERTA  = 40;
const unsigned long JOY_INTERVALO_MS = 5;

// Tabla de calibración POR DEFECTO (fallback si la EEPROM no es válida).
// Lectura ADC en el centro de cada traste. Común a todas las cuerdas.
const int CALIB_DEFECTO[NUM_TRASTES] = {
  998, 916, 828, 756, 681, 613, 551, 503, 438,
  391, 344, 291, 250, 208, 169, 133, 99
};

// --- Parámetros ajustables en caliente (comando serie) ------
// No son const: se pueden cambiar por el monitor con SHOW/UMBRAL/...
int           umbralPulsa     = 55;   // lectura para detectar dedo
int           umbralSuelta    = 35;   // lectura para detectar dedo fuera
int           margenHisteresis = 6;   // margen sobre la frontera de traste
unsigned long tPulsaMs        = 4;    // confirmar dedo presente
unsigned long tSueltaMs       = 120;  // confirmar dedo fuera (robusto a
                                      // microcortes del SoftPot al deslizar)
unsigned long tTrasteMs       = 8;    // confirmar SALTOS de >=2 trastes
                                      // (descarta el transitorio al levantar
                                      // el dedo; los cambios adyacentes de un
                                      // slide se comprometen al instante)
unsigned long tBotonMs        = 10;   // antirrebote del botón arcade

// Diagnóstico del mástil (comando DEBUG <cuerda>, 0 = apagado): loguea,
// SOLO para esa cuerda, cada zona nueva vista por el sensor, los
// microcortes de contacto con su duración y la frecuencia del bucle.
// Limitado a una cuerda para no saturar el serie (bloquearía el bucle).
int8_t debugCuerda = -1;   // índice de cuerda a diagnosticar, -1 = off

// ============================================================
//  Calibración persistida en EEPROM
// ============================================================
const uint16_t CALIB_MAGIC = 0x6C01;  // marca de "EEPROM calibrada"

int calibActual[NUM_TRASTES];         // tabla en uso (EEPROM o defecto)
int FRONTERA[NUM_TRASTES - 1];        // fronteras entre trastes

struct CalibGuardada {
  uint16_t magic;
  int      valores[NUM_TRASTES];
  uint8_t  checksum;
};

static uint8_t checksumCalib(uint16_t magic, const int *valores) {
  uint8_t s = 0;
  const uint8_t *pm = (const uint8_t *)&magic;
  s ^= pm[0]; s ^= pm[1];
  const uint8_t *pv = (const uint8_t *)valores;
  for (uint16_t i = 0; i < NUM_TRASTES * sizeof(int); i++) s ^= pv[i];
  return s;
}

void calcularFronteras() {
  for (uint8_t i = 0; i < NUM_TRASTES - 1; i++)
    FRONTERA[i] = (calibActual[i] + calibActual[i + 1]) / 2;
}

void cargarCalibracion() {
  CalibGuardada g;
  EEPROM.get(0, g);
  if (g.magic == CALIB_MAGIC &&
      g.checksum == checksumCalib(g.magic, g.valores)) {
    for (uint8_t i = 0; i < NUM_TRASTES; i++) calibActual[i] = g.valores[i];
    Serial.println(F("Calibracion cargada de EEPROM"));
  } else {
    for (uint8_t i = 0; i < NUM_TRASTES; i++) calibActual[i] = CALIB_DEFECTO[i];
    Serial.println(F("EEPROM sin calibrar: usando valores por defecto"));
  }
  calcularFronteras();
}

void guardarCalibracion(const int *tabla) {
  CalibGuardada g;
  g.magic = CALIB_MAGIC;
  for (uint8_t i = 0; i < NUM_TRASTES; i++) g.valores[i] = tabla[i];
  g.checksum = checksumCalib(g.magic, g.valores);
  EEPROM.put(0, g);
  for (uint8_t i = 0; i < NUM_TRASTES; i++) calibActual[i] = tabla[i];
  calcularFronteras();
  Serial.println(F("Calibracion guardada en EEPROM"));
}

// Lectura promediada estable (para calibrar).
int leerEstable(uint8_t pin) {
  long suma = 0;
  for (int i = 0; i < 30; i++) { suma += analogRead(pin); delay(8); }
  return (int)(suma / 30);
}

// Calibración guiada traste a traste (usa la cuerda 1). Al terminar
// imprime el CSV de confirmación y guarda en EEPROM. Bloquea mientras dura.
void calibrarGuiado() {
  int tabla[NUM_TRASTES];
  Serial.println(F("=== CALIBRACION DEL MASTIL ==="));
  Serial.println(F("Pon el dedo en el CENTRO de cada traste y mantenlo."));
  for (uint8_t t = 0; t < NUM_TRASTES; t++) {
    Serial.print(F("Traste "));
    Serial.print(t + 1);
    Serial.print(F(": pon el dedo..."));
    while (analogRead(PIN_SENSOR[0]) < umbralPulsa) delay(10);
    delay(80);
    tabla[t] = leerEstable(PIN_SENSOR[0]);
    Serial.print(F(" OK ("));
    Serial.print(tabla[t]);
    Serial.println(F(") - suelta"));
    while (analogRead(PIN_SENSOR[0]) > umbralSuelta) delay(10);
    delay(150);
  }
  Serial.println(F("traste,valor"));
  for (uint8_t t = 0; t < NUM_TRASTES; t++) {
    Serial.print(t + 1); Serial.print(','); Serial.println(tabla[t]);
  }
  guardarCalibracion(tabla);
}

// ============================================================
//  MIDI USB
// ============================================================
// --- Guardián anti-atasco ------------------------------------
// Si nadie lee el MIDI USB en el host (sinte sin abrir o caído), el
// buffer del endpoint se llena y cada sendMIDI BLOQUEA ~250 ms,
// arrastrando todo el bucle (y la detección de trastes) a ~2 Hz.
// Medimos cuánto tarda cada envío: si se bloquea, descartamos los
// siguientes y sondeamos cada MIDI_REINTENTO_MS con un solo paquete;
// cuando el consumidor vuelve, se reanuda solo.
const unsigned long MIDI_ATASCO_MS    = 50;    // envío más lento = atasco
const unsigned long MIDI_REINTENTO_MS = 2000;  // cadencia de reintento

bool          midiAtascado  = false;
unsigned long midiReintento = 0;

void midiEnvia(const midiEventPacket_t &ev) {
  if (midiAtascado) {
    if (millis() < midiReintento) return;          // descartar sin bloquear
    midiReintento = millis() + MIDI_REINTENTO_MS;  // toca paquete de sondeo
  }
  unsigned long t0 = millis();
  MidiUSB.sendMIDI(ev);
  if (millis() - t0 > MIDI_ATASCO_MS) {
    if (!midiAtascado)
      Serial.println(F("MIDI atascado: sin consumidor, descartando envios"));
    midiAtascado  = true;
    midiReintento = millis() + MIDI_REINTENTO_MS;
  } else if (midiAtascado) {
    midiAtascado = false;
    Serial.println(F("MIDI recuperado"));
    // Silencia notas que quedaran colgadas mientras descartábamos.
    for (uint8_t i = 0; i < NUM_CANALES; i++) {
      midiEventPacket_t off = { 0x0B, (uint8_t)(0xB0 | CANALES[i]), 123, 0 };
      MidiUSB.sendMIDI(off);
    }
    MidiUSB.flush();
  }
}

void midiFlush() {
  if (!midiAtascado) MidiUSB.flush();
}

void notaOn(uint8_t nota, uint8_t canal) {
  midiEventPacket_t ev = { 0x09, (uint8_t)(0x90 | canal), nota, VELOCIDAD };
  midiEnvia(ev);
}

void notaOff(uint8_t nota, uint8_t canal) {
  midiEventPacket_t ev = { 0x08, (uint8_t)(0x80 | canal), nota, 0 };
  midiEnvia(ev);
}

// Pitch bend en todos los canales de cuerda (14 bits: 0..16383, centro 8192).
void enviaBend(int v) {
  for (uint8_t i = 0; i < NUM_CANALES; i++) {
    midiEventPacket_t ev = { 0x0E, (uint8_t)(0xE0 | CANALES[i]),
                             (uint8_t)(v & 0x7F), (uint8_t)((v >> 7) & 0x7F) };
    midiEnvia(ev);
  }
}

// Control Change en todos los canales de cuerda.
void enviaCC(uint8_t cc, uint8_t valor) {
  for (uint8_t i = 0; i < NUM_CANALES; i++) {
    midiEventPacket_t ev = { 0x0B, (uint8_t)(0xB0 | CANALES[i]), cc, valor };
    midiEnvia(ev);
  }
}

// ============================================================
//  Mástil: máquina de estados del dedo + disparo de nota
// ============================================================
enum EstadoDedo { SIN_DEDO, CONFIRMANDO_PULSA, DEDO_PUESTO, CONFIRMANDO_SUELTA };

struct EstadoCuerda {
  // Sensor (máquina de estados)
  EstadoDedo    estado;
  unsigned long tEstado;       // ms al entrar en el estado actual
  int8_t        traste;        // 0..16 = trastes 1..17, -1 = sin dedo
  int8_t        trastePend;    // traste candidato en curso (antirrebote)
  unsigned long tTraste;       // ms desde que apareció el candidato
  // Botón arcade
  bool          botonEstado;   // estado confirmado (true = pisado)
  unsigned long tBoton;
  // MIDI
  bool          activa;        // cuerda sonando
  int8_t        notaSonando;   // nota MIDI activa, -1 = ninguna
  uint8_t       canalSonando;  // canal de la nota activa (para el Note Off)
};
EstadoCuerda cuerda[NUM_CUERDAS];

// Hay dedo apoyado (aunque esté confirmando la salida).
bool dedoPresente(const EstadoCuerda &e) {
  return e.estado == DEDO_PUESTO || e.estado == CONFIRMANDO_SUELTA;
}

// Nota que corresponde al estado actual del dedo en la cuerda c.
uint8_t notaActual(uint8_t c) {
  EstadoCuerda &e = cuerda[c];
  return dedoPresente(e) ? NOTA_AIRE[c] + e.traste + 1  // traste 1..17
                         : NOTA_AIRE[c];                // al aire
}

// Canal MIDI de la cuerda: fijo, uno por cuerda.
uint8_t canalActual(uint8_t c) {
  return CANALES[c];
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
  midiFlush();

  // Log: nota que empieza a sonar (canal MIDI 1-based + traste, o al aire).
  Serial.print(F("SUENA  canal "));
  Serial.print(canal + 1);
  if (dedoPresente(e)) { Serial.print(F("  traste ")); Serial.print(e.traste + 1); }
  else                 { Serial.print(F("  al aire")); }
  Serial.print(F("  (nota "));
  Serial.print(nueva);
  Serial.println(F(")"));
}

void apagaCuerda(EstadoCuerda &e) {
  if (e.notaSonando >= 0) {
    notaOff(e.notaSonando, e.canalSonando);
    midiFlush();
    // Log: la nota deja de sonar porque se ha levantado el dedo.
    Serial.print(F("CALLA  canal "));
    Serial.print(e.canalSonando + 1);
    Serial.println(F("  (dedo fuera)"));
  }
  e.notaSonando = -1;
  e.activa = false;
}

// Mediana de 3 lecturas: rechaza picos sueltos del SoftPot sin apenas
// latencia (~0,3 ms), para no bailar de traste por ruido.
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
               : FRONTERA[actual - 1] + margenHisteresis;
  int limInf = (actual == NUM_TRASTES - 1) ? 0
               : FRONTERA[actual] - margenHisteresis;
  if (valor <= limSup && valor >= limInf) return actual;
  return trasteCrudo(valor);
}

void procesaMastil(uint8_t c, unsigned long ahora) {
  EstadoCuerda &e = cuerda[c];
  int valor = leerSuavizado(PIN_SENSOR[c]);

  switch (e.estado) {
    case SIN_DEDO:
      if (valor > umbralPulsa) { e.estado = CONFIRMANDO_PULSA; e.tEstado = ahora; }
      break;

    case CONFIRMANDO_PULSA:
      if (valor <= umbralPulsa) {
        e.estado = SIN_DEDO;                 // rebote: no había dedo
      } else if (ahora - e.tEstado >= tPulsaMs) {
        e.estado = DEDO_PUESTO;
        e.traste = trasteCrudo(valor);
        e.trastePend = e.traste;
        if (e.activa) cambiaNota(e, notaActual(c), canalActual(c));
      }
      break;

    case DEDO_PUESTO:
      if (valor < umbralSuelta) {
        e.estado = CONFIRMANDO_SUELTA; e.tEstado = ahora;
        if (debugCuerda == (int8_t)c) {
          Serial.print(F("DBG c")); Serial.print(c + 1);
          Serial.print(F(" corte v=")); Serial.print(valor);
          Serial.print(F(" @")); Serial.println(ahora);
        }
      } else {
        // Cambio de traste:
        //  - Adyacente (±1): commit INMEDIATO (firma de un slide; la
        //    histéresis ya evita el baile en la frontera).
        //  - Salto >=2: ventana tTrasteMs SIN reinicio. Arranca al
        //    aparecer la desviación y al expirar compromete la zona MAS
        //    RECIENTE, aunque el dedo siga en movimiento (tras un
        //    microcorte del slide el dedo reaparece varios trastes más
        //    allá y no debe esperar a frenarse). Se cancela solo si la
        //    lectura vuelve al traste actual o se pierde el contacto
        //    (la FSM sale de DEDO_PUESTO, y el transitorio de levantar
        //    el dedo muere ahí: dura 1-2 ms, la ventana nunca expira).
        int8_t t = trasteConHisteresis(valor, e.traste);
        if (debugCuerda == (int8_t)c && t != e.trastePend) {
          // Primera muestra que cae en una zona nueva (se comprometa o no)
          Serial.print(F("DBG c")); Serial.print(c + 1);
          Serial.print(F(" zona ")); Serial.print(t + 1);
          Serial.print(F(" v=")); Serial.print(valor);
          Serial.print(F(" @")); Serial.println(ahora);
        }
        if (t == e.traste) {
          e.trastePend = t;                  // meneo: cancela el cambio pendiente
        } else {
          if (e.trastePend == e.traste)      // aparece la desviación:
            e.tTraste = ahora;               //   arranca la ventana (una vez)
          e.trastePend = t;
          int8_t salto = t - e.traste;
          if (salto < 0) salto = -salto;
          if (salto == 1 || ahora - e.tTraste >= tTrasteMs) {
            e.traste = t;                    // adyacente: ya; salto: al expirar
            e.trastePend = t;
            if (e.activa) cambiaNota(e, notaActual(c), canalActual(c));
          }
        }
      }
      break;

    case CONFIRMANDO_SUELTA:
      if (valor >= umbralSuelta) {
        e.estado = DEDO_PUESTO;              // volvió el contacto: no cortar
        if (debugCuerda == (int8_t)c) {
          Serial.print(F("DBG c")); Serial.print(c + 1);
          Serial.print(F(" vuelve v=")); Serial.print(valor);
          Serial.print(F(" corte_dur=")); Serial.print(ahora - e.tEstado);
          Serial.print(F("ms @")); Serial.println(ahora);
        }
      } else if (ahora - e.tEstado >= tSueltaMs) {
        e.estado = SIN_DEDO;
        e.traste = -1;
        e.trastePend = -1;
        if (e.activa) apagaCuerda(e);        // levantar el dedo apaga la nota
      }
      break;
  }

  // ---- Botón arcade: disparar la cuerda ----
  bool lecturaBoton = (digitalRead(PIN_BOTON[c]) == LOW);
  if (lecturaBoton != e.botonEstado) {
    if (e.tBoton == 0) e.tBoton = ahora;
    if (ahora - e.tBoton >= tBotonMs) {
      e.botonEstado = lecturaBoton;
      e.tBoton = 0;
      if (e.botonEstado) {
        // Flanco de pisada: arma la cuerda. Solo suena si hay dedo en
        // el mástil (nota del traste); sin dedo queda armada en
        // silencio y la nota saldrá cuando el dedo caiga en un traste
        // (la rama del sensor ya dispara si e.activa).
        e.activa = true;
        if (dedoPresente(e)) cambiaNota(e, notaActual(c), canalActual(c));
      }
      // Soltar el botón no hace nada: la nota sigue hasta que se
      // levante el dedo del sensor.
    }
  } else {
    e.tBoton = 0;
  }
}

// ============================================================
//  Joystick: pitch bend (A4) + CC (A5)
// ============================================================
int           joyCentroPitch = 512;
int           joyCentroCC    = 512;
int           joyUltimoBend  = 8192;
uint8_t       joyUltimoRobot = 255;   // 255 = fuerza el primer envío
unsigned long joyUltimoMs    = 0;

void procesaJoystick(unsigned long ahora) {
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

  // ---- Eje robot (A5) ----
  // Un único CC bipolar con centro en 64 (limpio). Hacia un lado sube hacia
  // 127 (frequency shifter), hacia el otro baja hacia 0 (bitcrusher),
  // replicando el knob "robot" de la web. La zona muerta central = limpio.
  desv = analogRead(PIN_JOY_CC) - joyCentroCC;
  int robot = CC_ROBOT_CENTRO;
  if (desv > JOY_ZONA_MUERTA) {
    robot = CC_ROBOT_CENTRO + (int)((long)(desv - JOY_ZONA_MUERTA) * 63
                / (1023 - joyCentroCC - JOY_ZONA_MUERTA));
  } else if (desv < -JOY_ZONA_MUERTA) {
    robot = CC_ROBOT_CENTRO - (int)((long)(-desv - JOY_ZONA_MUERTA) * 64
                / (joyCentroCC - JOY_ZONA_MUERTA));
  }
  robot = constrain(robot, 0, 127);
  if ((uint8_t)robot != joyUltimoRobot) {
    enviaCC(CC_ROBOT, (uint8_t)robot);
    joyUltimoRobot = (uint8_t)robot;
    huboEnvio = true;
  }

  if (huboEnvio) midiFlush();
}

// ============================================================
//  Parser de comandos serie (ajuste en caliente)
//  Ej.: SHOW | SUELTA 150 | TRASTE 8 | PULSA 4 | BOTON 10
//       UMBRAL 55 35 | HIST 6 | CALIBRAR
// ============================================================
char    cmdBuf[40];
uint8_t cmdLen = 0;

void mostrarParametros() {
  Serial.println(F("--- parametros ---"));
  Serial.print(F("PULSA(ms)="));   Serial.println(tPulsaMs);
  Serial.print(F("SUELTA(ms)="));  Serial.println(tSueltaMs);
  Serial.print(F("TRASTE(ms)="));  Serial.println(tTrasteMs);
  Serial.print(F("BOTON(ms)="));   Serial.println(tBotonMs);
  Serial.print(F("UMBRAL pulsa/suelta=")); Serial.print(umbralPulsa);
  Serial.print('/'); Serial.println(umbralSuelta);
  Serial.print(F("HIST="));        Serial.println(margenHisteresis);
}

void ejecutarComando(char *s) {
  char *cmd = strtok(s, " \t");
  if (!cmd) return;
  for (char *p = cmd; *p; p++) *p = toupper(*p);   // comandos sin distinguir may/min
  char *a1 = strtok(NULL, " \t");
  char *a2 = strtok(NULL, " \t");

  if      (!strcmp(cmd, "SHOW"))     { mostrarParametros(); return; }
  else if (!strcmp(cmd, "SUELTA") && a1) tSueltaMs = atol(a1);
  else if (!strcmp(cmd, "TRASTE") && a1) tTrasteMs = atol(a1);
  else if (!strcmp(cmd, "PULSA")  && a1) tPulsaMs  = atol(a1);
  else if (!strcmp(cmd, "BOTON")  && a1) tBotonMs  = atol(a1);
  else if (!strcmp(cmd, "HIST")   && a1) margenHisteresis = atoi(a1);
  else if (!strcmp(cmd, "UMBRAL") && a1 && a2) {
    umbralPulsa = atoi(a1); umbralSuelta = atoi(a2);
  }
  else if (!strcmp(cmd, "DEBUG")  && a1) {
    int n = atoi(a1);   // 1..NUM_CUERDAS = diagnosticar esa cuerda, 0 = off
    debugCuerda = (n >= 1 && n <= (int)NUM_CUERDAS) ? (int8_t)(n - 1) : -1;
  }
  else if (!strcmp(cmd, "CALIBRAR")) { calibrarGuiado(); return; }
  else { Serial.println(F("? comando desconocido")); return; }

  Serial.println(F("OK"));
}

void procesaComandos() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) { cmdBuf[cmdLen] = 0; ejecutarComando(cmdBuf); cmdLen = 0; }
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

// ============================================================
void setup() {
  Serial.begin(115200);

  for (uint8_t c = 0; c < NUM_CUERDAS; c++) {
    pinMode(PIN_BOTON[c], INPUT_PULLUP);
    //          estado   tEstado traste trastePend tTraste boton tBoton activa nota canal
    cuerda[c] = { SIN_DEDO, 0,    -1,    -1,        0,      false, 0,    false, -1,  0 };
  }
  pinMode(PIN_JOY_BTN, INPUT_PULLUP);

#if MODO_CALIBRACION
  while (!Serial);          // en calibración sí esperamos al monitor
  calibrarGuiado();
#else
  cargarCalibracion();
#endif

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
  Serial.println(F("Comandos: SHOW, SUELTA/TRASTE/PULSA/BOTON <ms>, UMBRAL <p> <s>, HIST <n>, DEBUG <cuerda|0>, CALIBRAR"));
}

void loop() {
  unsigned long ahora = millis();

  for (uint8_t c = 0; c < NUM_CUERDAS; c++) procesaMastil(c, ahora);

  procesaJoystick(ahora);
  procesaComandos();

  // Frecuencia del bucle (solo en modo diagnóstico), una línea por segundo.
  if (debugCuerda >= 0) {
    static unsigned long tHz = 0;
    static unsigned int  vueltas = 0;
    vueltas++;
    if (ahora - tHz >= 1000) {
      Serial.print(F("DBG hz=")); Serial.println(vueltas);
      vueltas = 0; tHz = ahora;
    }
  }
  // Sin delay: el mástil se muestrea lo más rápido posible para captar
  // el deslizamiento. Los antirrebotes van por tiempo (millis) y el
  // joystick se autolimita a JOY_INTERVALO_MS.
}
