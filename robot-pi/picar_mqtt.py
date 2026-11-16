#!/usr/bin/env python3
# File name   : picar_mqtt.py
# Description : Pilotage du PiCar-Pro par MQTT (TP4 IoT)
#               Utilise les fichiers Move.py, servo.py, Switch.py et Ultra.py
#               du dossier Server du depot Adeept.
#
# A copier dans le dossier Server du depot Adeept, puis :
#     cd ~/Adeept_PiCar-Pro/Server
#     sudo python3 picar_mqtt.py

import json
import threading
import time

import paho.mqtt.client as mqtt
from mpu6050 import mpu6050

import Move      # moteurs des roues arriere (fichier Adeept)
import servo     # servos : roues avant et camera (fichier servo.py)
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
VIRAGE = 65               # de combien on tourne les roues (valeur PWM, 65 = environ 30 degres)
PAS_CAMERA = 20           # deplacement de la camera a chaque clic (valeur PWM, environ 9 degres)


# ======================================================================
# 2. MATERIEL (comme dans les exemples Adeept)
# ======================================================================
# Servos : on utilise les fonctions de servo.py.
# servo.py compte en "valeur PWM" (300 = milieu) et retient la position
# de chaque servo (pwm0_pos, pwm1_pos, pwm2_pos). Sur notre robot :
#   canal 0 = roues avant      -> servo.lookleft()  / servo.lookright()
#   canal 1 = camera horizontal -> servo.down()     / servo.up()   (noms venant d'un autre robot)
#   canal 2 = camera vertical   -> servo.lookup()   / servo.lookdown()
# Ces fonctions bloquent deja le servo entre pwmX_min et pwmX_max.

def pwm_en_degres(valeur):
    """Pour l'affichage sur la page : 102 = 0 degre, 300 = 90 degres, 491 = 180 degres."""
    return round((valeur - 102) * 180 / (491 - 102))

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
    servo.pwm0_pos = servo.pwm0_init        # on repart du centre...
    servo.lookleft(VIRAGE)                  # ...puis on tourne a gauche (servo.py)
    return None

def droite():
    etat["steering"] = 1
    servo.pwm0_pos = servo.pwm0_init
    servo.lookright(VIRAGE)                 # (servo.py)
    return None

def tout_droit():
    etat["steering"] = 0
    servo.pwm0_pos = servo.pwm0_init
    servo.pwm.set_pwm(SERVO_ROUES, 0, servo.pwm0_init)
    return None

def changer_vitesse(vitesse):
    etat["speed"] = max(0, min(VITESSE_MAX, vitesse))   # on reste entre 0 et VITESSE_MAX
    if etat["direction"] != 0:                           # si on roule, on applique tout de suite
        Move.move(etat["speed"], etat["direction"], "mid")
    return None

# Camera : chaque appel la deplace de PAS_CAMERA (fonctions de servo.py).
# Si elle part du mauvais cote, echanger servo.down / servo.up (ou lookup / lookdown).
def camera_gauche():
    servo.down(PAS_CAMERA)
    return maj_camera()

def camera_droite():
    servo.up(PAS_CAMERA)
    return maj_camera()

def camera_haut():
    servo.lookup(PAS_CAMERA)
    return maj_camera()

def camera_bas():
    servo.lookdown(PAS_CAMERA)
    return maj_camera()

def camera_centre():
    servo.pwm1_pos = servo.pwm1_init
    servo.pwm2_pos = servo.pwm2_init
    servo.pwm.set_pwm(SERVO_CAMERA_H, 0, servo.pwm1_init)
    servo.pwm.set_pwm(SERVO_CAMERA_V, 0, servo.pwm2_init)
    return maj_camera()

