"""
probar_sonic.py — prueba del G1 en MuJoCo/SONIC sin la GUI.

    python -m robot.probar_sonic articulaciones   # mueve cada joint del brazo derecho con amplitud grande
    python -m robot.probar_sonic señas            # ejecuta todas las señas una a una
    python -m robot.probar_sonic Hola Gracias     # señas concretas

Requiere MuJoCo (Terminal 1) y deploy (Terminal 2) ya arrancados.
Sirve para separar "problema de la política/simulación" de "problema de la app".
"""
import logging
import sys
import time

from robot.conector_sonic import ConectorSonic, POSTURAS_G1, SEÑAS_SONIC

PRUEBAS = [  # (nombre, índice MuJoCo, valor grande, qué debe verse)
    ("hombro pitch → adelante/arriba", 22, -1.3, "brazo derecho sube hacia adelante"),
    ("hombro roll → afuera", 23, -0.9, "brazo derecho se separa del cuerpo"),
    ("codo → flexionado", 25, 1.8, "antebrazo sube"),
    ("muñeca yaw", 28, 0.9, "mano gira de lado"),
    ("muñeca pitch", 27, 0.9, "mano cabecea"),
    ("hombro pitch izq → adelante", 15, -1.3, "brazo izquierdo sube hacia adelante"),
]


def main(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    c = ConectorSonic()
    if not c.conectar():
        print(c.error)
        return 1
    c.iniciar_control()
    time.sleep(1.0)
    try:
        if not args or args[0] == "articulaciones":
            for nombre, mj, valor, esperado in PRUEBAS:
                print(f"\n>> {nombre}: debe verse: {esperado}")
                c.ir_a({mj: valor}, 1.5)
                time.sleep(0.8)
                c.reposo(1.2)
                time.sleep(0.5)
        else:
            nombres = list(SEÑAS_SONIC) if args[0] == "señas" else args
            for n in nombres:
                print(f"\n>> seña {n}")
                c.enviar_seña(n, pausa=0.6)
                c.reposo(1.2)
                time.sleep(0.5)
    finally:
        c.reposo(1.0)
        c.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
