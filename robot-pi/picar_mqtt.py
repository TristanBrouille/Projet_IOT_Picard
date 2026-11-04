#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
picar_mqtt.py - Pilotage du PiCar-Pro (Adeept) par MQTT.

A lancer sur le Raspberry Pi, depuis le dossier Server du depot Adeept
(pour pouvoir importer Move.py, Switch.py et Ultra.py) :

    cp picar_mqtt.py ~/Adeept_PiCar-Pro/Server/
    cd ~/Adeept_PiCar-Pro/Server
    sudo python3 picar_mqtt.py --broker localhost

Sans materiel (test sur PC) :  python3 picar_mqtt.py --sim --broker <ip>

Topics (prefixe par defaut "picar") :
  picar/cmd                  <- commandes JSON
  picar/ack                  -> accuse de reception de chaque commande (id)
  picar/state                -> etat du robot (retenu)
  picar/status               -> {"online": true/false} (retenu + LWT)
  picar/telemetry/distance   -> distance ultrason a 2 Hz
  picar/telemetry/imu        -> accelerometre MPU6050 a 2 Hz

Exemple de commande :
  {"id": "abc", "action": "forward", "speed": 30}
Actions : forward, backward, stop, left, right, center, speed, camera,
          camera_center, light, mode
"""

import argparse
import json
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

# --------------------------------------------------------------------------
# Parametres (relevés dans les exemples Adeept et sur le kit)
# --------------------------------------------------------------------------
MAX_SPEED = 40            # vitesse limitee pour les tests (0..100)
DEFAULT_SPEED = 25
STEER_CHANNEL = 0         # angleRoue = pin0
CAM_PAN_CHANNEL = 1       # servo horizontal = pin1
CAM_TILT_CHANNEL = 2      # servo vertical = pin2
STEER_CENTER = 90
STEER_DELTA = 30          # WebServer.py Adeept : moveAngle(0, +/-30)
STEER_SIGN = 1            # mettre -1 si gauche/droite sont inverses
CAM_MIN, CAM_MAX = 0, 180
SAFE_DISTANCE_CM = 20     # arret de securite en mode "safe"
TELEMETRY_PERIOD = 0.5    # 2 Hz
DIST_MIN_CM, DIST_MAX_CM = 2.0, 199.0   # HC-SR04 : max_distance=2 m


def now_ms():
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# Couche materiel
# --------------------------------------------------------------------------
class Hardware:
    """Appels Adeept verifies : Move.move / Move.motorStop, servo PCA9685
    (0x5f, canal 0..2), Switch.switch(port, 0/1), Ultra.checkdist(), MPU6050."""

    def __init__(self):
        import Move as move          # Server/Move.py
        import Switch as switch      # Server/Switch.py
        from adafruit_motor import servo

        self.move = move
        self.switch = switch
        self.move.setup()
        self.switch.switchSetup()
        # On reutilise le meme PCA9685 que les moteurs (meme adresse 0x5f, 50 Hz)
        self._servos = {
            ch: servo.Servo(self.move.pwm_motor.channels[ch], min_pulse=500,
                            max_pulse=2400, actuation_range=180)
            for ch in (STEER_CHANNEL, CAM_PAN_CHANNEL, CAM_TILT_CHANNEL)
        }
        try:
            import Ultra as ultra    # Server/Ultra.py (Trig 23 / Echo 24)
            self.ultra = ultra
        except Exception as e:
            print("Ultrason indisponible :", e)
            self.ultra = None
        try:
            from mpu6050 import mpu6050
            self.mpu = mpu6050(0x68)
        except Exception as e:
            print("MPU6050 indisponible :", e)
            self.mpu = None

    def drive(self, speed, direction):
        if speed <= 0 or direction == 0:
            self.move.motorStop()
        else:
            self.move.move(speed, direction, "mid")

    def stop(self):
        self.move.motorStop()

    def servo(self, channel, angle):
        self._servos[channel].angle = angle

    def light(self, port, on):
        self.switch.switch(port, 1 if on else 0)

    def distance(self):
        if self.ultra is None:
            return None
        return float(self.ultra.checkdist())

    def imu(self):
        if self.mpu is None:
            return None
        a = self.mpu.get_accel_data()
        return {"x": round(a["x"], 3), "y": round(a["y"], 3), "z": round(a["z"], 3)}

    def close(self):
        try:
            self.move.motorStop()
            self.switch.set_all_switch_off()
            self.servo(STEER_CHANNEL, STEER_CENTER)
        finally:
            try:
                self.move.destroy()
            except Exception:
                pass


class SimHardware:
    """Robot simule pour tester la chaine sans Raspberry."""

    def __init__(self):
        import math
        import random
        self._math, self._random = math, random
        self._t0 = time.time()
        self.speed = 0

    def drive(self, speed, direction):
        self.speed = speed * direction
        print(f"[SIM] moteurs vitesse={speed} sens={direction}")

    def stop(self):
        self.speed = 0
        print("[SIM] moteurs stop")

    def servo(self, channel, angle):
        print(f"[SIM] servo {channel} -> {angle} deg")

    def light(self, port, on):
        print(f"[SIM] lumiere {port} -> {'on' if on else 'off'}")

    def distance(self):
        t = time.time() - self._t0
        if self._random.random() < 0.05:
            return None                      # mesure absente
        return round(60 + 45 * self._math.sin(t / 3), 2)

    def imu(self):
        r = self._random.uniform
        return {"x": round(r(-0.3, 0.3), 3), "y": round(r(-0.3, 0.3), 3),
                "z": round(9.81 + r(-0.1, 0.1), 3)}

    def close(self):
        self.stop()


# --------------------------------------------------------------------------
# Logique robot
# --------------------------------------------------------------------------
class Robot:
    def __init__(self, hw, client, prefix):
        self.hw = hw
        self.client = client
        self.prefix = prefix
        self.lock = threading.Lock()
        self.speed = DEFAULT_SPEED
        self.direction = 0            # 1 avance, -1 recule, 0 arret
        self.steering = 0             # -1 gauche, 0 centre, 1 droite
        self.pan = 90
        self.tilt = 90
        self.lights = {1: False, 2: False, 3: False}
        self.mode = "safe"            # "safe" = arret auto devant obstacle
        self.safety_stop = False
        self.last_distance = None
        self.running = True

    # --- etat -------------------------------------------------------------
    def state(self):
        return {
            "direction": self.direction,
            "moving": self.direction != 0,
            "speed": self.speed,
            "maxSpeed": MAX_SPEED,
            "steering": self.steering,
            "pan": self.pan,
            "tilt": self.tilt,
            "lights": {str(k): v for k, v in self.lights.items()},
            "mode": self.mode,
            "safetyStop": self.safety_stop,
            "ts": now_ms(),
        }

    def publish(self, sub, payload, retain=False, qos=0):
        self.client.publish(f"{self.prefix}/{sub}", json.dumps(payload), qos=qos, retain=retain)

    def publish_state(self):
        self.publish("state", self.state(), retain=True)

    # --- actions ----------------------------------------------------------
    def stop(self, reason="cmd"):
        with self.lock:
            self.direction = 0
            self.hw.stop()

    def _apply_drive(self):
        self.hw.drive(self.speed, self.direction)

    def handle(self, cmd):
        action = str(cmd.get("action", "")).lower()
        ok, error = True, None
        with self.lock:
            if action in ("forward", "backward"):
                if "speed" in cmd:
                    self.speed = clamp(int(cmd["speed"]), 0, MAX_SPEED)
                want = 1 if action == "forward" else -1
                if (want == 1 and self.mode == "safe" and self.last_distance is not None
                        and self.last_distance < SAFE_DISTANCE_CM):
                    ok, error = False, f"obstacle a {self.last_distance:.0f} cm"
                    self.safety_stop = True
                else:
                    self.safety_stop = False
                    self.direction = want
                    self._apply_drive()
            elif action == "stop":
                self.direction = 0
                self.hw.stop()
            elif action in ("left", "right", "center"):
                self.steering = {"left": -1, "right": 1, "center": 0}[action]
                angle = STEER_CENTER - self.steering * STEER_DELTA * STEER_SIGN
                self.hw.servo(STEER_CHANNEL, angle)
            elif action == "speed":
                self.speed = clamp(int(cmd.get("speed", DEFAULT_SPEED)), 0, MAX_SPEED)
                if self.direction != 0:
                    self._apply_drive()
            elif action == "camera":
                self.pan = clamp(int(cmd.get("pan", self.pan)), CAM_MIN, CAM_MAX)
                self.tilt = clamp(int(cmd.get("tilt", self.tilt)), CAM_MIN, CAM_MAX)
                self.hw.servo(CAM_PAN_CHANNEL, self.pan)
                self.hw.servo(CAM_TILT_CHANNEL, self.tilt)
            elif action == "camera_center":
                self.pan = self.tilt = 90
                self.hw.servo(CAM_PAN_CHANNEL, 90)
                self.hw.servo(CAM_TILT_CHANNEL, 90)
            elif action == "light":
                port = int(cmd.get("port", 0))
                if port in self.lights:
                    self.lights[port] = bool(cmd.get("on", not self.lights[port]))
                    self.hw.light(port, self.lights[port])
                else:
                    ok, error = False, "port inconnu"
            elif action == "mode":
                mode = str(cmd.get("mode", "")).lower()
                if mode in ("safe", "manual"):
                    self.mode = mode
                    if mode == "manual":
                        self.safety_stop = False
                else:
                    ok, error = False, "mode inconnu"
            else:
                ok, error = False, f"action inconnue : {action}"

        self.publish("ack", {"id": cmd.get("id"), "action": action, "ok": ok,
                             "error": error, "ts": now_ms()}, qos=1)
        self.publish_state()

    # --- telemetrie 2 Hz --------------------------------------------------
    def telemetry_loop(self):
        while self.running:
            t0 = time.time()
            self._publish_distance()
            imu = None
            try:
                imu = self.hw.imu()
            except Exception as e:
                print("Erreur MPU6050 :", e)
            if imu is not None:
                imu["ts"] = now_ms()
                self.publish("telemetry/imu", imu)
            time.sleep(max(0.0, TELEMETRY_PERIOD - (time.time() - t0)))

    def _publish_distance(self):
        try:
            d = self.hw.distance()
        except Exception as e:
            print("Erreur ultrason :", e)
            d = None

        if d is None:
            status, valid = "absent", False
        elif d != d or d < DIST_MIN_CM:          # NaN ou trop proche
            status, valid = "incoherent", False
        elif d >= DIST_MAX_CM:
            status, valid = "out_of_range", False
        else:
            status, valid = "ok", True

        self.last_distance = d if valid else None
        self.publish("telemetry/distance", {
            "distanceCm": round(d, 1) if d is not None and d == d else None,
            "valid": valid, "status": status, "ts": now_ms()})

        # Arret de securite : obstacle trop proche en marche avant
        if (valid and self.mode == "safe" and self.direction == 1
                and d < SAFE_DISTANCE_CM):
            self.stop("obstacle")
            self.safety_stop = True
            print(f"ARRET SECURITE : obstacle a {d:.1f} cm")
            self.publish_state()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# MQTT
# --------------------------------------------------------------------------
def make_client(client_id):
    try:  # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


def main():
    p = argparse.ArgumentParser(description="PiCar-Pro <-> MQTT")
    p.add_argument("--broker", default="test.mosquitto.org")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--prefix", default="epf-picar-tristan")
    p.add_argument("--sim", action="store_true", help="robot simule (sans materiel)")
    args = p.parse_args()

    hw = SimHardware() if args.sim else Hardware()
    hw.stop()                                  # arret impose au demarrage
    hw.servo(STEER_CHANNEL, STEER_CENTER)

    client = make_client(f"picar-robot-{int(time.time())}")
    robot = Robot(hw, client, args.prefix)
    status_topic = f"{args.prefix}/status"
    client.will_set(status_topic, json.dumps({"online": False}), qos=1, retain=True)

    def on_connect(c, userdata, flags, rc, properties=None):
        print("MQTT connecte, code", rc)
        c.subscribe(f"{args.prefix}/cmd", qos=1)
        c.publish(status_topic, json.dumps({"online": True, "ts": now_ms()}), qos=1, retain=True)
        robot.publish_state()

    def on_disconnect(c, userdata, *a):
        print("MQTT deconnecte -> arret de securite")
        robot.stop("mqtt")

    def on_message(c, userdata, msg):
        try:
            cmd = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(cmd, dict):
                raise ValueError("objet JSON attendu")
        except Exception as e:
            print("Message ignore :", e)
            robot.publish("ack", {"id": None, "ok": False, "error": "JSON invalide", "ts": now_ms()})
            return
        robot.handle(cmd)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    def shutdown(*_):
        robot.running = False
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)

    client.connect(args.broker, args.port, keepalive=10)
    client.loop_start()
    t = threading.Thread(target=robot.telemetry_loop, daemon=True)
    t.start()
    print(f"Robot pret (broker {args.broker}:{args.port}, prefixe '{args.prefix}'). Ctrl+C pour quitter.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        robot.running = False
        print("Fermeture : arret moteurs")
        hw.close()                             # arret impose a la fermeture
        client.publish(status_topic, json.dumps({"online": False}), qos=1, retain=True)
        time.sleep(0.2)
        client.loop_stop()
        client.disconnect()
        sys.exit(0)


if __name__ == "__main__":
    main()
