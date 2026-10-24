import { WebSocket } from 'ws';

/**
 * Live connections for admins and candidates + the proctoring camera relay.
 *
 * Camera design (why admins used to see black / frozen tiles):
 *  - Candidates only upload frames at the rate somebody actually needs:
 *      focus  : an admin opened this candidate's focus view  -> ~5 fps, 640x480
 *      grid   : candidate is on an admin's visible grid page -> 1 fps, 320x240
 *      idle   : nobody is looking                            -> 1 frame / 5 s (keeps a fresh snapshot)
 *  - The latest frame per candidate is cached, so a newly opened tile shows a
 *    picture immediately instead of waiting.
 *  - Each admin only receives frames for the candidates it is watching, and frames
 *    are dropped (not queued) for an admin whose connection is slow, so video never lags behind.
 */

export type RateMode = 'idle' | 'grid' | 'focus';

export const RATE_PROFILES: Record<RateMode, { interval_ms: number; width: number; height: number; quality: number }> = {
  idle: { interval_ms: 5000, width: 320, height: 240, quality: 0.6 },
  grid: { interval_ms: 1000, width: 320, height: 240, quality: 0.6 },
  focus: { interval_ms: 200, width: 640, height: 480, quality: 0.7 }
};

const MAX_FRAME_CHARS = 600_000; // ~450 KB JPEG as base64
const ADMIN_BUFFER_LIMIT = 2_000_000;

interface AdminInfo {
  watch: Set<number>;
  focus: number | null;
  alive: boolean;
}

interface FrameRecord {
  frame: string;
  at: number;
  dark: boolean;
  session_id: number;
}

class ConnectionManager {
  private admins = new Map<WebSocket, AdminInfo>();
  private candidates = new Map<number, Set<WebSocket>>();
  private alive = new WeakMap<WebSocket, boolean>();
  private currentMode = new Map<number, RateMode>();
  latestFrames = new Map<number, FrameRecord>();

  constructor() {
    // Heartbeat: keeps connections open through nginx/firewalls and removes dead ones.
    setInterval(() => this.heartbeat(), 25_000).unref();
  }

  private trackAlive(ws: WebSocket) {
    this.alive.set(ws, true);
    ws.on('pong', () => this.alive.set(ws, true));
  }

  private heartbeat() {
    const all: WebSocket[] = [...this.admins.keys()];
    for (const set of this.candidates.values()) all.push(...set);
    for (const ws of all) {
      if (this.alive.get(ws) === false) {
        try { ws.terminate(); } catch (_) {}
        continue;
      }
      this.alive.set(ws, false);
      try { ws.ping(); } catch (_) {}
    }
  }

  // ── Admins ──────────────────────────────────────────────────────────────
  addAdminSocket(ws: WebSocket) {
    this.admins.set(ws, { watch: new Set(), focus: null, alive: true });
    this.trackAlive(ws);
    const cleanup = () => {
      if (this.admins.delete(ws)) this.recomputeRates();
    };
    ws.on('close', cleanup);
    ws.on('error', cleanup);
    ws.on('message', (data: any) => {
      let msg: any;
      try { msg = JSON.parse(data.toString()); } catch (_) { return; }
      const info = this.admins.get(ws);
      if (!info) return;
      if (msg.type === 'watch') {
        const ids: number[] = Array.isArray(msg.user_ids) ? msg.user_ids.map(Number).filter(Number.isFinite).slice(0, 64) : [];
        info.watch = new Set(ids);
        info.focus = Number.isFinite(Number(msg.focus)) && msg.focus !== null ? Number(msg.focus) : null;
        this.recomputeRates();
        // Send cached frames right away so tiles are never empty/black while waiting
        const wanted = new Set(ids);
        if (info.focus !== null) wanted.add(info.focus);
        for (const uid of wanted) {
          const f = this.latestFrames.get(uid);
          if (f) this.sendFrameTo(ws, uid, f);
        }
      }
    });
  }

  broadcastToAdmins(payload: any) {
    const data = JSON.stringify(payload);
    for (const ws of this.admins.keys()) {
      if (ws.readyState === WebSocket.OPEN) {
        try { ws.send(data); } catch (_) {}
      }
    }
  }

