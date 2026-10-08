"""
Prepara desde la app el servidor de cámara (teleimager-server) del PC2 del G1.

Hace por SSH lo que antes se hacía a mano en la «Terminal A»:

  1. Busca qué /dev/videoN es la cámara externa (OBSBOT) — el número cambia entre reinicios.
  2. Escribe cam_config_server.yaml con la RealSense (cabeza) desactivada y la externa activa.
  3. (Re)inicia teleimager-server en segundo plano si hace falta.
  4. Espera a que el puerto ZMQ de la cámara responda.

Requiere acceso SSH sin contraseña al PC2 (una sola vez:  ssh-copy-id unitree@192.168.123.164)
o, si está instalado `sshpass`, indicar la clave con --g1-clave.
"""

import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("robot.servidor_camara_g1")

HOST_DEFECTO = "192.168.123.164"
USUARIO_DEFECTO = "unitree"
DIR_TELEIMAGER = "~/teleimager"
PUERTO_EXTERNA = 55556
NOMBRE_EXTERNA = "OBSBOT"
LOG_REMOTO = "/tmp/teleimager_lsc.log"
FORMA = (540, 960)               # alto, ancho de la imagen que publica la cámara externa


class ErrorCamaraG1(RuntimeError):
    """Fallo al preparar la cámara; el mensaje se muestra tal cual al usuario."""


def parsear_dispositivos(texto: str) -> List[Tuple[str, List[str]]]:
    """Salida de `v4l2-ctl --list-devices` → [(nombre, [/dev/videoN, ...]), ...]."""
    bloques: List[Tuple[str, List[str]]] = []
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        if not linea[0].isspace():
            bloques.append((linea.strip().rstrip(":"), []))
        elif bloques:
            m = re.search(r"/dev/video\d+", linea)
            if m:
                bloques[-1][1].append(m.group(0))
    return bloques


def elegir_video(texto: str, clave: str = NOMBRE_EXTERNA) -> str:
    """Devuelve el número (str) del primer /dev/video de la cámara externa."""
    bloques = parsear_dispositivos(texto)
    for nombre, videos in bloques:
        if clave.lower() in nombre.lower() and videos:
            return videos[0].replace("/dev/video", "")
    for nombre, videos in bloques:      # sin coincidencia: la primera que no sea RealSense ni Tegra
        if videos and not re.search(r"realsense|tegra", nombre, re.IGNORECASE):
            return videos[0].replace("/dev/video", "")
    vistos = "; ".join(f"{n}: {' '.join(v) or '-'}" for n, v in bloques) or "ninguno"
    raise ErrorCamaraG1(f"No encuentro la cámara externa «{clave}» en el robot. Dispositivos: {vistos}. "
                        "¿Está conectada por USB al PC2?")


def generar_yaml(video_id: str, puerto: int = PUERTO_EXTERNA, forma: Tuple[int, int] = FORMA) -> str:
    """cam_config_server.yaml con la RealSense apagada y la cámara externa publicando por ZMQ."""
    def apagada(vid: str) -> str:
        return (f"  enable_zmq: false\n  enable_webrtc: false\n  type: opencv\n  binocular: false\n"
                f"  fps: 30\n  image_shape: [480, 640]\n  video_id: \"{vid}\"\n"
                f"  serial_number: null\n  physical_path: null\n")
    return (
        "head_camera:\n" + apagada("2") + "\n"
        "left_wrist_camera:\n"
        f"  enable_zmq: true\n  zmq_port: {puerto}\n  enable_webrtc: false\n  type: opencv\n"
        f"  binocular: false\n  fps: 30\n  image_shape: [{forma[0]}, {forma[1]}]\n"
        f"  video_id: \"{video_id}\"\n  serial_number: null\n  physical_path: null\n\n"
        "right_wrist_camera:\n" + apagada("1")
    )


