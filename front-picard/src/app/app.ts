import { DecimalPipe, UpperCasePipe } from '@angular/common';
import { Component, HostListener, afterNextRender, computed, inject, signal } from '@angular/core';
import { Action, RobotService } from './robot.service';

const KEYS: Record<string, Action> = {
  ArrowUp: 'forward', z: 'forward', w: 'forward',
  ArrowDown: 'backward', s: 'backward',
  ArrowLeft: 'left', q: 'left', a: 'left',
  ArrowRight: 'right', d: 'right',
  c: 'center',
  ' ': 'stop',
};

@Component({
  selector: 'app-root',
  imports: [DecimalPipe, UpperCasePipe],
  templateUrl: './app.html',
})
export class App {
  protected readonly robot = inject(RobotService);
  protected readonly speed = signal(25);
  protected readonly camStep = 10;

  protected readonly st = this.robot.state;
  protected readonly maxSpeed = computed(() => this.st()?.maxSpeed ?? 40);
  protected readonly ready = computed(() => this.robot.mqttConnected() && this.robot.robotOnline());

  /** Distance affichee sur une jauge 0-200 cm. */
  protected readonly gauge = computed(() => {
    const d = this.robot.distance();
    if (!d || !d.valid || d.distanceCm == null) return 0;
    return Math.min(100, (d.distanceCm / 200) * 100);
  });

  protected readonly distanceLevel = computed(() => {
    const d = this.robot.distance();
    if (!d || !d.valid || d.distanceCm == null) return 'none';
    if (d.distanceCm < 20) return 'danger';
    if (d.distanceCm < 50) return 'warn';
    return 'ok';
  });

  protected readonly statusLabel: Record<string, string> = {
    ok: 'Mesure valide',
    absent: 'Mesure absente',
    incoherent: 'Mesure incohérente',
    out_of_range: 'Hors portée (> 2 m)',
  };

  /** Courbe des 40 dernieres mesures (SVG 200x48). */
  protected readonly sparkline = computed(() => {
    const h = this.robot.distanceHistory();
    if (h.length < 2) return '';
    const step = 200 / 39;
    const off = 40 - h.length;
    let path = '';
    let pen = false;
    h.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      const x = (i + off) * step;
      const y = 46 - (Math.min(v, 200) / 200) * 44;
      path += `${pen ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)} `;
      pen = true;
    });
    return path;
  });

  /** Inclinaison approx. a partir de l'accelerometre (degres). */
  protected readonly tilt = computed(() => {
    const i = this.robot.imu();
    if (!i) return null;
    const deg = (r: number) => (r * 180) / Math.PI;
    return {
      roll: deg(Math.atan2(i.y, i.z)),
      pitch: deg(Math.atan2(-i.x, Math.hypot(i.y, i.z))),
    };
  });

  constructor() {
    afterNextRender(() => this.robot.connect());
  }

  protected cmd(action: Action): void {
    if (action === 'forward' || action === 'backward') {
      this.robot.send(action, { speed: this.speed() });
    } else {
      this.robot.send(action);
    }
  }

  protected setSpeed(value: string): void {
    this.speed.set(Number(value));
  }

  protected commitSpeed(): void {
    this.robot.send('speed', { speed: this.speed() });
  }

  protected camera(dPan: number, dTilt: number): void {
    const s = this.st();
    const pan = Math.max(0, Math.min(180, (s?.pan ?? 90) + dPan));
    const tilt = Math.max(0, Math.min(180, (s?.tilt ?? 90) + dTilt));
    this.robot.send('camera', { pan, tilt });
  }

  protected light(port: number): void {
    const on = !(this.st()?.lights?.[String(port)] ?? false);
    this.robot.send('light', { port, on });
  }

  protected toggleMode(): void {
    this.robot.send('mode', { mode: this.st()?.mode === 'safe' ? 'manual' : 'safe' });
  }

  protected bar(v: number | undefined): number {
    // accelerometre : +/- 12 m/s2 -> 0..100 %
    return Math.max(0, Math.min(100, 50 + ((v ?? 0) / 12) * 50));
  }

  @HostListener('window:keydown', ['$event'])
  protected onKey(e: KeyboardEvent): void {
    if ((e.target as HTMLElement)?.tagName === 'INPUT' && e.key !== ' ') return;
    const action = KEYS[e.key.length === 1 ? e.key.toLowerCase() : e.key];
    if (!action || e.repeat) return;
    e.preventDefault();
    this.cmd(action);
  }
}
