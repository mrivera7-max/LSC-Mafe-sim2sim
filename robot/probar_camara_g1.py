"""
probar_camara_g1.py — comprueba la cámara de cabeza del G1 sin abrir la GUI.

    python -m robot.probar_camara_g1 [IP] [PUERTO]   # IP del PC2 (def. 192.168.123.164) y puerto ZMQ (def. 55555)
    # cámara externa publicada como left_wrist_camera: python -m robot.probar_camara_g1 192.168.123.164 55556

Muestra la configuración que publica teleimager, mide los fps y guarda un frame en
`g1_frame.jpg` (vista izquierda, la misma que ve el reconocedor).
"""
import logging
import sys
import time

import cv2

from robot.camara_g1 import CamaraG1


def main(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    host = args[0] if args else "192.168.123.164"
    puerto = int(args[1]) if len(args) > 1 else 55555
    cam = CamaraG1(host, puerto)
    if not cam.isOpened():
        print("ERROR:", cam.error)
        return 1
    n, t0, ultimo = 0, time.monotonic(), None
    while time.monotonic() - t0 < 3.0:
        ok, f = cam.read()
        if ok:
            n, ultimo = n + 1, f
    cam.release()
    if ultimo is None:
        print("ERROR: no llegaron frames")
        return 1
    cv2.imwrite("g1_frame.jpg", ultimo)
    print(f"OK: {n / 3.0:.1f} fps, frame {ultimo.shape[1]}x{ultimo.shape[0]} guardado en g1_frame.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
