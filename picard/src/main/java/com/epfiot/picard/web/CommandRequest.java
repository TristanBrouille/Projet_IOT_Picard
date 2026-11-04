package com.epfiot.picard.web;

/**
 * Corps de POST /api/commands. Seul "action" est obligatoire.
 * Exemples : {"action":"forward","speed":30} {"action":"light","port":2,"on":true}
 */
public record CommandRequest(String action, Integer speed, Integer pan, Integer tilt,
                             Integer port, Boolean on, String mode) {
}
