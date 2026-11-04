package com.epfiot.picard.web;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

import org.eclipse.paho.client.mqttv3.MqttException;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import com.epfiot.picard.mqtt.MqttBridge;

@RestController
@RequestMapping("/api")
public class RobotController {

    private static final Set<String> ACTIONS = Set.of(
            "forward", "backward", "stop", "left", "right", "center",
            "speed", "camera", "camera_center", "light", "mode");
    private static final Set<String> MODES = Set.of("safe", "manual");

    private final MqttBridge mqtt;
    private final TelemetryHub hub;

    public RobotController(MqttBridge mqtt, TelemetryHub hub) {
        this.mqtt = mqtt;
        this.hub = hub;
    }

    /** Envoie une commande au robot. */
    @PostMapping("/commands")
    public ResponseEntity<Map<String, Object>> command(@RequestBody CommandRequest req) {
        String action = req.action() == null ? "" : req.action().toLowerCase();
        if (!ACTIONS.contains(action)) {
            return ResponseEntity.badRequest().body(Map.of("error", "action inconnue : " + action));
        }
        if (!mqtt.isConnected()) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "broker MQTT non connecte"));
        }

        Map<String, Object> params = new LinkedHashMap<>();
        if (req.speed() != null) params.put("speed", clamp(req.speed(), 0, 100));
        if (req.pan() != null) params.put("pan", clamp(req.pan(), 0, 180));
        if (req.tilt() != null) params.put("tilt", clamp(req.tilt(), 0, 180));
        if (req.port() != null) params.put("port", clamp(req.port(), 1, 3));
        if (req.on() != null) params.put("on", req.on());
        if (req.mode() != null) {
            String mode = req.mode().toLowerCase();
            if (!MODES.contains(mode)) {
                return ResponseEntity.badRequest().body(Map.of("error", "mode inconnu : " + mode));
            }
            params.put("mode", mode);
        }

        try {
            String id = mqtt.publishCommand(action, params);
            return ResponseEntity.accepted().body(Map.of("id", id, "action", action));
        } catch (MqttException e) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("error", "publication MQTT impossible : " + e.getMessage()));
        }
    }

    /** Dernier etat connu (chargement de la page). */
    @GetMapping(value = "/state", produces = MediaType.APPLICATION_JSON_VALUE)
    public String state() {
        return mqtt.snapshotJson();
    }

    /** Flux temps reel : evenements mqtt, status, state, distance, imu, ack. */
    @GetMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream() {
        return hub.subscribe();
    }

    private static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(max, v));
    }
}
