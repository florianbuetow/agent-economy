import type { Particle, Ripple } from "./types";

// ─── Particle updates ──────────────────────────────────────────────────────────

/** Advance a particle by dt seconds. Returns true while alive. */
export function updateParticle(p: Particle, dt: number): boolean {
  p.x += p.vx * dt;
  p.y += p.vy * dt;
  p.vy += 80 * dt; // gravity — coins arc downward
  p.life -= dt;
  return p.life > 0;
}

// ─── Ripple updates ─────────────────────────────────────────────────────────────

/** Expand a ripple ring by dt seconds. Returns true while alive. */
export function updateRipple(r: Ripple, dt: number): boolean {
  r.r += (r.maxR / r.maxLife) * dt;
  r.life -= dt;
  return r.life > 0;
}

// ─── Spawn helpers ──────────────────────────────────────────────────────────────

/** Burst 6 gold square particles outward from (x, y). */
export function spawnCoinParticles(x: number, y: number): Particle[] {
  const particles: Particle[] = [];
  const count = 6;

  for (let i = 0; i < count; i++) {
    const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.4;
    const speed = 80 + Math.random() * 70; // 80-150
    const life = 0.8 + Math.random() * 0.4; // ~0.8-1.2s (centered around 1.0)
    const size = 3 + Math.random() * 2; // 3-5

    particles.push({
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      life,
      maxLife: life,
      size,
      color: "#F59E0B", // gold
    });
  }

  return particles;
}

/** Spawn 2-3 expanding ripple rings at (x, y) with the given color. */
export function spawnRipples(x: number, y: number, color: string): Ripple[] {
  const ripples: Ripple[] = [];
  const count = 2 + (Math.random() < 0.5 ? 1 : 0); // 2 or 3

  for (let i = 0; i < count; i++) {
    const maxR = 60 + Math.random() * 60; // 60-120
    const maxLife = 1.0 + Math.random() * 0.4; // 1.0-1.4s

    ripples.push({
      x,
      y,
      r: 0,
      maxR: maxR + i * 10, // stagger each ring slightly larger
      life: maxLife + i * 0.1, // stagger each ring slightly longer
      maxLife: maxLife + i * 0.1,
      color,
    });
  }

  return ripples;
}
