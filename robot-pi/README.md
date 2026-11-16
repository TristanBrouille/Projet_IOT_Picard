# Côté Raspberry Pi : `picar_mqtt.py`

Ce script tourne sur le Raspberry Pi du robot. Il reçoit les commandes de la page web par MQTT, fait bouger le robot, et renvoie la distance et la position.

```
Page Angular  →  Back Spring (PC)  →  broker MQTT  →  picar_mqtt.py (Pi)  →  robot
              ←                    ←  test.mosquitto.org  ←  distance, position, état
```

## 1. Installation (une seule fois)

Le script utilise les fichiers du dossier `Server` du dépôt Adeept. Il faut donc d'abord avoir lancé l'installation Adeept, qui installe les bibliothèques des moteurs, des servos et de l'accéléromètre :

```bash
cd ~/Adeept_PiCar-Pro
sudo python3 setup.py
```

Puis la bibliothèque MQTT :

```bash
sudo pip3 install paho-mqtt --break-system-packages
```

`servo.py` utilise l'ancienne bibliothèque `Adafruit_PCA9685`. Si elle manque : `sudo pip3 install adafruit-pca9685 --break-system-packages`.

L'I2C doit être activé (`sudo raspi-config` > Interface Options > I2C). La commande `i2cdetect -y 1` doit afficher `5f` (carte moteurs et servos) et `68` (accéléromètre).

Le broker utilisé est public (`test.mosquitto.org`) : il n'y a rien à installer sur la Pi pour lui, mais la Pi doit avoir accès à internet.

## 2. Lancement

Le script importe `Move.py`, `servo.py`, `Switch.py` et `Ultra.py` : il doit être placé et lancé dans le dossier `Server`, avec `servo.py` à côté.

```bash
cp picar_mqtt.py servo.py ~/Adeept_PiCar-Pro/Server/
cd ~/Adeept_PiCar-Pro/Server
sudo python3 picar_mqtt.py
```

Pour l'arrêter : `Ctrl+C` (les moteurs s'arrêtent et les roues se remettent droites).

Avec Thonny : vérifier que l'interpréteur est `/usr/bin/python3` (Outils > Options > Interpréteur), sinon les bibliothèques installées par `setup.py` ne sont pas trouvées.

## 3. Organisation du script

Le fichier se lit de haut en bas :

| Partie du fichier | Rôle | Partie du TP |
|---|---|---|
| 1. Réglages | broker, préfixe des topics, vitesse max, distance de sécurité, ports des servos | |
| 2. Matériel | servos avec `servo.py`, accéléromètre MPU6050 | A |
| 3. État du robot | dictionnaire `etat` envoyé à la page web | |
| 4. Fonctions de pilotage | `avancer`, `reculer`, `stop`, `gauche`, `droite`, `tout_droit`, `changer_vitesse`, `camera_gauche`… `camera_centre`, `lumiere` | B |
| 5. Capteurs | `lire_distance` (avec statut), `lire_position` | D |
| 6. MQTT | réception des commandes JSON, accusés de réception | C |
| 7. Programme principal | lance les threads, arrêt à la fermeture | D |

Le programme fait trois choses en même temps, chacune dans son thread :

| Thread | Fonction | Rôle |
|---|---|---|
| MQTT | lancé par `client.loop_start()` | reçoit les commandes et appelle `on_message()` |
| Distance | `thread_distance()` | mesure la distance à 2 Hz, publie, fait l'arrêt de sécurité |
| Position | `thread_position()` | lit l'accéléromètre à 2 Hz et publie X, Y, Z |

Le programme principal se contente d'attendre. Au `Ctrl+C`, `en_marche` passe à `False` pour arrêter les threads, puis les moteurs sont coupés.

Fichiers Adeept utilisés :

| Fichier | Utilisé pour |
|---|---|
| `Move.py` | moteurs des roues arrière : `Move.move(vitesse, sens, "mid")`, `Move.motorStop()` |
| `servo.py` | servos : `lookleft()`, `lookright()`, `up()`, `down()`, `lookup()`, `lookdown()` |
| `Switch.py` | lumières : `Switch.switch(port, 0 ou 1)` |
| `Ultra.py` | distance : `Ultra.checkdist()` en cm |

Ports utilisés :

| Élément | Port |
|---|---|
| Direction des roues avant | servo 0 |
| Caméra horizontal / vertical | servos 1 / 2 |
| Roue arrière gauche / droite | MotorA / MotorB |
| Lumière caméra / droite / gauche | Switch 1 / 2 / 3 |
| Ultrason | GPIO 23 (Trig) / 24 (Echo) |
| Accéléromètre | I2C `0x68` |

### Servos : fonctions de `servo.py`

Le script appelle directement les fonctions de `servo.py`. Elles déplacent le servo d'un certain nombre de pas à partir de sa position actuelle (et pas vers un angle précis), et le bloquent entre `pwmX_min` et `pwmX_max`.

