import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, NgZone, computed, inject, signal } from '@angular/core';

export type Action =
  | 'forward' | 'backward' | 'stop' | 'left' | 'right' | 'center'
  | 'speed' | 'camera' | 'camera_center' | 'light' | 'mode';

export interface CommandParams {
  speed?: number;
  pan?: number;
  tilt?: number;
  port?: number;
  on?: boolean;
  mode?: 'safe' | 'manual';
}

export interface RobotState {
  direction: -1 | 0 | 1;
  moving: boolean;
  speed: number;
  maxSpeed: number;
  steering: -1 | 0 | 1;
  pan: number;
  tilt: number;
  lights: Record<string, boolean>;
  mode: 'safe' | 'manual';
  safetyStop: boolean;
  ts: number;
}

export interface Distance {
  distanceCm: number | null;
  valid: boolean;
  status: 'ok' | 'absent' | 'incoherent' | 'out_of_range';
  ts: number;
}

export interface Imu { x: number; y: number; z: number; ts: number; }

export interface Ack {
  id: string | null;
  action: string;
  ok: boolean;
  error: string | null;
  ts: number;
  latencyMs?: number;
}

export interface LogEntry { time: string; text: string; level: 'ok' | 'warn' | 'err'; }

/** Base de l'API : vide = meme origine (proxy de ng serve vers le back Spring sur :8080). */
const API = '/api';

@Injectable({ providedIn: 'root' })
export class RobotService {
  private readonly http = inject(HttpClient);
  private readonly zone = inject(NgZone);
  private source?: EventSource;

  readonly backendUp = signal(false);
  readonly mqttConnected = signal(false);
  readonly broker = signal('');
  readonly robotOnline = signal(false);
  readonly state = signal<RobotState | null>(null);
  readonly distance = signal<Distance | null>(null);
  readonly distanceHistory = signal<(number | null)[]>([]);
  readonly imu = signal<Imu | null>(null);
  readonly lastLatency = signal<number | null>(null);
  readonly latencies = signal<number[]>([]);
  readonly log = signal<LogEntry[]>([]);

  readonly avgLatency = computed(() => {
    const l = this.latencies();
    return l.length ? l.reduce((a, b) => a + b, 0) / l.length : null;
  });

  /** A appeler cote navigateur uniquement (pas pendant le pre-rendu SSR). */
  connect(): void {
    if (this.source) return;
    this.http.get<Record<string, unknown>>(`${API}/state`).subscribe({
      next: (s) => this.applySnapshot(s),
      error: () => this.backendUp.set(false),
    });
    this.openStream();
  }

  private openStream(): void {
    const es = new EventSource(`${API}/stream`);
    this.source = es;
    const on = <T>(name: string, fn: (data: T) => void) =>
      es.addEventListener(name, (e) =>
        this.zone.run(() => {
          try {
            fn(JSON.parse((e as MessageEvent).data) as T);
          } catch {
            /* message invalide ignore */
          }
        }),
      );

    es.onopen = () => this.zone.run(() => this.backendUp.set(true));
    es.onerror = () =>
      this.zone.run(() => {
        // EventSource se reconnecte tout seul ; on signale juste la coupure
        this.backendUp.set(false);
        this.mqttConnected.set(false);
      });

    on<{ connected: boolean; broker: string }>('mqtt', (m) => this.applyMqtt(m));
    on<{ online: boolean }>('status', (s) => this.robotOnline.set(!!s.online));
    on<RobotState>('state', (s) => this.state.set(s));
    on<Distance>('distance', (d) => this.pushDistance(d));
    on<Imu>('imu', (i) => this.imu.set(i));
    on<Ack>('ack', (a) => this.applyAck(a));
  }

  private applySnapshot(s: Record<string, unknown>): void {
    this.backendUp.set(true);
    if (s['mqtt']) this.applyMqtt(s['mqtt'] as { connected: boolean; broker: string });
    if (s['status']) this.robotOnline.set(!!(s['status'] as { online: boolean }).online);
    if (s['state']) this.state.set(s['state'] as RobotState);
    if (s['distance']) this.pushDistance(s['distance'] as Distance);
    if (s['imu']) this.imu.set(s['imu'] as Imu);
  }

  private applyMqtt(m: { connected: boolean; broker: string }): void {
    this.backendUp.set(true);
    if (m.connected !== this.mqttConnected()) {
      this.addLog(m.connected ? `Broker connecté (${m.broker})` : 'Broker MQTT déconnecté',
        m.connected ? 'ok' : 'err');
    }
    this.mqttConnected.set(m.connected);
    this.broker.set(m.broker);
    if (!m.connected) this.robotOnline.set(false);
  }

  private pushDistance(d: Distance): void {
    this.distance.set(d);
    this.distanceHistory.update((h) => [...h, d.valid ? d.distanceCm : null].slice(-40));
  }

  private applyAck(a: Ack): void {
    if (a.latencyMs != null) {
      this.lastLatency.set(a.latencyMs);
      this.latencies.update((l) => [...l, a.latencyMs as number].slice(-50));
    }
    const lat = a.latencyMs != null ? ` · ${a.latencyMs.toFixed(1)} ms` : '';
    this.addLog(a.ok ? `✓ ${a.action}${lat}` : `✗ ${a.action} : ${a.error ?? 'refusé'}${lat}`,
      a.ok ? 'ok' : 'warn');
  }

  send(action: Action, params: CommandParams = {}): void {
    this.http.post<{ id: string }>(`${API}/commands`, { action, ...params }).subscribe({
      error: (e: HttpErrorResponse) =>
        this.addLog(`Échec ${action} : ${e.error?.error ?? e.statusText ?? 'back injoignable'}`, 'err'),
    });
  }

  resetLatency(): void {
    this.latencies.set([]);
    this.lastLatency.set(null);
  }

  private addLog(text: string, level: LogEntry['level']): void {
    const time = new Date().toLocaleTimeString('fr-FR');
    this.log.update((l) => [{ time, text, level }, ...l].slice(0, 30));
  }
}
