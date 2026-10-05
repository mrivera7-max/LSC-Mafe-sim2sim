# Cámara del G1 físico como fuente del reconocimiento LSC

La pestaña de cámara puede usar la cámara de cabeza del Unitree G1 en lugar de la USB.
El robot la publica con `teleimager-server` (corre en su PC2); la app la lee por ZMQ con
`pyzmq` + OpenCV, sin instalar teleimager en el entorno de la app.

```
G1 (PC2, 192.168.123.164)                        PC 5062robotika
teleimager-server ── ZMQ PUB :55555 (JPEG) ─────▶ robot/camara_g1.py ─▶ reconocedor LSC
                  └─ ZMQ REP :60000 (GET_DATA)
```

Si la imagen es estéreo (480×1280 = izquierda | derecha) se usa la mitad izquierda; las cámaras
normales (RealSense 640×480, USB) se usan enteras. Se detecta solo por la proporción del frame. El reconocedor espeja la imagen como con la cámara USB.

## Paso 0 — Dejar el servidor de imagen corriendo en el PC2 del robot

Solo la primera vez. El servidor (`teleimager-server`) corre **en el robot**, porque la
cámara de cabeza está cableada al PC2 (Jetson, ARM).

1. Red: PC por cable al robot, IP `192.168.123.x`, robot encendido.
   ```bash
   ping -c 3 192.168.123.164
   ssh unitree@192.168.123.164        # usuario/clave de fábrica de Unitree: unitree / 123 (usa la tuya si la cambiaste)
   ```
2. ¿Ya está instalado?
   ```bash
   conda env list | grep -i teleimager ; which teleimager-server
   ```
   Si aparece, salta al punto 4.
3. Instalación (según el README de teleimager; el PC2 necesita salida a internet para `git`/`pip`):
   ```bash
   mkdir -p ~/miniconda3
   wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh -O ~/miniconda3/miniconda.sh
   bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3 && rm ~/miniconda3/miniconda.sh
   source ~/miniconda3/bin/activate && conda init --all
   conda create -n teleimager python=3.10 -y && conda activate teleimager
   sudo apt install -y libusb-1.0-0-dev libturbojpeg-dev
   git clone https://github.com/unitreerobotics/teleimager.git && cd teleimager
   pip install -e ".[server]"
   bash setup_uvc.sh                  # permisos de /dev/video*
   ```
   Si el PC2 no tiene internet, descarga el repositorio y los paquetes en la PC y cópialos con `scp`.
4. Descubrir la cámara y configurar:
   ```bash
   conda activate teleimager && cd ~/teleimager
   teleimager-server --cf --rs        # lista video_id / serial_number / resoluciones (RealSense incluida)
   nano cam_config_server.yaml
   ```
   En `head_camera` deja `enable_zmq: true`, `zmq_port: 55555`, `enable_webrtc: false` y rellena
   `type`, `image_shape`, `binocular`, `fps` y `video_id`/`serial_number` con lo descubierto.
   Si la cámara es **estéreo** (imagen `[480, 1280]`), `binocular: true`; si es una sola
   cámara, `binocular: false` **y** en la app `camara_g1_binocular: false`.
5. Arrancar (deja esa terminal abierta):
   ```bash
   teleimager-server --rs             # sin --rs si la cámara no es RealSense
   ```
   Debe decir `head_camera is ready` y `ZMQ: enabled, zmq port=55555`.
6. Opcional, arranque automático al encender el robot: `bash setup_autostart.sh`.

Solo un proceso puede usar la cámara a la vez: si `xr_teleoperate` o `teleop_hand_and_arm.py`
están corriendo, ciérralos.

## Requisitos

1. PC y robot en la misma red (`192.168.123.x`) y el G1 encendido.
2. `teleimager-server` corriendo en el PC2 (paso 0), con `head_camera.enable_zmq: true` y `zmq_port: 55555`.
3. Puertos 55555 y 60000 alcanzables desde la PC.

## Comprobación paso a paso

```bash
ping -c 3 192.168.123.164                    # 0% pérdida
cd ~/LSC-Mafe && source venv/bin/activate
python -m robot.probar_camara_g1             # o con otra IP: python -m robot.probar_camara_g1 192.168.123.164
```

Debe imprimir `OK: ~30 fps, frame 640x480 guardado en g1_frame.jpg`. Abre `g1_frame.jpg`
para ver lo que ve el reconocedor.

## Usar la cámara del G1 en la app

```bash
python app_unificada.py --sin-robot --v2 --camara-g1                    # IP por defecto
python app_unificada.py --sin-robot --v2 --camara-g1 192.168.123.164    # IP explícita
```

En `config.json` se puede dejar fijo: `"camara_fuente": "g1"`, `"camara_g1_host"`,
`"camara_g1_puerto"` y `"camara_g1_binocular"`. Con `"camara_fuente": "usb"` (por defecto)
se usa `camara_idx` como siempre.

## Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `No llegan imágenes de ...:55555` | El servidor de imagen no corre en el PC2, o no hay red | `ping`; por SSH al PC2 arrancar `teleimager-server` y ver `head_camera is ready` |
| `Sin respuesta de configuración ... :60000` (aviso) | Puerto de configuración cerrado | No impide el video; revisar firewall si se quiere ver la config |
| Imagen fija o retrasada | Otro cliente saturando el PC2 (Pico, otra app) | Cerrar otros clientes |
| Se ve la imagen pero no reconoce | Iluminación o distancia distinta a la del entrenamiento | Colocarse frente a la cámara a ~1 m, con luz frontal |
| Dos imágenes lado a lado | `camara_g1_binocular` en `false` | Ponerlo en `true` |

## Cámara adicional con mejor ángulo

La cámara de cabeza del G1 apunta hacia abajo y puede no servir para ver las señas. Dos opciones:

**A. USB directa a la PC (la más simple, no toca el robot).** Pon la cámara en un trípode a la
altura de la cabeza del robot, apuntando a quien hace la seña, y conéctala a la PC:
```bash
ls /dev/video*                     # cada cámara USB crea 2 nodos (video0/video1, video2/video3...)
v4l2-ctl --list-devices            # nombre de cada cámara (sudo apt install v4l-utils)
python app_unificada.py --sin-robot --v2 --camara 2     # el número es el índice del dispositivo
```
El índice para OpenCV suele ser el primero de cada pareja (0, 2, 4…). Si no abre, prueba con otro.

**B. USB al PC2 del robot como cámara adicional de teleimager.** Sirve si la cámara va montada
en el robot y el cable llega al PC2. En `cam_config_server.yaml` del PC2 activa otra entrada
(por ejemplo `left_wrist_camera`) con `enable_zmq: true`, `zmq_port: 55556`,
`binocular: false`, su `video_id` y `image_shape: [480, 640]`, y reinicia `teleimager-server`.
Desde la PC:
```bash
python -m robot.probar_camara_g1 192.168.123.164 55556
python app_unificada.py --sin-robot --v2 --camara-g1 --camara-g1-puerto 55556 --camara-g1-mono
```
Cada cámara del servidor usa su propio puerto ZMQ (55555 cabeza, 55556, 55557…).