  // ── Candidates ──────────────────────────────────────────────────────────
  addCandidateSocket(userId: number, ws: WebSocket) {
    let set = this.candidates.get(userId);
    if (!set) { set = new Set(); this.candidates.set(userId, set); }
    set.add(ws);
    this.trackAlive(ws);
    const cleanup = () => {
      const s = this.candidates.get(userId);
      if (s) {
        s.delete(ws); // only this socket — a newer connection from the same user stays registered
        if (s.size === 0) this.candidates.delete(userId);
      }
    };
    ws.on('close', cleanup);
    ws.on('error', cleanup);
    this.sendRate(userId, ws);
  }

  isCandidateOnline(userId: number): boolean {
    const s = this.candidates.get(userId);
    return !!s && [...s].some(ws => ws.readyState === WebSocket.OPEN);
  }

  sendToCandidate(userId: number, payload: any) {
    const set = this.candidates.get(userId);
    if (!set) return;
    const data = JSON.stringify(payload);
    for (const ws of set) {
      if (ws.readyState === WebSocket.OPEN) {
        try { ws.send(data); } catch (_) {}
      }
    }
  }

  broadcastGlobalCommand(payload: any) {
    const data = JSON.stringify(payload);
    for (const set of this.candidates.values()) {
      for (const ws of set) {
        if (ws.readyState === WebSocket.OPEN) {
          try { ws.send(data); } catch (_) {}
        }
      }
    }
  }

  // ── Camera relay ────────────────────────────────────────────────────────
  modeFor(userId: number): RateMode {
    let mode: RateMode = 'idle';
    for (const info of this.admins.values()) {
      if (info.focus === userId) return 'focus';
      if (info.watch.has(userId)) mode = 'grid';
    }
    return mode;
  }

  private sendRate(userId: number, only?: WebSocket) {
    const mode = this.modeFor(userId);
    this.currentMode.set(userId, mode);
    const payload = JSON.stringify({ type: 'proctor_rate', mode, ...RATE_PROFILES[mode] });
    const targets = only ? [only] : [...(this.candidates.get(userId) || [])];
    for (const ws of targets) {
      if (ws.readyState === WebSocket.OPEN) {
        try { ws.send(payload); } catch (_) {}
      }
    }
  }

  private recomputeRates() {
    for (const userId of this.candidates.keys()) {
      if (this.currentMode.get(userId) !== this.modeFor(userId)) this.sendRate(userId);
    }
  }

  private sendFrameTo(ws: WebSocket, userId: number, f: FrameRecord) {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > ADMIN_BUFFER_LIMIT) return; // slow admin link: drop, don't lag
    try {
      ws.send(JSON.stringify({ type: 'proctor_frame', user_id: userId, session_id: f.session_id, frame: f.frame, at: f.at, dark: f.dark }));
    } catch (_) {}
  }

  handleProctorFrame(userId: number, sessionId: number, frame: string, dark = false): boolean {
    if (typeof frame !== 'string' || !frame.startsWith('data:image/') || frame.length > MAX_FRAME_CHARS) return false;
    const rec: FrameRecord = { frame, at: Date.now(), dark: !!dark, session_id: sessionId };
    this.latestFrames.set(userId, rec);
    for (const [ws, info] of this.admins) {
      if (info.focus === userId || info.watch.has(userId)) this.sendFrameTo(ws, userId, rec);
    }
    return true;
  }

  /** HTTP fallback for candidates whose WebSocket is blocked: tell them the current rate. */
  rateFor(userId: number) {
    const mode = this.modeFor(userId);
    return { mode, ...RATE_PROFILES[mode] };
  }

  frameSnapshot(ids: number[]) {
    const out: Record<number, { frame: string; at: number; dark: boolean }> = {};
    for (const id of ids) {
      const f = this.latestFrames.get(id);
      if (f) out[id] = { frame: f.frame, at: f.at, dark: f.dark };
    }
    return out;
  }

  stats() {
    let candidateSockets = 0;
    for (const s of this.candidates.values()) candidateSockets += s.size;
    return { admins: this.admins.size, candidates_online: this.candidates.size, candidate_sockets: candidateSockets };
  }
}

export const wsManager = new ConnectionManager();
