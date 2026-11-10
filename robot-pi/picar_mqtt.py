#!/usr/bin/env python3
# File name   : picar_mqtt.py
# Description : Pilotage du PiCar-Pro par MQTT (TP4 IoT)
#               Inspire des fichiers Move.py, Switch.py, Ultra.py
#               et des exemples Servo / MPU6050 du depot Adeept.
#
# A copier dans le dossier Server du depot Adeept, puis :
#     cd ~/Adeept_PiCar-Pro/Server
#     sudo python3 picar_mqtt.py

import json
import time

import paho.mqtt.client as mqtt
from board import SCL, SDA
import busio
from adafruit_pca9685 import PCA9685
from adafruit_motor import servo
from mpu6050 import mpu6050

import Move      # moteurs des roues arriere (fichier Adeept)
import Switch    # lumieres (fichier Adeept)
import Ultra     # capteur ultrason (fichier Adeept)


# ======================================================================
# 1. REGLAGES
# ======================================================================
BROKER = "test.mosquitto.org"
PORT = 1883
PREFIXE = "epf-picar-tristan"        # meme valeur que topic-prefix dans le back

TOPIC_COMMANDE = PREFIXE + "/cmd"                    # on recoit les ordres ici
TOPIC_ACK = PREFIXE + "/ack"                         # on confirme chaque ordre
TOPIC_ETAT = PREFIXE + "/state"                      # etat du robot
TOPIC_EN_LIGNE = PREFIXE + "/status"                 # robot allume ou non
TOPIC_DISTANCE = PREFIXE + "/telemetry/distance"     # mesure ultrason
TOPIC_POSITION = PREFIXE + "/telemetry/imu"          # accelerometre

VITESSE_MAX = 40          # vitesse limitee pendant les tests (0 a 100)
DISTANCE_SECURITE = 20    # cm : en dessous, le robot s'arrete (mode securite)
PERIODE_MESURE = 0.5      # secondes entre deux mesures -> 2 Hz

# Servos (ports de la carte)
SERVO_ROUES = 0           # angleRoue = pin0
SERVO_CAMERA_H = 1        # servo horizontal = pin1
SERVO_CAMERA_V = 2        # servo vertical = pin2
ANGLE_TOUT_DROIT = 90
ANGLE_VIRAGE = 30         # de combien on tourne les roues


# ======================================================================
# 2. MATERIEL (comme dans les exemples Adeept)
# ======================================================================
# Servos : meme code que Examples/03_Servo/Servo180Degree.py
i2c = busio.I2C(SCL, SDA)
pca = PCA9685(i2c, address=0x5f)
pca.frequency = 50

def set_angle(ID, angle):
    servo_angle = servo.Servo(pca.channels[ID], min_pulse=500, max_pulse=2400, actuation_range=180)
    servo_angle.angle = angle

# Accelerometre : meme code que Examples/12_MPU6050/Mpu6050.py
capteur_mpu = mpu6050(0x68)


# ======================================================================
# 3. ETAT DU ROBOT (ce qu'on envoie a la page web)
# ======================================================================
etat = {
    "direction": 0,        # 1 = avance, -1 = recule, 0 = arrete
    "speed": 25,           # vitesse choisie
    "maxSpeed": VITESSE_MAX,
    "steering": 0,         # -1 = gauche, 0 = tout droit, 1 = droite
    "pan": 90,             # angle camera horizontal
    "tilt": 90,            # angle camera vertical
    "lights": {"1": False, "2": False, "3": False},
    "mode": "safe",        # "safe" = arret auto devant un obstacle, "manual" = non
    "safetyStop": False,   # True si le robot s'est arrete a cause d'un obstacle
}

derniere_distance = None   # derniere mesure valide (en cm)


# ======================================================================
# 4. FONCTIONS DE PILOTAGE (partie B)
# ======================================================================
def avancer():
    # En mode securite, on refuse d'avancer si un obstacle est trop proche
    if etat["mode"] == "safe" and derniere_distance is not None and derniere_distance < DISTANCE_SECURITE:
        etat["safetyStop"] = True
        return "obstacle trop proche"
    etat["safetyStop"] = False
    etat["direction"] = 1
    Move.move(etat["speed"], 1, "mid")
    return None

def reculer():
    etat["safetyStop"] = False
    etat["direction"] = -1
    Move.move(etat["speed"], -1, "mid")
    return None

def stop():
    etat["direction"] = 0
    Move.motorStop()
    return None

def gauche():
    etat["steering"] = -1
    set_angle(SERVO_ROUES, ANGLE_TOUT_DROIT + ANGLE_VIRAGE)
    return None

def droite():
    etat["steering"] = 1
    set_angle(SERVO_ROUES, ANGLE_TOUT_DROIT - ANGLE_VIRAGE)
    return None

def tout_droit():
    etat["steering"] = 0
    set_angle(SERVO_ROUES, ANGLE_TOUT_DROIT)
    return None

def changer_vitesse(vitesse):
    etat["speed"] = max(0, min(VITESSE_MAX, vitesse))   # on reste entre 0 et VITESSE_MAX
    if etat["direction"] != 0:                           # si on roule, on applique tout de suite
        Move.move(etat["speed"], etat["direction"], "mid")
    return None

def bouger_camera(pan, tilt):
    etat["pan"] = max(0, min(180, pan))
    etat["tilt"] = max(0, min(180, tilt))
    set_angle(SERVO_CAMERA_H, etat["pan"])
    set_angle(SERVO_CAMERA_V, etat["tilt"])
    return None

