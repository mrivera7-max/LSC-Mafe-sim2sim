"""
conector_sonic.py — LSC UDI -> política SONIC en MuJoCo (Unitree G1, 29 GDL).

Sustituye al visor 3D del navegador en la pestaña Voz/Texto. Las señas se
ejecutan en el mismo entorno del runbook sim2sim (GR00T-WholeBodyControl),
pero SIN Pico 4: este módulo hace de publicador ZMQ y el deploy ONNX lo lee
con `--input-type zmq_manager`.

    App LSC ──ZMQ :5556──▶ deploy.sh (política SONIC, ONNX) ──▶ run_sim_loop.py (MuJoCo)

Qué se envía
------------
Topic `command`  {start, stop, planner}: arranca el control y elige modo planner.
Topic `planner`  {mode=IDLE, movement, facing, speed, height,
                  upper_body_position[17], upper_body_velocity[17]}.
  El planner mantiene al G1 de pie (IDLE) y los 17 GDL de torso y brazos siguen
  las posiciones que enviamos. Las piernas las controla la política.

El deploy considera obsoleto un mensaje `planner` de más de ~100 ms, así que
un hilo lo reenvía continuamente (50 Hz) con la postura actual.

Formato de mensaje (idéntico a gear_sonic_deploy/.../tests/test_zmq_manager.py):
    topic + cabecera JSON rellena con NUL hasta 1280 bytes + datos little-endian.

Orden de los 17 valores (IsaacLab restringido al tren superior, según
policy_parameters.hpp: upper_body_joint_isaaclab_order_in_mujoco_index):
    waist yaw/roll/pitch, hombro pitch L/R, hombro roll L/R, hombro yaw L/R,
    codo L/R, muñeca roll L/R, muñeca pitch L/R, muñeca yaw L/R.
"""

import json
import logging
import math
import threading
import time
from typing import Dict, Optional

import numpy as np

try:
    import zmq
except ImportError:  # se avisa al llamar a conectar()
    zmq = None

from robot.conector_g1 import POSTURAS_G1, ComandoArticular

log = logging.getLogger("lsc_bridge.conector_sonic")

# ── Protocolo ZMQ del deploy ─────────────────────────────────────────────
HEADER_SIZE = 1280
TOPIC_COMMAND = b"command"
TOPIC_PLANNER = b"planner"
MODO_IDLE = 0                      # LocomotionMode::IDLE: el G1 se queda de pie

_DTYPES = {"u8": "u1", "i32": "<i4", "f32": "<f4"}

# ── Articulaciones ───────────────────────────────────────────────────────
# Índice MuJoCo de cada uno de los 17 valores de upper_body_position.
UB17_A_MJ = (12, 13, 14, 15, 22, 16, 23, 17, 24, 18, 25, 19, 26, 20, 27, 21, 28)
MJ_A_UB17 = {mj: i for i, mj in enumerate(UB17_A_MJ)}

# Postura de pie por defecto de la política (default_angles en policy_parameters.hpp).
# Es la postura de REPOSO: con ella no hay salto al empezar ni al terminar.
POSTURA_BASE_MJ = {
    12: 0.0, 13: 0.0, 14: 0.0,
    15: 0.2, 16: 0.2, 17: 0.0, 18: 0.6, 19: 0.0, 20: 0.0, 21: 0.0,
    22: 0.2, 23: -0.2, 24: 0.0, 25: 0.6, 26: 0.0, 27: 0.0, 28: 0.0,
}

# Límites articulares (g1_29dof_old_freebase.xml, el modelo de scene_29dof_freebase.xml).
LIMITES_MJ = {
    12: (-2.618, 2.618), 13: (-0.52, 0.52), 14: (-0.52, 0.52),
    15: (-3.0892, 2.6704), 16: (-1.5882, 2.2515), 17: (-2.618, 2.618),
    18: (-1.0472, 2.0944), 19: (-1.97222, 1.97222),
    20: (-1.61443, 1.61443), 21: (-1.61443, 1.61443),
    22: (-3.0892, 2.6704), 23: (-2.2515, 1.5882), 24: (-2.618, 2.618),
    25: (-1.0472, 2.0944), 26: (-1.97222, 1.97222),
    27: (-1.61443, 1.61443), 28: (-1.61443, 1.61443),
}