def maj_camera():
    """Recopie la position des servos camera dans l'etat (en degres pour la page)."""
    etat["pan"] = pwm_en_degres(servo.pwm1_pos)
    etat["tilt"] = pwm_en_degres(servo.pwm2_pos)
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
            etat["speed"] = max(0, min(VITESSE_MAX, int(cmd["speed"])))
        erreur = avancer()
    elif action == "backward":
        if "speed" in cmd:
            etat["speed"] = max(0, min(VITESSE_MAX, int(cmd["speed"])))
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
    elif action == "look_left":
        erreur = camera_gauche()
    elif action == "look_right":
        erreur = camera_droite()
    elif action == "look_up":
        erreur = camera_haut()
    elif action == "look_down":
        erreur = camera_bas()
    elif action == "camera_center":
        erreur = camera_centre()
    elif action == "light":
        erreur = lumiere(int(cmd.get("port", 0)), bool(cmd.get("on")))
    elif action == "mode":
        if cmd.get("mode") in ("safe", "manual"):
            etat["mode"] = cmd["mode"]
        else:
            erreur = "mode inconnu"
    else:
        erreur = "action inconnue"

    publier(TOPIC_ACK, {"id": cmd.get("id"), "action": action, "ok": erreur is None, "error": erreur})
    publier(TOPIC_ETAT, etat, retenir=True)

# properties=None et *args : marche avec paho-mqtt 1.x et 2.x
def on_connect(client, userdata, flags, reason_code, properties=None):
    print("Connecte au broker", BROKER)
    client.subscribe(TOPIC_COMMANDE, qos=1)
    publier(TOPIC_EN_LIGNE, {"online": True}, retenir=True)
    publier(TOPIC_ETAT, etat, retenir=True)

def on_disconnect(client, userdata, *args):
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
    camera_centre()

# Le programme utilise 3 threads (3 taches qui tournent en meme temps) :
#   - thread MQTT     : recoit les commandes (lance par client.loop_start())
#   - thread distance : mesure la distance et gere l'arret de securite
#   - thread position : lit l'accelerometre
en_marche = True   # passe a False a la fermeture pour arreter les threads

def thread_distance():
    """Mesure la distance 2 fois par seconde (2 Hz) et arrete le robot si besoin."""
    global derniere_distance
    while en_marche:
        distance, statut = lire_distance()
        valide = (statut == "ok")
        derniere_distance = distance if valide else None
        publier(TOPIC_DISTANCE, {"distanceCm": distance, "valid": valide, "status": statut})

        # Arret de securite : obstacle trop proche pendant qu'on avance
        if etat["mode"] == "safe" and etat["direction"] == 1 and valide and distance < DISTANCE_SECURITE:
            print("ARRET DE SECURITE : obstacle a", distance, "cm")
            stop()
            etat["safetyStop"] = True
            publier(TOPIC_ETAT, etat, retenir=True)

        time.sleep(PERIODE_MESURE)

def thread_position():
    """Lit l'accelerometre 2 fois par seconde et publie X, Y, Z."""
    while en_marche:
        position = lire_position()
        if position is not None:
            publier(TOPIC_POSITION, position)
        time.sleep(PERIODE_MESURE)


client = mqtt.Client("Jonah")
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.will_set(TOPIC_EN_LIGNE, json.dumps({"online": False}), qos=1, retain=True)

if __name__ == "__main__":
    setup()
    client.connect(BROKER, PORT, keepalive=10)
    client.loop_start()                      # MQTT tourne en arriere-plan
    print("Robot pret. Ctrl+C pour quitter.")

    # On lance les deux threads des capteurs.
    # daemon=True : ils s'arretent tout seuls quand le programme se termine.
    threading.Thread(target=thread_distance, daemon=True).start()
    threading.Thread(target=thread_position, daemon=True).start()

    try:
        while True:
            time.sleep(1)                    # le programme principal attend, les threads travaillent
    except KeyboardInterrupt:
        pass
    finally:
        en_marche = False                    # on demande aux threads de s'arreter
        print("Fermeture : arret des moteurs")
        stop()                               # arret impose a la fermeture
        Switch.set_all_switch_off()
        tout_droit()
        publier(TOPIC_EN_LIGNE, {"online": False}, retenir=True)
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        Move.destroy()