def lumiere(port, allumee):
    if port not in (1, 2, 3):
        return "port inconnu"
    etat["lights"][str(port)] = allumee
    Switch.switch(port, 1 if allumee else 0)
    return None


# ======================================================================
# 5. LECTURE DES CAPTEURS (partie D)
# ======================================================================
def lire_distance():
    """Renvoie (distance en cm, statut). Statut : ok, absent, incoherent ou out_of_range."""
    try:
        distance = Ultra.checkdist()
    except Exception:
        return None, "absent"            # le capteur ne repond pas
    if distance < 2:
        return distance, "incoherent"    # trop petit pour etre vrai
    if distance >= 199:
        return distance, "out_of_range"  # rien devant (le capteur va jusqu'a 2 m)
    return distance, "ok"

def lire_position():
    try:
        acc = capteur_mpu.get_accel_data()
        return {"x": round(acc["x"], 2), "y": round(acc["y"], 2), "z": round(acc["z"], 2)}
    except Exception:
        return None


# ======================================================================
# 6. MQTT (partie C)
# ======================================================================
def publier(topic, donnees, retenir=False):
    client.publish(topic, json.dumps(donnees), qos=1, retain=retenir)

def executer_commande(cmd):
    """Lit le champ "action" du message JSON et appelle la bonne fonction."""
    action = cmd.get("action")
    erreur = None

    if action == "forward":
        if "speed" in cmd:
            changer_vitesse(int(cmd["speed"]))
        erreur = avancer()
    elif action == "backward":
        if "speed" in cmd:
            changer_vitesse(int(cmd["speed"]))
        erreur = reculer()
    elif action == "stop":
        erreur = stop()
    elif action == "left":
        erreur = gauche()
    elif action == "right":
        erreur = droite()
    elif action == "center":
        erreur = tout_droit()
    elif action == "speed":
        erreur = changer_vitesse(int(cmd.get("speed", 25)))
    elif action == "camera":
        erreur = bouger_camera(int(cmd.get("pan", etat["pan"])), int(cmd.get("tilt", etat["tilt"])))
    elif action == "camera_center":
        erreur = bouger_camera(90, 90)
    elif action == "light":
        erreur = lumiere(int(cmd.get("port", 0)), bool(cmd.get("on")))
    elif action == "mode":
        if cmd.get("mode") in ("safe", "manual"):
            etat["mode"] = cmd["mode"]
        else:
            erreur = "mode inconnu"
    else:
        erreur = "action inconnue"

    # Accuse de reception : le back s'en sert pour mesurer la latence
    publier(TOPIC_ACK, {"id": cmd.get("id"), "action": action, "ok": erreur is None, "error": erreur})
    publier(TOPIC_ETAT, etat, retenir=True)

def on_connect(client, userdata, flags, reason_code, properties):
    print("Connecte au broker", BROKER)
    client.subscribe(TOPIC_COMMANDE, qos=1)
    publier(TOPIC_EN_LIGNE, {"online": True}, retenir=True)
    publier(TOPIC_ETAT, etat, retenir=True)

def on_disconnect(client, userdata, flags, reason_code, properties):
    print("Deconnecte du broker -> arret des moteurs")
    stop()                                   # securite : plus de reseau = on s'arrete

def on_message(client, userdata, msg):
    texte = msg.payload.decode("utf-8")
    print("Commande recue :", texte)
    try:
        cmd = json.loads(texte)
    except ValueError:
        print("Message ignore : ce n'est pas du JSON")
        return
    executer_commande(cmd)


# ======================================================================
# 7. PROGRAMME PRINCIPAL
# ======================================================================
def setup():
    Move.setup()
    Switch.switchSetup()
    stop()                                   # arret impose au demarrage
    tout_droit()
    bouger_camera(90, 90)

def boucle():
    global derniere_distance
    while True:
        # Distance (2 Hz)
        distance, statut = lire_distance()
        valide = (statut == "ok")
        derniere_distance = distance if valide else None
        publier(TOPIC_DISTANCE, {"distanceCm": distance, "valid": valide, "status": statut})

        # Position (accelerometre)
        position = lire_position()
        if position is not None:
            publier(TOPIC_POSITION, position)

        # Arret de securite : obstacle trop proche pendant qu'on avance
        if etat["mode"] == "safe" and etat["direction"] == 1 and valide and distance < DISTANCE_SECURITE:
            print("ARRET DE SECURITE : obstacle a", distance, "cm")
            stop()
            etat["safetyStop"] = True
            publier(TOPIC_ETAT, etat, retenir=True)

        time.sleep(PERIODE_MESURE)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
# Si le robot disparait sans prevenir, le broker publiera ce message a sa place
client.will_set(TOPIC_EN_LIGNE, json.dumps({"online": False}), qos=1, retain=True)

if __name__ == "__main__":
    setup()
    client.connect(BROKER, PORT, keepalive=10)
    client.loop_start()                      # MQTT tourne en arriere-plan
    print("Robot pret. Ctrl+C pour quitter.")
    try:
        boucle()
    except KeyboardInterrupt:
        pass
    finally:
        print("Fermeture : arret des moteurs")
        stop()                               # arret impose a la fermeture
        Switch.set_all_switch_off()
        tout_droit()
        publier(TOPIC_EN_LIGNE, {"online": False}, retenir=True)
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        Move.destroy()