| Action | Fonction de `servo.py` | Canal |
|---|---|---|
| Roues à gauche / à droite | `lookleft(VIRAGE)` / `lookright(VIRAGE)` | 0 |
| Caméra à gauche / à droite | `down(PAS_CAMERA)` / `up(PAS_CAMERA)` | 1 |
| Caméra en haut / en bas | `lookup(PAS_CAMERA)` / `lookdown(PAS_CAMERA)` | 2 |

Les valeurs sont en « valeur PWM » : 300 = milieu, environ 2,2 valeurs par degré. Par défaut `VIRAGE = 65` (environ 30°) et `PAS_CAMERA = 20` (environ 9° par clic), réglables en haut du script.

Avant de tourner, les roues repartent du centre : cliquer deux fois sur « Gauche » ne tourne pas plus. `servo.py` n'a pas de fonction pour revenir au centre, donc `tout_droit()` et `camera_centre()` remettent la position à `pwmX_init` (300).

Si la caméra part du mauvais côté, échanger `servo.down` et `servo.up` (ou `lookup` et `lookdown`) dans les fonctions `camera_...()`.

## 4. Topics MQTT

Tous les topics commencent par le préfixe `epf-picar-tristan` (réglage `PREFIXE`). Il doit être identique à `topic-prefix` dans `application.yaml` du back. Comme le broker est public, ce préfixe doit rester unique pour que personne d'autre ne pilote le robot.

| Topic | Sens | Contenu |
|---|---|---|
| `epf-picar-tristan/cmd` | vers le robot | `{"id":"…","action":"forward","speed":30}` |
| `epf-picar-tristan/ack` | depuis le robot | `{"id":"…","action":"forward","ok":true,"error":null}` |
| `epf-picar-tristan/state` | depuis le robot (retenu) | vitesse, direction, servos, lumières, mode, arrêt de sécurité |
| `epf-picar-tristan/status` | depuis le robot (retenu) | `{"online":true}` ou `{"online":false}` |
| `epf-picar-tristan/telemetry/distance` | depuis le robot, 2 Hz | `{"distanceCm":42.1,"valid":true,"status":"ok"}` |
| `epf-picar-tristan/telemetry/imu` | depuis le robot, 2 Hz | `{"x":…,"y":…,"z":…}` en m/s² |

Statuts de la distance : `ok`, `absent` (le capteur ne répond pas), `incoherent` (moins de 2 cm) ou `out_of_range` (rien devant, plus de 2 m).

Actions possibles dans `cmd` :

| Action | Champs en plus | Effet |
|---|---|---|
| `forward` / `backward` | `speed` (optionnel) | avancer / reculer |
| `stop` | | arrêter les moteurs |
| `left` / `right` / `center` | | tourner les roues / les remettre droites |
| `speed` | `speed` | changer la vitesse (0 à `VITESSE_MAX`) |
| `look_left` / `look_right` | | tourner la caméra d'un pas à gauche / à droite |
| `look_up` / `look_down` | | lever / baisser la caméra d'un pas |
| `camera_center` | | caméra au centre |
| `light` | `port` (1 à 3), `on` | allumer / éteindre une lumière |
| `mode` | `mode` : `safe` ou `manual` | activer / couper l'arrêt automatique |

Pour tester sans la page web, depuis n'importe quel PC :

```bash
mosquitto_sub -h test.mosquitto.org -t "epf-picar-tristan/#" -v
mosquitto_pub -h test.mosquitto.org -t "epf-picar-tristan/cmd" -m '{"action":"forward","speed":20}'
mosquitto_pub -h test.mosquitto.org -t "epf-picar-tristan/cmd" -m '{"action":"stop"}'
```

Ces topics se réutilisent tels quels dans le tableau de bord Node-RED (partie D).

## 5. Sécurité

- Vitesse plafonnée à 40/100 (`VITESSE_MAX`).
- Arrêt des moteurs au démarrage, à la fermeture (`Ctrl+C`) et si la connexion au broker est perdue.
- Mode `safe` (par défaut) : le robot refuse d'avancer et s'arrête tout seul si un obstacle est à moins de 20 cm (`DISTANCE_SECURITE`). Reculer reste possible.
- Si le robot disparaît sans prévenir, le broker publie `{"online":false}` à sa place (message de « dernière volonté »).
- Si gauche et droite sont inversées, échanger `servo.lookleft` et `servo.lookright` dans les fonctions `gauche()` et `droite()`.

## 6. Latence

Chaque commande porte un `id`. Le back Spring note l'heure d'envoi, et calcule `latencyMs` quand l'`ack` avec le même `id` revient du robot. La page affiche la dernière latence et la moyenne. Avec un broker public sur internet, elle est plus élevée qu'avec un broker local sur la Pi.
