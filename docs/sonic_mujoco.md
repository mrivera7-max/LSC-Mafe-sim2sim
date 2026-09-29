# Voz/Texto → Seña en MuJoCo (G1 de 29 GDL, política SONIC) — sin Pico 4

La pestaña **Voz / Texto → Seña** de `app_unificada.py` ya no abre el visor 3D del
navegador. Ahora ejecuta las señas en el mismo entorno del runbook *sim2sim*
(GR00T-WholeBodyControl): MuJoCo con el G1 de 29 GDL y la política SONIC en ONNX.

```
App LSC ──ZMQ :5556──▶ deploy.sh (política SONIC) ──▶ run_sim_loop.py (MuJoCo)
```

La app publica por ZMQ el mensaje `planner` con la posición de los 17 GDL de torso y
brazos (`upper_body_position`); la política mantiene al G1 de pie y sigue esas
posiciones. Las piernas las controla la política. Código: `robot/conector_sonic.py`.

## Qué NO se necesita (respecto al runbook sim2sim)

Hotspot `G1Capture`, Pico 4, trackers, XRoboToolkit (Terminal 0) y el Pico manager
(Terminal 3). El `command` de arranque que antes enviaba el Pico manager lo envía ahora
la app.

## Requisitos (una sola vez)

- El runbook sim2sim ya funciona en `5062robotika` (`~/blea/GR00T-WholeBodyControl`,
  `.venv_teleop`, deploy compilado, modelos ONNX descargados).
- Entorno de la app LSC con `pip install -r requirements.txt` (incluye `pyzmq`).
- Puerto **5556** libre. Si el Pico manager sigue corriendo, ciérralo: usa el mismo puerto.

## Arranque (cada sesión, en este orden)

**Terminal 1 — MuJoCo**

```bash
cd ~/blea/GR00T-WholeBodyControl
source .venv_teleop/bin/activate
python gear_sonic/scripts/run_sim_loop.py
```

Espera a que abra la ventana con el G1.

**Terminal 2 — deploy ONNX**

```bash
cd ~/blea/GR00T-WholeBodyControl/gear_sonic_deploy
source scripts/setup_env.sh
./deploy.sh --input-type zmq_manager sim
```

Confirma cuando pregunte y espera `Init done`.

**Terminal 3 — app LSC**

```bash
cd ~/LSC-Mafe
source venv/bin/activate
python app_unificada.py --sin-robot --v2
```

1. Pestaña **Voz / Texto → Seña** → **▶ Conectar MuJoCo G1**. Debe decir
   `● MuJoCo conectado (:5556)`. En la Terminal 2 debe aparecer `Planner enabled`.
2. En la ventana de MuJoCo pulsa **`9`** para soltar al G1 al suelo.
3. Escribe o di una frase (por ejemplo «hola gracias»). El G1 hace cada seña y vuelve
   a la postura de pie.

Parada de emergencia: tecla **`O`** en la Terminal 2 (igual que en el runbook).

## Diagnóstico

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El botón dice `No se pudo conectar: … Address already in use` | Otro publicador usa el puerto 5556 (Pico manager, otra instancia de la app) | Cerrarlo y reintentar |
| Conecta pero el G1 no se mueve | El comando `start` llegó antes de que el deploy estuviera listo | **Desconectar** y **Conectar** otra vez (reenvía `start`); el deploy debe estar en `Init done` |
| El G1 se mueve pero cuelga de la banda | Falta soltarlo | Tecla `9` en la ventana de MuJoCo |
| Tras una parada con `O` el G1 no reacciona | Control detenido | Reiniciar Terminal 2 y volver a pulsar Desconectar → Conectar |
| «Sin simulación conectada» en el registro | No se pulsó el botón | Pulsar **▶ Conectar MuJoCo G1** |

## Detalles técnicos

- Mensajes idénticos byte a byte al publicador de referencia
  (`gear_sonic_deploy/.../tests/test_zmq_manager.py`): tópico + cabecera JSON de 1280 B + datos.
- El deploy descarta mensajes `planner` de más de ~100 ms; la app los reenvía a 50 Hz
  con la postura actual (perfil coseno, velocidad analítica incluida).
- Orden de los 17 valores: torso (yaw, roll, pitch) y, por pares izquierda/derecha,
  hombro pitch, hombro roll, hombro yaw, codo, muñeca roll, muñeca pitch, muñeca yaw
  (`upper_body_joint_isaaclab_order_in_mujoco_index` en `policy_parameters.hpp`).
- Reposo = postura de pie por defecto de la política (`default_angles`), así no hay
  saltos al empezar ni al terminar.
- Los ángulos de `POSTURAS_G1` (`robot/conector_g1.py`) se reutilizan con los signos ya
  verificados en la Fase 4 (brazo derecho: roll y yaw espejados). Un campo en `0.0`
  significa «la seña no lo define» y se queda en la postura de pie.
- Cada ángulo se recorta a los límites del modelo (`g1_29dof_old_freebase.xml`).

## Limitaciones

- El modelo de 29 GDL no tiene dedos articulados: solo se ven posiciones de brazo y
  muñeca, no la forma de la mano.
- Las posturas de `POSTURAS_G1` se ajustaron con `unitree_mujoco` (robot colgado, kp=40).
  Con la política SONIC pueden necesitar retoques finos.
- El visor web (`voz_a_sena/servidor.py`, `static/index.html`) sigue en el repositorio
  y funciona por separado con `python voz_a_sena/servidor.py`, pero la app unificada
  ya no lo levanta.
