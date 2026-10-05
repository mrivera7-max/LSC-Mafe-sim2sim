"""
Abre desde la app la simulación MuJoCo y/o el deploy SONIC en terminales visibles.

Equivale a las Terminales 1 y 2 de docs/sonic_mujoco.md y docs/g1_real.md:

  simulación : run_sim_loop.py (MuJoCo)  +  ./deploy.sh --input-type zmq_manager sim
  robot real : ./deploy.sh --input-type zmq_manager <interfaz>      (sin MuJoCo)

Las terminales quedan abiertas para que se vean los avisos y se pueda pulsar O (paro).
La salida del deploy se registra con `script` para saber cuándo aparece «Init done».
"""

import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("robot.lanzador_sonic")

DIR_DEFECTO = "/home/udirobotika/DURVVIN/SIM2SIM/blea/GR00T-WholeBodyControl"
INTERFAZ_DEFECTO = "enp131s0"
ESPERA_DEPLOY_SIM_S = 8          # el deploy necesita que MuJoCo ya publique LowState
DIR_LOGS = Path.home() / ".cache" / "lsc-mafe"
_INIT_DONE = re.compile(r"init\s*done", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class LanzadorSonic:
    def __init__(self, directorio: Optional[str] = None, interfaz: Optional[str] = None):
        self.directorio = Path(directorio or os.environ.get("GR00T_DIR") or DIR_DEFECTO).expanduser()
        self.interfaz = interfaz or INTERFAZ_DEFECTO
        self.error: Optional[str] = None

    # ── Comprobaciones y comandos ────────────────────────────────────
    def validar(self) -> Optional[str]:
        """None si todo está; si no, el motivo (con la opción para corregirlo)."""
        d = self.directorio
        if not (d / "gear_sonic_deploy" / "deploy.sh").exists():
            return (f"No encuentro gear_sonic_deploy/deploy.sh en {d}. "
                    "Indica la carpeta con --gr00t-dir o la variable GR00T_DIR.")
        return None

    def _venv(self) -> Optional[Path]:
        pref = self.directorio / ".venv_teleop"
        if (pref / "bin" / "activate").exists():
            return pref
        for c in sorted(self.directorio.glob(".venv*")):
            if (c / "bin" / "activate").exists():
                return c
        return None

    def comando_sim(self) -> str:
        venv = self._venv()
        if venv is None:
            raise RuntimeError(f"No hay entorno .venv* en {self.directorio} (el de gear_sonic_teleop).")
        return (f"cd {shlex.quote(str(self.directorio))} && "
                f"source {shlex.quote(str(venv / 'bin' / 'activate'))} && "
                "python gear_sonic/scripts/run_sim_loop.py")

    def comando_deploy(self, real: bool) -> str:
        destino = self.interfaz if real else "sim"
        previo = "" if real else f"echo 'Esperando {ESPERA_DEPLOY_SIM_S} s a que MuJoCo abra...'; sleep {ESPERA_DEPLOY_SIM_S}; "
        return (previo + f"cd {shlex.quote(str(self.directorio / 'gear_sonic_deploy'))} && "
                "source scripts/setup_env.sh && "
                f"./deploy.sh --input-type zmq_manager {shlex.quote(destino)}")

    # ── Terminales ───────────────────────────────────────────────────
    @staticmethod
    def _argv_terminal(titulo: str, cmd: str) -> Optional[List[str]]:
        bash = ["bash", "-lc", cmd]
        if shutil.which("gnome-terminal"):
            return ["gnome-terminal", "--title", titulo, "--"] + bash
        if shutil.which("x-terminal-emulator"):
            return ["x-terminal-emulator", "-e"] + bash
        if shutil.which("konsole"):
            return ["konsole", "-p", f"tabtitle={titulo}", "-e"] + bash
        if shutil.which("xfce4-terminal"):
            return ["xfce4-terminal", "--title", titulo, "-x"] + bash
        if shutil.which("xterm"):
            return ["xterm", "-T", titulo, "-e"] + bash
        return None

    def ruta_log(self, nombre: str) -> Path:
        return DIR_LOGS / f"{nombre}.log"

    def abrir_terminal(self, titulo: str, comando: str, log_nombre: Optional[str] = None) -> bool:
        """Abre una terminal que ejecuta `comando` y se queda abierta al terminar."""
        interior = comando
        if log_nombre and shutil.which("script"):
            DIR_LOGS.mkdir(parents=True, exist_ok=True)
            ruta = self.ruta_log(log_nombre)
            ruta.write_text("")
            # `script` conserva el terminal (pty): los avisos y la tecla O siguen funcionando
            interior = f"script -qf -c {shlex.quote(comando)} {shlex.quote(str(ruta))}"
        completo = f"{interior}; echo; echo '[proceso terminado — cierra esta ventana]'; exec bash"
        argv = self._argv_terminal(titulo, completo)
        if argv is None:
            self.error = "No hay emulador de terminal (gnome-terminal, xterm, konsole...)."
            return False
        try:
            subprocess.Popen(argv, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError as e:
            self.error = f"No se pudo abrir la terminal: {e}"
            return False

    def lanzar(self, real: bool) -> bool:
        """Simulación: MuJoCo + deploy sim. Real: solo deploy real."""
        self.error = None
        msg = self.validar()
        if msg:
            self.error = msg
            return False
        try:
            if not real:
                if not self.abrir_terminal("LSC · MuJoCo (T1)", self.comando_sim()):
                    return False
            return self.abrir_terminal(
                "LSC · Deploy SONIC " + ("REAL" if real else "sim") + " (T2)",
                self.comando_deploy(real), log_nombre="deploy_real" if real else "deploy_sim")
        except RuntimeError as e:
            self.error = str(e)
            return False

    def comandos_manuales(self, real: bool) -> str:
        """Texto con los comandos, por si no se puede abrir terminal."""
        partes = []
        try:
            if not real:
                partes.append("Terminal 1:  " + self.comando_sim())
        except RuntimeError as e:
            partes.append(f"Terminal 1: {e}")
        partes.append("Terminal 2:  " + self.comando_deploy(real))
        return "\n".join(partes)

    def deploy_listo(self, real: bool) -> bool:
        """True si el log del deploy ya muestra «Init done»."""
        ruta = self.ruta_log("deploy_real" if real else "deploy_sim")
        try:
            texto = _ANSI.sub("", ruta.read_text(errors="ignore"))
        except OSError:
            return False
        return bool(_INIT_DONE.search(texto))

    def hay_log(self, real: bool) -> bool:
        return shutil.which("script") is not None