# Campo de ComandoArticular -> (índice MuJoCo, signo). Mismos signos verificados
# en robot_sim/reproducir_sena.py (el brazo derecho es espejo en roll y yaw).
CAMPOS_A_MJ = {
    "hombro_izq_pitch": (15, +1), "hombro_izq_roll": (16, +1), "codo_izq": (18, +1),
    "muñeca_izq_pitch": (20, +1), "muñeca_izq_yaw": (21, +1),
    "hombro_der_pitch": (22, +1), "hombro_der_roll": (23, -1), "codo_der": (25, +1),
    "muñeca_der_pitch": (27, +1), "muñeca_der_yaw": (28, -1),
}


# ── Señas expresivas para SONIC ──────────────────────────────────────────
# Las posturas de POSTURAS_G1 son estáticas y de poca amplitud (se ajustaron con
# el robot colgado en unitree_mujoco). La política SONIC atenúa los movimientos
# pequeños y rápidos, así que aquí cada seña es una secuencia de fotogramas clave
# con más amplitud, más tiempo y, cuando la seña lo pide, oscilación
# (saludo, sí, no). Valores absolutos por índice MuJoCo (brazo derecho 22..28):
#   22 hombro pitch (− = adelante/arriba)   23 hombro roll (− = separa del cuerpo)
#   24 hombro yaw   25 codo (+ = flexiona)  26 muñeca roll
#   27 muñeca pitch 28 muñeca yaw
# Cada fotograma: ({índice: rad}, duración_s). El resto de joints conserva su valor.

def _osc(mj: int, centro: float, amp: float, n: int, dur: float = 0.35, extra=None):
    """n oscilaciones (ida y vuelta) de un joint alrededor de `centro`."""
    extra = extra or {}
    kf = []
    for _ in range(n):
        kf.append(({mj: centro + amp, **extra}, dur))
        kf.append(({mj: centro - amp, **extra}, dur))
    return kf


_MANO_ARRIBA = {22: -1.35, 23: -0.45, 24: 0.0, 25: 1.45, 26: 0.0, 27: 0.0, 28: 0.0}

SEÑAS_SONIC = {
    "Hola": [(_MANO_ARRIBA, 1.0)] + _osc(28, 0.0, 0.7, 3) + [({28: 0.0}, 0.3)],
    "Gracias": [
        ({22: -1.25, 23: -0.15, 25: 1.75, 27: -0.4}, 1.0),   # mano en la boca
        ({22: -0.55, 23: -0.25, 25: 0.95, 27: -0.1}, 1.0),   # se aleja hacia adelante
    ],
    "Si": [({22: -0.7, 23: -0.2, 25: 1.2, 27: 0.0}, 0.9)]
          + _osc(27, 0.0, 0.6, 2, 0.3) + [({27: 0.0}, 0.3)],
    "No": [({22: -0.9, 23: -0.3, 25: 1.4, 27: 0.0}, 0.9)]
          + _osc(28, 0.0, 0.75, 2, 0.3) + [({28: 0.0}, 0.3)],
    "Bien": [
        ({22: -0.9, 23: -0.2, 25: 1.7, 26: 0.0, 27: 0.0}, 0.9),  # puño al pecho
        ({22: -1.2, 23: -0.3, 25: 1.0, 27: 0.3}, 0.9),           # pulgar arriba adelante
    ],
    "Mal": [
        ({22: -1.0, 23: -0.2, 25: 1.6, 27: 0.4}, 0.9),
        ({22: -0.1, 23: -0.5, 25: 0.7, 27: -0.9}, 0.9),          # mano gira hacia abajo
    ],
    "Silencio": [({22: -1.45, 23: -0.1, 25: 1.95, 27: 0.0}, 1.0), ({}, 1.0)],  # dedo a los labios
}


def comando_a_objetivos_mj(cmd: ComandoArticular) -> Dict[int, float]:
    """ComandoArticular -> {índice MuJoCo: ángulo en rad}.

    Un campo en 0.0 significa "la seña no lo define" y se deja en la postura
    de pie de la política (así, una seña de mano derecha no estira el brazo izquierdo).
    """
    objetivos = {}
    for campo, (mj, signo) in CAMPOS_A_MJ.items():
        valor = getattr(cmd, campo)
        objetivos[mj] = POSTURA_BASE_MJ[mj] if valor == 0.0 else signo * valor
    return objetivos


# ── Empaquetado de mensajes ──────────────────────────────────────────────