class ServidorCamaraG1:
    def __init__(self, host: str = HOST_DEFECTO, usuario: str = USUARIO_DEFECTO,
                 clave: Optional[str] = None, puerto: int = PUERTO_EXTERNA,
                 nombre_camara: str = NOMBRE_EXTERNA, directorio: str = DIR_TELEIMAGER):
        self.host, self.usuario, self.clave = host, usuario, clave
        self.puerto, self.nombre_camara, self.directorio = puerto, nombre_camara, directorio

    # ── SSH ──────────────────────────────────────────────────────────
    def _argv_ssh(self, remoto: str) -> Tuple[List[str], dict]:
        base = ["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ServerAliveInterval=5", f"{self.usuario}@{self.host}", remoto]
        env = dict(os.environ)
        if self.clave and shutil.which("sshpass"):
            env["SSHPASS"] = self.clave
            return ["sshpass", "-e"] + base, env
        return base[:1] + ["-o", "BatchMode=yes"] + base[1:], env

    def _ssh(self, remoto: str, entrada: Optional[str] = None, timeout: float = 20.0) -> str:
        if shutil.which("ssh") is None:
            raise ErrorCamaraG1("No está instalado el cliente SSH (sudo apt install openssh-client).")
        argv, env = self._argv_ssh(remoto)
        try:
            r = subprocess.run(argv, input=entrada, capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ErrorCamaraG1(f"El robot ({self.host}) no respondió por SSH a tiempo.")
        if r.returncode == 255:
            err = (r.stderr or "").strip()
            if "denied" in err.lower():
                raise ErrorCamaraG1(
                    f"El robot pide contraseña por SSH. Haz esto una sola vez en una terminal de la PC:\n"
                    f"   ssh-copy-id {self.usuario}@{self.host}\n"
                    f"(o instala sshpass y usa --g1-clave).")
            raise ErrorCamaraG1(f"No se pudo conectar por SSH con {self.host}: {err or 'sin respuesta'}. "
                                "¿El robot está encendido y en la misma red?")
        return r.stdout

    # ── Pasos ────────────────────────────────────────────────────────
    def puerto_abierto(self, espera: float = 1.0) -> bool:
        try:
            with socket.create_connection((self.host, self.puerto), timeout=espera):
                return True
        except OSError:
            return False

    def _yaml_actual(self) -> str:
        try:
            return self._ssh(f"cat {self.directorio}/cam_config_server.yaml 2>/dev/null")
        except ErrorCamaraG1:
            raise

    def preparar(self, avisar: Optional[Callable[[str], None]] = None, espera_s: float = 30.0) -> int:
        """Deja la cámara externa publicando. Devuelve el puerto ZMQ. Lanza ErrorCamaraG1."""
        def paso(msg: str):
            log.info(msg)
            if avisar:
                avisar(msg)

        paso("Conectando por SSH con el robot…")
        listado = self._ssh("v4l2-ctl --list-devices 2>&1")
        if not listado.strip():
            raise ErrorCamaraG1("El robot no devolvió la lista de cámaras (¿falta v4l2-ctl?).")
        video = elegir_video(listado, self.nombre_camara)
        paso(f"Cámara externa en /dev/video{video}.")

        nuevo = generar_yaml(video, self.puerto)
        igual = self._yaml_actual().strip() == nuevo.strip()
        if igual and self.puerto_abierto():
            paso("El servidor de cámara ya estaba listo.")
            return self.puerto

        if not igual:
            paso("Desactivando la RealSense y activando la cámara externa…")
            d = self.directorio
            self._ssh(f"cd {d} && {{ [ -f cam_config_server.yaml.lsc_bak ] || "
                      f"cp cam_config_server.yaml cam_config_server.yaml.lsc_bak 2>/dev/null; }}; "
                      f"cat > cam_config_server.yaml", entrada=nuevo)

        paso("Iniciando teleimager-server en el robot…")
        # [t] evita que pkill coincida con este mismo comando
        self._ssh("pkill -f '[t]eleimager-server'; sleep 1; "
                  f"cd {self.directorio} && PATH=$HOME/.local/bin:$PATH "
                  f"setsid nohup teleimager-server > {LOG_REMOTO} 2>&1 < /dev/null & sleep 0.5; echo ok",
                  timeout=25)
        fin = time.time() + espera_s
        while time.time() < fin:
            if self.puerto_abierto():
                paso("Cámara del robot lista.")
                return self.puerto
            time.sleep(1.0)
        cola = ""
        try:
            cola = self._ssh(f"tail -n 8 {LOG_REMOTO}")
        except ErrorCamaraG1:
            pass
        raise ErrorCamaraG1(f"El servidor de cámara no abrió el puerto {self.puerto} en {espera_s:.0f} s."
                            + (f"\nÚltimas líneas del robot ({LOG_REMOTO}):\n{cola.strip()}" if cola else ""))

    def detener(self):
        """Apaga teleimager-server en el robot (libera la cámara)."""
        try:
            self._ssh("pkill -f '[t]eleimager-server'; echo ok", timeout=10)
        except ErrorCamaraG1 as e:
            log.warning(f"No se pudo detener teleimager-server: {e}")
