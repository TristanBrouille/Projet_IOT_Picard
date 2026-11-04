package com.epfiot.picard.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configuration MQTT (voir application.yaml, section "picar").
 *
 * @param brokerUrl   adresse du broker Mosquitto, ex : tcp://192.168.1.42:1883
 * @param clientId    identifiant MQTT du back
 * @param topicPrefix prefixe commun des topics (doit etre le meme que le script robot)
 * @param username    optionnel
 * @param password    optionnel
 */
@ConfigurationProperties(prefix = "picar")
public record PicarProperties(String brokerUrl, String clientId, String topicPrefix,
                              String username, String password) {

    public PicarProperties {
        if (brokerUrl == null || brokerUrl.isBlank()) brokerUrl = "tcp://raspberrypi.local:1883";
        if (clientId == null || clientId.isBlank()) clientId = "picard-back";
        if (topicPrefix == null || topicPrefix.isBlank()) topicPrefix = "picar";
    }

    public String topic(String suffix) {
        return topicPrefix + "/" + suffix;
    }
}