def _empaquetar(topic: bytes, campos) -> bytes:
    """campos: lista de (nombre, dtype 'u8'|'i32'|'f32', ndarray-like)."""
    arreglos = [(n, d, np.asarray(a, dtype=_DTYPES[d])) for n, d, a in campos]
    cabecera = {
        "v": 1,
        "endian": "le",
        "count": 1,
        "fields": [{"name": n, "dtype": d, "shape": list(a.shape)} for n, d, a in arreglos],
    }
    cabecera_json = json.dumps(cabecera).encode("utf-8")
    if len(cabecera_json) > HEADER_SIZE:
        raise ValueError(f"Cabecera ZMQ demasiado grande ({len(cabecera_json)} > {HEADER_SIZE})")
    cabecera_bytes = cabecera_json + b"\x00" * (HEADER_SIZE - len(cabecera_json))
    datos = b"".join(a.tobytes() for _, _, a in arreglos)
    return topic + cabecera_bytes + datos


def empaquetar_comando(start: bool, stop: bool, planner: bool) -> bytes:
    return _empaquetar(TOPIC_COMMAND, [
        ("start", "u8", [1 if start else 0]),
        ("stop", "u8", [1 if stop else 0]),
        ("planner", "u8", [1 if planner else 0]),
    ])


def empaquetar_planner(upper_body_position, upper_body_velocity=None,
                       mode: int = MODO_IDLE) -> bytes:
    campos = [
        ("mode", "i32", [mode]),
        ("movement", "f32", [0.0, 0.0, 0.0]),
        ("facing", "f32", [1.0, 0.0, 0.0]),
        ("speed", "f32", [-1.0]),
        ("height", "f32", [-1.0]),
        ("upper_body_position", "f32", np.asarray(upper_body_position).reshape(17)),
    ]
    if upper_body_velocity is not None:
        campos.append(("upper_body_velocity", "f32", np.asarray(upper_body_velocity).reshape(17)))
    return _empaquetar(TOPIC_PLANNER, campos)


# ── Conector ─────────────────────────────────────────────────────────────

