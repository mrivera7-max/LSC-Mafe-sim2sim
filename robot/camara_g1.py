"""
camara_g1.py — cámara de cabeza del Unitree G1 como fuente de video para LSC.

El G1 publica su cámara con `teleimager-server` (corre en el PC2 del robot,
normalmente 192.168.123.164). Protocolo (teleimager/client.py):

    ZMQ PUB/SUB   tcp://<host>:55555   mensajes = bytes JPEG crudos (sin cabecera)
    ZMQ REQ/REP   tcp://<host>:60000   b"GET_DATA" -> JSON con la configuración

Este módulo lo lee solo con pyzmq + OpenCV (ya instalados en el venv de la app),
sin instalar teleimager. Expone la misma interfaz mínima que cv2.VideoCapture
(isOpened / read / set / release) para poder sustituirla en el panel de cámara.

La cámara de cabeza del G1 es estéreo (imagen [480, 1280] = izq | der). El
reconocedor necesita una sola vista, así que por defecto se recorta la mitad
izquierda.
"""

import logging
import threading
import time
from typing import Optional, Tuple

import numpy as np

try:
    import zmq
except ImportError:
    zmq = None

try:
    import cv2
except ImportError:
    cv2 = None

log = logging.getLogger("lsc_bridge.camara_g1")

PUERTO_ZMQ = 55555
PUERTO_CONFIG = 60000


def pedir_configuracion(host: str, puerto: int = PUERTO_CONFIG, timeout_ms: int = 2000) -> Optional[dict]:
    """Pide al servidor de imagen su configuración (cámaras, puertos, tamaños)."""
    if zmq is None:
        return None
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.LINGER, 0)
    try:
        sock.connect(f"tcp://{host}:{puerto}")
        sock.send(b"GET_DATA")
        if sock.poll(timeout_ms):
            return sock.recv_json()
    except Exception as e:  # noqa: BLE001
        log.debug(f"GET_DATA falló: {e}")
    finally:
        sock.close(0)
    return None


class CamaraG1:
    """Fuente de video con interfaz de cv2.VideoCapture, alimentada por ZMQ."""

    def __init__(self, host: str = "192.168.123.164", puerto: int = PUERTO_ZMQ,
                 binocular: Optional[bool] = None, vista: str = "izq", espera_s: float = 5.0):
        """
        host       IP del PC2 del robot (o de la PC que corre teleimager-server).
        binocular  True: recortar la mitad (imagen estéreo izq | der). False: usar la imagen entera.
                   None (por defecto): automático, se recorta solo si el frame es muy ancho
                   (ancho/alto >= 2.4, p. ej. 1280x480); la RealSense y las USB normales no se recortan.
        vista      "izq" o "der": mitad que se entrega cuando binocular=True.
        espera_s   segundos que se espera el primer frame antes de dar la cámara por caída.
        """
        self.host, self.puerto = host, puerto
        self.binocular, self.vista = binocular, vista
        self.error: Optional[str] = None

        self._ctx = None
        self._sock = None
        self._hilo: Optional[threading.Thread] = None
        self._activo = threading.Event()
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._n = 0               # frames recibidos
        self._n_leidos = 0        # último frame entregado a read()
        self._t_ultimo = 0.0
        self._abrir(espera_s)

    # ── Interfaz tipo cv2.VideoCapture ────────────────────────────────
    def isOpened(self) -> bool:
        return self._activo.is_set()

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Bloquea hasta que haya un frame nuevo (máx. 1 s) y lo devuelve."""
        t0 = time.monotonic()
        while self._activo.is_set():
            with self._lock:
                if self._frame is not None and self._n != self._n_leidos:
                    self._n_leidos = self._n
                    return True, self._frame.copy()
            if time.monotonic() - t0 > 1.0:
                return False, None
            time.sleep(0.005)
        return False, None

    def set(self, *_args) -> bool:      # cv2.VideoCapture.set: la resolución la fija el servidor
        return False

    def release(self):
        self._activo.clear()
        if self._hilo is not None and self._hilo is not threading.current_thread():
            self._hilo.join(timeout=2.0)
        if self._sock is not None:
            self._sock.close(0)
            self._sock = None
        log.info("Cámara del G1 cerrada")

    # ── Interno ───────────────────────────────────────────────────────
    def _abrir(self, espera_s: float):
        if zmq is None or cv2 is None:
            self.error = "Faltan pyzmq u opencv (pip install pyzmq opencv-python)"
            log.error(self.error)
            return
        cfg = pedir_configuracion(self.host)
        if cfg:
            log.info(f"teleimager en {self.host}: {cfg}")
        else:
            log.warning(f"Sin respuesta de configuración en {self.host}:{PUERTO_CONFIG} "
                        "(se sigue con el puerto de video por defecto)")
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.setsockopt(zmq.CONFLATE, 1)        # solo el frame más reciente
        self._sock.setsockopt(zmq.RCVHWM, 1)
        self._sock.setsockopt(zmq.RCVTIMEO, 500)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt_string(zmq.SUBSCRIBE, "")
        self._sock.connect(f"tcp://{self.host}:{self.puerto}")
        self._activo.set()
        self._hilo = threading.Thread(target=self._bucle, name="camara-g1", daemon=True)
        self._hilo.start()

        t0 = time.monotonic()
        while time.monotonic() - t0 < espera_s:
            if self._n > 0:
                log.info(f"Cámara del G1 conectada ({self.host}:{self.puerto}), "
                         f"frame {self._frame.shape[1]}x{self._frame.shape[0]}")
                return
            time.sleep(0.05)
        self.error = (f"No llegan imágenes de {self.host}:{self.puerto}. Revisa que el robot esté "
                      "encendido, en la misma red y con teleimager-server corriendo en el PC2.")
        log.error(self.error)
        self._activo.clear()

    def _bucle(self):
        while self._activo.is_set():
            try:
                jpg = self._sock.recv()
            except zmq.Again:
                continue
            except zmq.ZMQError:
                break
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            estereo = self.binocular if self.binocular is not None else (img.shape[1] / img.shape[0] >= 2.4)
            if estereo:
                mitad = img.shape[1] // 2
                img = img[:, :mitad] if self.vista == "izq" else img[:, mitad:]
            with self._lock:
                self._frame = img
                self._n += 1
                self._t_ultimo = time.monotonic()
