package com.epfiot.picard.mqtt;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken;
import org.eclipse.paho.client.mqttv3.MqttCallback;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import com.epfiot.picard.config.PicarProperties;
import com.epfiot.picard.web.TelemetryHub;

/**
 * Pont entre le broker MQTT du robot et le front :
 * - publie les commandes sur {prefix}/cmd
 * - ecoute {prefix}/status, /state, /ack et /telemetry/#
 * - mesure la latence commande -> prise en compte (ack du robot)
 */
@Service
public class MqttBridge implements MqttCallback, DisposableBean {

    private static final Logger log = LoggerFactory.getLogger(MqttBridge.class);
    private static final Pattern ACK_ID = Pattern.compile("\"id\"\\s*:\\s*\"([^\"]+)\"");

    private final PicarProperties props;
    private final TelemetryHub hub;
    private final MqttClient client;

    /** Dernier message recu par type (status, state, distance, imu, ack). */
    private final Map<String, String> last = new ConcurrentHashMap<>();
    /** id de commande -> instant d'envoi (System.nanoTime). */
    private final Map<String, Long> pending = new ConcurrentHashMap<>();
    private volatile boolean lastKnownConnected = false;

    public MqttBridge(PicarProperties props, TelemetryHub hub) throws MqttException {
        this.props = props;
        this.hub = hub;
        String id = props.clientId() + "-" + UUID.randomUUID().toString().substring(0, 8);
        this.client = new MqttClient(props.brokerUrl(), id, new MemoryPersistence());
        this.client.setCallback(this);
    }

    // ------------------------------------------------------------------ connexion

    /** Connexion au demarrage puis reconnexion toutes les 5 s si le broker est perdu. */
    @Scheduled(initialDelay = 500, fixedDelay = 5000)
    void ensureConnected() {
        if (client.isConnected()) return;
        try {
            MqttConnectOptions opts = new MqttConnectOptions();
            opts.setCleanSession(true);
            opts.setConnectionTimeout(3);
            opts.setKeepAliveInterval(10);
            if (props.username() != null && !props.username().isBlank()) {
                opts.setUserName(props.username());
                if (props.password() != null) opts.setPassword(props.password().toCharArray());
            }
            client.connect(opts);
            client.subscribe(new String[] {
                    props.topic("status"), props.topic("state"),
                    props.topic("ack"), props.topic("telemetry/#") },
                    new int[] { 1, 0, 1, 0 });
            log.info("MQTT connecte a {}", props.brokerUrl());
            setConnected(true);
        } catch (MqttException e) {
            log.warn("Broker MQTT injoignable ({}) : {}", props.brokerUrl(), e.getMessage());
            setConnected(false);
        }
    }

    private void setConnected(boolean connected) {
        if (connected != lastKnownConnected) {
            lastKnownConnected = connected;
            hub.broadcast("mqtt", mqttJson());
        }
    }

    public boolean isConnected() {
        return client.isConnected();
    }

    private String mqttJson() {
        return "{\"connected\":" + client.isConnected()
                + ",\"broker\":\"" + props.brokerUrl().replace("\"", "") + "\"}";
    }

    @Override
    public void destroy() {
        try {
            if (client.isConnected()) {
                publishCommand("stop", Map.of()); // securite : arret si le back s'arrete
                client.disconnect(1000);
            }
            client.close();
        } catch (MqttException ignored) {
            // fermeture
        }
    }

    // ------------------------------------------------------------------ commandes

    /**
     * Publie une commande JSON sur {prefix}/cmd.
     *
     * @param action action deja validee
     * @param params champs supplementaires (valeurs Number, Boolean ou String sures)
     * @return l'id de la commande
     */
    public String publishCommand(String action, Map<String, Object> params) throws MqttException {
        String id = UUID.randomUUID().toString();
        StringBuilder json = new StringBuilder("{\"id\":\"").append(id)
                .append("\",\"action\":\"").append(action).append('"');
        params.forEach((k, v) -> {
            json.append(",\"").append(k).append("\":");
            if (v instanceof Number || v instanceof Boolean) json.append(v);
            else json.append('"').append(String.valueOf(v).replaceAll("[\"\\\\]", "")).append('"');
        });
        json.append(",\"ts\":").append(System.currentTimeMillis()).append('}');

        MqttMessage msg = new MqttMessage(json.toString().getBytes(StandardCharsets.UTF_8));
        msg.setQos(1);
        pending.put(id, System.nanoTime());
        client.publish(props.topic("cmd"), msg);
        return id;
    }

    /** Oublie les commandes jamais acquittees (robot eteint...). */
    @Scheduled(fixedRate = 10000)
    void cleanPending() {
        long limit = System.nanoTime() - 10_000_000_000L;
        pending.values().removeIf(t -> t < limit);
    }

    // ------------------------------------------------------------------ reception

    @Override
    public void messageArrived(String topic, MqttMessage message) {
        String payload = new String(message.getPayload(), StandardCharsets.UTF_8);
        String sub = topic.substring(props.topicPrefix().length() + 1);
        String event = switch (sub) {
            case "status" -> "status";
            case "state" -> "state";
            case "ack" -> "ack";
            case "telemetry/distance" -> "distance";
            case "telemetry/imu" -> "imu";
            default -> null;
        };
        if (event == null || payload.isBlank() || payload.charAt(0) != '{') return;

        if ("ack".equals(event)) {
            payload = withLatency(payload);
        }
        last.put(event, payload);
        hub.broadcast(event, payload);
    }

    /** Ajoute "latencyMs" (envoi par le back -> ack du robot) au message d'ack. */
    private String withLatency(String ack) {
        Matcher m = ACK_ID.matcher(ack);
        if (!m.find()) return ack;
        Long sent = pending.remove(m.group(1));
        if (sent == null) return ack;
        double ms = (System.nanoTime() - sent) / 1_000_000.0;
        String latency = String.format(java.util.Locale.ROOT, "%.1f", ms);
        log.debug("Latence commande {} : {} ms", m.group(1), latency);
        return ack.substring(0, ack.lastIndexOf('}')) + ",\"latencyMs\":" + latency + "}";
    }

    @Override
    public void connectionLost(Throwable cause) {
        log.warn("Connexion MQTT perdue : {}", cause.getMessage());
        setConnected(false);
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
        // rien
    }

    /** Etat complet pour le chargement initial de la page (JSON brut). */
    public String snapshotJson() {
        return "{\"mqtt\":" + mqttJson()
                + ",\"status\":" + last.getOrDefault("status", "null")
                + ",\"state\":" + last.getOrDefault("state", "null")
                + ",\"distance\":" + last.getOrDefault("distance", "null")
                + ",\"imu\":" + last.getOrDefault("imu", "null")
                + ",\"ack\":" + last.getOrDefault("ack", "null") + "}";
    }
}