class ConectorSonic:
    """Publica señas LSC hacia el deploy de SONIC (MuJoCo, 29 GDL).

    Misma idea que ConectorG1: `enviar_seña("Hola")` bloquea lo que dura el
    movimiento, así que se llama desde un hilo (la GUI ya lo hace).
    """

    def __init__(self, host: str = "*", puerto: int = 5556, hz: float = 50.0):
        self.host = host
        self.puerto = puerto
        self.hz = hz
        self.error: Optional[str] = None

        self._ctx = None
        self._pub = None
        self._lock_pub = threading.Lock()
        self._activo = threading.Event()
        self._hilo: Optional[threading.Thread] = None

        # Trayectoria del tren superior (17 GDL, orden UB17)
        self._lock_tray = threading.Lock()
        base = np.array([POSTURA_BASE_MJ[mj] for mj in UB17_A_MJ], dtype=np.float64)
        self._q_from = base.copy()
        self._q_to = base.copy()
        self._t0 = time.monotonic()
        self._dur = 1.0

    # ── Conexión ─────────────────────────────────────────────────────
    def conectar(self) -> bool:
        """Abre el publicador ZMQ y empieza a transmitir la postura de pie."""
        if zmq is None:
            self.error = "pyzmq no instalado (pip install pyzmq)"
            log.error(self.error)
            return False
        if self.conectado:
            return True
        try:
            self._ctx = zmq.Context()
            self._pub = self._ctx.socket(zmq.PUB)
            self._pub.setsockopt(zmq.LINGER, 0)
            self._pub.bind(f"tcp://{self.host}:{self.puerto}")
        except zmq.ZMQError as e:
            self.error = f"No se pudo abrir el puerto {self.puerto}: {e}"
            log.error(self.error)
            self._liberar()
            return False

        time.sleep(0.5)  # da tiempo a que el deploy (subscriptor) se enganche
        self.error = None
        self._activo.set()
        self._hilo = threading.Thread(target=self._bucle, name="sonic-pub", daemon=True)
        self._hilo.start()
        log.info(f"Publicador SONIC en tcp://{self.host}:{self.puerto} ({self.hz:.0f} Hz)")
        return True

    def iniciar_control(self, reintentos: int = 3):
        """Pide al deploy que arranque el control en modo planner.

        Es idempotente: si el control ya estaba arrancado no pasa nada. Se
        repite porque ZMQ PUB/SUB puede perder el primer mensaje.
        """
        for _ in range(reintentos):
            self._enviar(empaquetar_comando(start=True, stop=False, planner=True))
            time.sleep(0.3)
        log.info("Comando de inicio enviado al deploy SONIC (modo planner)")

    def parada_emergencia(self):
        """Envía `stop` al deploy (equivale a pulsar O en su terminal)."""
        self._enviar(empaquetar_comando(start=False, stop=True, planner=True))
        log.warning("Parada de emergencia enviada al deploy SONIC")

    def cerrar(self):
        """Vuelve a la postura de pie y cierra el publicador."""
        if self.conectado:
            try:
                self.reposo(1.0)
            except Exception:
                pass
        self._activo.clear()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
        self._liberar()
        log.info("Publicador SONIC cerrado")

    @property
    def conectado(self) -> bool:
        return self._pub is not None and self._activo.is_set()

    # ── Movimiento ───────────────────────────────────────────────────
    def enviar_seña(self, nombre_seña: str, pausa: float = 0.4) -> bool:
        """Ejecuta una seña LSC (POSTURAS_G1). Bloquea duración + pausa."""
        if not self.conectado:
            log.warning("SONIC no conectado — seña descartada")
            return False
        if nombre_seña == "REPOSO":
            return self.reposo(POSTURAS_G1["REPOSO"].duracion)
        cmd = POSTURAS_G1.get(nombre_seña)
        if cmd is None and nombre_seña not in SEÑAS_SONIC:
            log.warning(f"Seña '{nombre_seña}' sin postura definida — se omite")
            return False
        secuencia = SEÑAS_SONIC.get(nombre_seña)
        if secuencia is not None:
            dur_total = sum(d for _, d in secuencia)
            log.info(f"→ SONIC: {nombre_seña} ({len(secuencia)} fotogramas, {dur_total:.1f} s)")
            for objetivos, dur in secuencia:
                self.ir_a(objetivos, dur)
        else:
            log.info(f"→ SONIC: {nombre_seña} ({cmd.duracion:.1f} s)")
            self.ir_a(comando_a_objetivos_mj(cmd), cmd.duracion)
        time.sleep(pausa)
        return True

    def reposo(self, duracion: float = 1.5) -> bool:
        """Brazos y torso a la postura de pie de la política."""
        self.ir_a(dict(POSTURA_BASE_MJ), duracion)
        return True

    def ir_a(self, objetivos_mj: Dict[int, float], duracion: float, esperar: bool = True):
        """Lleva los joints indicados (índice MuJoCo) a su ángulo con perfil coseno.

        Los joints no indicados conservan su consigna. Los índices de piernas
        se ignoran: los controla la política.
        """
        with self._lock_tray:
            q_ini, _ = self._calcular(time.monotonic())
            q_fin = q_ini.copy()
            for mj, valor in objetivos_mj.items():
                i = MJ_A_UB17.get(mj)
                if i is None:
                    continue
                lo, hi = LIMITES_MJ[mj]
                if not lo <= valor <= hi:
                    log.warning(f"Joint {mj}: {valor:.2f} rad fuera de límites, se recorta")
                q_fin[i] = min(max(valor, lo), hi)
            self._q_from, self._q_to = q_ini, q_fin
            self._dur = max(float(duracion), 0.05)
            self._t0 = time.monotonic()
            dur = self._dur
        if esperar:
            time.sleep(dur)

    def postura_actual(self) -> Dict[int, float]:
        """Consigna actual {índice MuJoCo: rad} del tren superior."""
        with self._lock_tray:
            q, _ = self._calcular(time.monotonic())
        return {mj: float(q[i]) for i, mj in enumerate(UB17_A_MJ)}

    # ── Interno ──────────────────────────────────────────────────────
    def _calcular(self, t: float):
        """(posición, velocidad) de los 17 GDL en el instante t. Requiere _lock_tray."""
        s = min(max((t - self._t0) / self._dur, 0.0), 1.0)
        delta = self._q_to - self._q_from
        q = self._q_from + 0.5 * (1.0 - math.cos(math.pi * s)) * delta
        if s < 1.0:
            dq = (0.5 * math.pi * math.sin(math.pi * s) / self._dur) * delta
        else:
            dq = np.zeros(17)
        return q, dq

    def _enviar(self, mensaje: bytes):
        if self._pub is None:
            return
        with self._lock_pub:
            try:
                self._pub.send(mensaje, zmq.NOBLOCK)
            except zmq.ZMQError as e:
                log.debug(f"ZMQ send: {e}")

    def _bucle(self):
        dt = 1.0 / self.hz
        siguiente = time.perf_counter()
        while self._activo.is_set():
            with self._lock_tray:
                q, dq = self._calcular(time.monotonic())
            self._enviar(empaquetar_planner(q, dq))
            siguiente += dt
            time.sleep(max(0.0, siguiente - time.perf_counter()))

    def _liberar(self):
        with self._lock_pub:
            if self._pub is not None:
                self._pub.close(0)
                self._pub = None
            if self._ctx is not None:
                self._ctx.term()
                self._ctx = None
