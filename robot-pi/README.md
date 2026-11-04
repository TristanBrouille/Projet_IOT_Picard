# Côté Raspberry Pi

## 1. Installation (une seule fois)

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo pip3 install paho-mqtt mpu6050-raspberrypi --break-system-packages
```

Mosquitto 2 n'accepte par défaut que les connexions locales. Pour que le PC (back Spring) puisse se connecter :

```bash
sudo tee /etc/mosquitto/conf.d/picar.conf <<'CONF'
listener 1883
allow_anonymous true
CONF
sudo systemctl restart mosquitto
```

## 2. Lancement

Le script importe `Move.py`, `Switch.py` et `Ultra.py` du dépôt Adeept : il doit être lancé depuis le dossier `Server`.

```bash
cp picar_mqtt.py ~/Adeept_PiCar-Pro/Server/
cd ~/Adeept_PiCar-Pro/Server
sudo python3 picar_mqtt.py --broker localhost
```

Pour tester sans le robot (sur le PC) : `python3 picar_mqtt.py --sim --broker <ip_du_broker>`.

## 3. Topics MQTT

| Topic | Sens | Contenu |
|---|---|---|
| `picar/cmd` | vers le robot | `{"id":"…","action":"forward","speed":30}` |
| `picar/ack` | depuis le robot | `{"id":"…","action":"forward","ok":true,"error":null,"ts":…}` |
| `picar/state` | depuis le robot (retenu) | vitesse, direction, servos, lumières, mode, arrêt de sécurité |
| `picar/status` | depuis le robot (retenu + LWT) | `{"online":true}` / `{"online":false}` |
| `picar/telemetry/distance` | depuis le robot, 2 Hz | `{"distanceCm":42.1,"valid":true,"status":"ok"}` ; `status` = `ok`, `absent`, `incoherent` ou `out_of_range` |
| `picar/telemetry/imu` | depuis le robot, 2 Hz | `{"x":…,"y":…,"z":…}` en m/s² |

Actions : `forward`, `backward`, `stop`, `left`, `right`, `center`, `speed`, `camera` (`pan`, `tilt`), `camera_center`, `light` (`port` 1-3, `on`), `mode` (`safe` / `manual`).

Ces topics se réutilisent tels quels dans le tableau de bord Node-RED (partie D).

## 4. Sécurité

- Vitesse plafonnée à 40/100 (`MAX_SPEED`).
- Arrêt des moteurs au démarrage, à la fermeture (Ctrl+C, SIGTERM) et en cas de perte du broker.
- Mode `safe` : refus d'avancer et arrêt automatique si un obstacle est à moins de 20 cm.
- Si gauche et droite sont inversées sur votre kit, passer `STEER_SIGN` à `-1`.

## 5. Latence

Chaque commande porte un `id`. Le back Spring note l'heure d'envoi et calcule `latencyMs` à la réception de l'`ack` du robot. La page affiche la dernière valeur et la moyenne.
