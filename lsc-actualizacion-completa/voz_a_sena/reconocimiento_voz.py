"""
Reconocimiento de voz para el sistema Voz/Texto -> Sena LSC.

Usa la biblioteca SpeechRecognition con el motor de Google (gratuito, requiere
internet) para convertir audio del microfono a texto en espanol.
"""

import logging
from dataclasses import dataclass

log = logging.getLogger("voz_a_sena.reconocimiento_voz")

SR_OK = False
try:
    import speech_recognition as sr
    SR_OK = True
except ImportError:
    log.warning("SpeechRecognition no instalado. Instala con: pip install SpeechRecognition pyaudio")


@dataclass
class ResultadoVoz:
    texto: str
    exito: bool
    error: str = ""
    fuente: str = "pc"


class ReconocedorVoz:
    """
    Captura audio del microfono y lo convierte a texto en espanol.

    Uso:
        rec = ReconocedorVoz()
        resultado = rec.escuchar()
        if resultado.exito:
            print(resultado.texto)
    """

    def __init__(self, idioma: str = "es-CO", timeout_segundos: float = 5.0,
                 limite_frase_segundos: float = 8.0, fuente: str = "pc",
                 fallback_pc: bool = False, iface_ip: str = None):
        """
        fuente       "pc" (micrófono de esta PC) o "g1" (micrófono del robot por multicast).
        fallback_pc  con fuente "g1": si el robot no entrega audio, usa el micrófono de la PC.
        """
        self.fuente = fuente
        self.fallback_pc = fallback_pc
        self._mic_g1 = None
        if fuente == "g1":
            from voz_a_sena.microfono_g1 import MicrofonoG1
            self._mic_g1 = MicrofonoG1(iface_ip=iface_ip)
        self.idioma = idioma
        self.timeout_segundos = timeout_segundos
        self.limite_frase_segundos = limite_frase_segundos
        self._recognizer = None
        self._microfono = None

        if SR_OK:
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = 300
            self._recognizer.dynamic_energy_threshold = True

    def disponible(self) -> bool:
        return SR_OK

    def listar_microfonos(self) -> list:
        """Lista los microfonos disponibles en el sistema."""
        if not SR_OK:
            return []
        try:
            return sr.Microphone.list_microphone_names()
        except Exception as e:
            log.error(f"Error listando microfonos: {e}")
            return []

    def calibrar_ruido_ambiente(self, microfono_idx: int = None, duracion: float = 1.0):
        """Calibra el nivel de ruido ambiente antes de escuchar (mejora precision)."""
        if not SR_OK:
            return
        try:
            with sr.Microphone(device_index=microfono_idx) as fuente:
                log.info("Calibrando ruido ambiente...")
                self._recognizer.adjust_for_ambient_noise(fuente, duration=duracion)
                log.info(f"Calibracion completa. Umbral: {self._recognizer.energy_threshold:.0f}")
        except Exception as e:
            log.error(f"Error calibrando microfono: {e}")

    def escuchar(self, microfono_idx: int = None) -> ResultadoVoz:
        """
        Escucha del microfono y convierte a texto.

        Args:
            microfono_idx: Indice del microfono a usar (None = predeterminado).

        Returns:
            ResultadoVoz con el texto reconocido o el error ocurrido.
        """
        if not SR_OK:
            return ResultadoVoz(
                texto="", exito=False,
                error="SpeechRecognition no esta instalado. "
                      "Instala con: pip install SpeechRecognition pyaudio"
            )

        fuente = self.fuente
        audio = None
        if fuente == "g1":
            pcm = self._mic_g1.capturar_frase(self.timeout_segundos, self.limite_frase_segundos)
            if pcm is not None:
                audio = sr.AudioData(pcm, 16000, 2)
            elif self.fallback_pc and "No llegan paquetes" in (self._mic_g1.error or ""):
                log.warning(f"{self._mic_g1.error}. Se usa el micrófono de la PC.")
                fuente = "pc"
            else:
                return ResultadoVoz(texto="", exito=False, fuente="g1",
                                    error=self._mic_g1.error or "Sin audio del G1")
        if fuente == "pc":
            try:
                with sr.Microphone(device_index=microfono_idx) as fuente_audio:
                    log.info("Escuchando... (habla ahora)")
                    audio = self._recognizer.listen(
                        fuente_audio,
                        timeout=self.timeout_segundos,
                        phrase_time_limit=self.limite_frase_segundos,
                    )
            except sr.WaitTimeoutError:
                return ResultadoVoz(texto="", exito=False, error="Tiempo de espera agotado, no se detecto voz")
            except OSError as e:
                return ResultadoVoz(texto="", exito=False, error=f"Error de microfono: {e}")
            except Exception as e:
                return ResultadoVoz(texto="", exito=False, error=f"Error capturando audio: {e}")

        log.info("Procesando audio...")
        try:
            texto = self._recognizer.recognize_google(audio, language=self.idioma)
            log.info(f"Texto reconocido: '{texto}' (micrófono {fuente})")
            return ResultadoVoz(texto=texto, exito=True, fuente=fuente)
        except sr.UnknownValueError:
            return ResultadoVoz(texto="", exito=False, fuente=fuente, error="No se pudo entender el audio")
        except sr.RequestError as e:
            return ResultadoVoz(
                texto="", exito=False, fuente=fuente,
                error=f"Error de conexion con el servicio de reconocimiento: {e}. "
                      "Verifica tu conexion a internet."
            )
