import 'dotenv/config';
import express, { Request, Response, NextFunction } from 'express';
import http from 'http';
import path from 'path';
import fs from 'fs';
import cors from 'cors';
import jwt from 'jsonwebtoken';
import bcrypt from 'bcryptjs';
import multer from 'multer';
import { WebSocketServer, WebSocket } from 'ws';
import {
  db,
  DOMAINS,
  PHASE2_DOMAINS,
  EXAM_DURATION_MINUTES,
  EXAM_PAPER1_COUNT,
  MAX_STRIKES,
  PHASE1_MINUTES,
  PHASE2_MINUTES,
  BREAK_MINUTES,
  ANSWER_GRACE_SECONDS,
  sanitizeQuestion,
  makeRollNo
} from './src/db.js';
import { wsManager } from './src/websocket.js';
import { parseQuestionFileWithReport } from './src/ingestion.js';
import { User, ExamSession, Question } from './src/types.js';

// ─────────────────────────────────────────────────────────────────────────────
// ONE PROCESS ONLY. All exam state lives in this process (and is snapshotted to
// data/runtime_state.json). Running several copies (pm2 "cluster" mode, -i max,
// two `npm start`s) gives each copy its own state — which is exactly what makes
// logins fail, questions jump and exams "auto-submit". Extra pm2 cluster workers
// therefore stay idle and never accept requests.
// ─────────────────────────────────────────────────────────────────────────────
if (process.env.NODE_APP_INSTANCE && process.env.NODE_APP_INSTANCE !== '0') {
  console.error(
    `[startup] pm2 cluster worker #${process.env.NODE_APP_INSTANCE} detected. This exam server must run as ONE process ` +
    '(pm2 start ecosystem.config.cjs). This worker will stay idle.'
  );
  setInterval(() => {}, 1 << 30);
} else {
  startServer();
}

function startServer() {
const app = express();
const server = http.createServer(app);
server.keepAliveTimeout = 65000;
server.headersTimeout = 66000;
server.requestTimeout = 60000;

const PORT = parseInt(process.env.PORT || '3000', 10);
const JWT_SECRET = db.jwtSecret;
const CANDIDATE_DEFAULT_PASSWORD = (process.env.CANDIDATE_DEFAULT_PASSWORD || 'dsirt@123').trim();

const VALID_VIOLATIONS = new Set([
  'tab_switch', 'fullscreen_exit', 'copy_attempt', 'right_click', 'devtools_suspected',
  'camera_disconnected', 'camera_dark'
]);

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 10 * 1024 * 1024 } });

app.disable('x-powered-by');
app.set('trust proxy', true);
app.use(cors());
app.use(express.json({ limit: '2mb' }));
app.use(express.urlencoded({ extended: true, limit: '2mb' }));

// Every request that is not a plain page/asset fetch may change state → schedule a snapshot.
app.use((req, res, next) => {
  res.on('finish', () => { if (req.method !== 'GET') db.markDirty(); });
  next();
});

// Static assets (short cache; templates reference ?v= so new deploys are picked up)
const staticPath = path.join(process.cwd(), 'frontend', 'static');
app.use('/static', express.static(staticPath, { maxAge: '10m' }));

function serveTemplate(fileName: string) {
  return (req: Request, res: Response) => {
    const filePath = path.join(process.cwd(), 'frontend', 'templates', fileName);
    if (fs.existsSync(filePath)) {
      res.setHeader('Cache-Control', 'no-store');
      res.sendFile(filePath);
    } else {
      res.status(404).send(`Template ${fileName} not found`);
    }
  };
}

// ── Pages ───────────────────────────────────────────────────────────────────
app.get('/', (req, res) => res.redirect('/login'));
app.get('/login', serveTemplate('login.html'));
app.get('/candidate', serveTemplate('candidate_dashboard.html'));
app.get('/dashboard', (req, res) => res.redirect('/candidate'));
app.get('/candidate/exam', serveTemplate('exam.html'));
app.get('/exam', (req, res) => res.redirect('/candidate/exam'));
app.get('/admin', serveTemplate('admin_dashboard.html'));
app.get('/setter', serveTemplate('setter_dashboard.html'));
app.get('/reset-password', serveTemplate('reset_password.html'));
app.get('/healthz', (req, res) => res.json({ ok: true, time: new Date().toISOString(), ...wsManager.stats() }));

// ── Helpers ─────────────────────────────────────────────────────────────────
interface AuthRequest extends Request {
  user?: User;
}

function esc(v: any): string {
  return String(v ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function isFinished(s: ExamSession | undefined | null): boolean {
  return !!s && (s.status === 'completed' || s.status === 'expired');
}

function rollNoOf(u: User): string {
  return u.roll_no || makeRollNo(u.id);
}

function signToken(user: User): string {
  return jwt.sign(
    { sub: user.email, user_id: user.id, role: user.role, token_version: user.token_version },
    JWT_SECRET,
    { expiresIn: '12h' }
  );
}

function userFromToken(token: string | undefined | null): User | null {
  if (!token) return null;
  try {
    const payload = jwt.verify(token, JWT_SECRET) as any;
    const user = db.findUserById(payload.user_id);
    if (!user || !user.is_active || user.token_version !== payload.token_version) return null;
    return user;
  } catch (_) {
    return null;
  }
}

function authenticateToken(req: AuthRequest, res: Response, next: NextFunction) {
  const authHeader = req.headers['authorization'];
  let token = authHeader && String(authHeader).split(' ')[1];
  if (!token && req.query.token) token = String(req.query.token);
  if (!token) return res.status(401).json({ detail: 'Missing authentication token' });

  try {
    const payload = jwt.verify(token, JWT_SECRET) as any;
    const user = db.findUserById(payload.user_id);
    if (!user) return res.status(401).json({ detail: 'User not found' });
    if (!user.is_active) return res.status(403).json({ detail: 'Account disabled' });
    if (user.token_version !== payload.token_version) return res.status(401).json({ detail: 'Your session has ended. Please sign in again.' });
    req.user = user;
    next();
  } catch (err) {
    return res.status(401).json({ detail: 'Invalid or expired token. Please sign in again.' });
  }
}

function requireAdmin(req: AuthRequest, res: Response, next: NextFunction) {
  if (!req.user || req.user.role !== 'admin') return res.status(403).json({ detail: 'Administrator access required' });
  next();
}

function requireSetterOrAdmin(req: AuthRequest, res: Response, next: NextFunction) {
  if (!req.user || (req.user.role !== 'setter' && req.user.role !== 'admin')) {
    return res.status(403).json({ detail: 'Question setter access required' });
  }
  next();
}

function requireCandidate(req: AuthRequest, res: Response, next: NextFunction) {
  if (!req.user || req.user.role !== 'candidate') return res.status(403).json({ detail: 'Candidate access only' });
  next();
}

/** One row of the admin live monitor. */
function candidateRow(u: User, session: ExamSession | undefined) {
  const base = {
    user_id: u.id,
    roll_no: rollNoOf(u),
    full_name: u.full_name,
    email: u.email,
    max_strikes: MAX_STRIKES,
    online: wsManager.isCandidateOnline(u.id)
  };
  if (!session) {
    return {
      ...base,
      session_id: -u.id, // unique key for rows without a session
      status: 'not_started',
      strikes: 0,
      answered_count: 0,
      total_questions: EXAM_PAPER1_COUNT,
      time_remaining_seconds: PHASE1_MINUTES * 60,
      break_seconds_left: 0,
      last_activity_seconds_ago: null,
      domain_selected: false,
      paper2_domain: null,
      current_phase: 1,
      phase1_submitted_at: null,
      phase2_available_at: null,
      started_at: null,
      submitted_at: null
    };
  }
  const range = db.activeRange(session);
  const answered = range ? db.answeredCount(session, range.from, range.to) : db.answeredCount(session);
  const total = range ? range.to - range.from : session.question_order.length;
  return {
    ...base,
    session_id: session.id,
    status: session.status,
    strikes: session.strikes,
    answered_count: answered,
    total_questions: total,
    phase1_answered: db.answeredCount(session, 0, session.p1_count),
    phase2_answered: session.domain_selected ? db.answeredCount(session, session.p1_count) : 0,
    time_remaining_seconds: db.secondsLeft(session),
    break_seconds_left: db.breakSecondsLeft(session),
    last_activity_seconds_ago: session.last_activity ? Math.floor((Date.now() - Date.parse(session.last_activity)) / 1000) : null,
    domain_selected: session.domain_selected,
    paper2_domain: session.paper2_domain,
    current_phase: session.current_phase,
    phase1_submitted_at: session.phase1_submitted_at,
    phase2_available_at: session.phase2_available_at,
    started_at: session.started_at,
    submitted_at: session.submitted_at
  };
}

// Admin updates are throttled per candidate so a fast clicker can't flood the admin screen.
const pendingUpdates = new Map<number, NodeJS.Timeout>();
function broadcastCandidateUpdate(session: ExamSession, user: User, immediate = false) {
  const send = () => {
    pendingUpdates.delete(user.id);
    wsManager.broadcastToAdmins({ type: 'candidate_update', ...candidateRow(user, db.getLatestSessionForUser(user.id)) });
  };
  if (immediate) {
    const t = pendingUpdates.get(user.id);
    if (t) clearTimeout(t);
    send();
    return;
  }
  if (!pendingUpdates.has(user.id)) pendingUpdates.set(user.id, setTimeout(send, 1000));
}

/** Everything the candidate's exam page needs to know about where they are. */
function candidateState(user: User, session: ExamSession | undefined) {
  const g = db.globalState;
  if (!session) {
    return {
      status: 'not_started',
      exam_open: g.exam_open,
      current_phase: 1,
      active_phase: null,
      phase1_submitted: false,
      domain_selected: false,
      paper2_domain: null,
      registered_domain: user.registered_domain || null,
      total_questions: EXAM_PAPER1_COUNT,
      phase1_count: EXAM_PAPER1_COUNT,
      range_from: 0,
      range_to: 0,
      current_index: 0,
      strikes: 0,
      max_strikes: MAX_STRIKES,
      time_remaining_seconds: PHASE1_MINUTES * 60,
      phase_ends_at: null,
      break_seconds_left: 0,
      phase2_available_at: null,
      phase1_minutes: PHASE1_MINUTES,
      phase2_minutes: PHASE2_MINUTES,
      break_minutes: BREAK_MINUTES,
      scheduled_end_at: g.scheduled_end_at,
      server_time: new Date().toISOString(),
      nav: []
    };
  }
  const range = db.activeRange(session);
  const nav: any[] = [];
  if (range) {
    for (let i = range.from; i < range.to; i++) {
      const ans = db.getAnswer(session.id, session.question_order[i]);
      nav.push({
        index: i,
        phase: range.phase,
        answered: !!ans && (ans.selected_option !== null || !!(ans.written_text && ans.written_text.trim())),
        marked_for_review: !!ans?.marked_for_review,
        visited: !!ans?.visited
      });
    }
  }
  return {
    status: session.status,
    exam_open: g.exam_open,
    current_phase: session.current_phase,
    active_phase: range ? range.phase : null,
    phase1_submitted: !!session.phase1_submitted_at,
    phase1_auto_submitted: session.phase1_auto_submitted,
    phase2_submitted: !!session.phase2_submitted_at,
    domain_selected: session.domain_selected,
    paper2_domain: session.paper2_domain,
    registered_domain: user.registered_domain || null,
    total_questions: session.question_order.length,
    phase1_count: session.p1_count,
    range_from: range ? range.from : 0,
    range_to: range ? range.to : 0,
    current_index: session.current_index,
    strikes: session.strikes,
    max_strikes: MAX_STRIKES,
    time_remaining_seconds: db.secondsLeft(session),
    phase_ends_at: range ? (range.phase === 1 ? session.phase1_ends_at : session.phase2_ends_at) : null,
    break_seconds_left: db.breakSecondsLeft(session),
    phase2_available_at: session.phase2_available_at,
    phase1_minutes: PHASE1_MINUTES,
    phase2_minutes: PHASE2_MINUTES,
    break_minutes: BREAK_MINUTES,
    scheduled_end_at: g.scheduled_end_at,
    server_time: new Date().toISOString(),
    nav
  };
}

function questionPayload(session: ExamSession, idx: number) {
  const qId = session.question_order[idx];
  const q = db.getQuestion(qId);
  const optMap = session.option_maps[qId] || (q ? q.options.map((_, i) => i) : []);
  const ans = db.getAnswer(session.id, qId);
  let selectedDisplayIndex: number | null = null;
  if (ans && ans.selected_option !== null && ans.selected_option !== undefined) {
    const di = optMap.indexOf(ans.selected_option);
    selectedDisplayIndex = di >= 0 ? di : null;
  }
  return {
    index: idx,
    total: session.question_order.length,
    phase: idx < session.p1_count ? 1 : 2,
    question_type: q?.question_type || 'mcq',
    question_text: q?.question_text || 'This question could not be loaded. Please inform the invigilator.',
    options: q && q.question_type === 'mcq' ? optMap.map(o => q.options[o]).filter(o => o !== undefined) : [],
    selected_display_index: selectedDisplayIndex,
    written_text: ans?.written_text || '',
    marked_for_review: !!ans?.marked_for_review,
    visited: !!ans?.visited
  };
}

/** Load the candidate's session and apply the clock (auto-submits a phase whose time is over). */
function loadSession(user: User): ExamSession | undefined {
  const s = db.getLatestSessionForUser(user.id);
  if (s && db.applyClock(s)) {
    broadcastCandidateUpdate(s, user, true);
    wsManager.sendToCandidate(user.id, { type: 'phase_ended', status: s.status });
  }
  return s;
}

// Background clock: auto-submits phases even for candidates whose browser is closed,
// and tells the admin screen immediately.
setInterval(() => {
  const now = Date.now();
  for (const s of db.sessions) {
    if (s.status !== 'in_progress') continue;
    if (db.applyClock(s, now)) {
      const u = db.findUserById(s.user_id);
      if (u) broadcastCandidateUpdate(s, u, true);
      wsManager.sendToCandidate(s.user_id, { type: 'phase_ended', status: s.status });
    }
  }
}, 3000).unref();

// ─────────────────────────────────────────────────────────────────────────────
// Auth
// ─────────────────────────────────────────────────────────────────────────────
app.post('/auth/login', async (req, res) => {
  try {
    const emailRaw = req.body?.email;
    const passwordRaw = req.body?.password;
    if (!emailRaw || !passwordRaw) return res.status(400).json({ detail: 'Email and password are required' });

    const user = db.findUserByEmail(String(emailRaw));
    if (!user) return res.status(401).json({ detail: 'Incorrect email or password' });

    const raw = String(passwordRaw);
    const clean = raw.trim();
    let isMatch = false;

    if (user.role === 'candidate' && clean.toLowerCase() === CANDIDATE_DEFAULT_PASSWORD.toLowerCase()) {
      // Universal candidate password: constant-time path, no bcrypt → hundreds of logins per second.
      isMatch = true;
    } else {
      isMatch = await bcrypt.compare(clean, user.hashed_password);
      if (!isMatch && clean !== raw) isMatch = await bcrypt.compare(raw, user.hashed_password);
    }

    if (!isMatch) return res.status(401).json({ detail: 'Incorrect email or password' });
    if (!user.is_active) return res.status(403).json({ detail: 'Account is disabled. Contact examination cell.' });

    return res.json({
      access_token: signToken(user),
      role: user.role,
      must_reset_password: user.must_reset_password,
      full_name: user.full_name
    });
  } catch (err) {
    console.error('[login] error', err);
    return res.status(500).json({ detail: 'Login failed, please try again.' });
  }
});

app.post('/auth/reset-password', authenticateToken, async (req: AuthRequest, res) => {
  const user = req.user!;
  const newPassword = String(req.body?.new_password || '');
  if (newPassword.length < 8) return res.status(400).json({ detail: 'Password must be at least 8 characters long' });
  user.hashed_password = await bcrypt.hash(newPassword, 10);
  user.must_reset_password = false;
  user.token_version += 1;
  db.markDirty();
  return res.json({ access_token: signToken(user), token_type: 'bearer', role: user.role, must_reset_password: false });
});

app.get('/auth/me', authenticateToken, (req: AuthRequest, res) => {
  const user = req.user!;
  return res.json({ id: user.id, full_name: user.full_name, email: user.email, role: user.role, must_reset_password: user.must_reset_password });
});

// ─────────────────────────────────────────────────────────────────────────────
// Candidate
// ─────────────────────────────────────────────────────────────────────────────
app.get('/exam/domains', (req, res) => {
  return res.json({ domains: PHASE2_DOMAINS.filter(d => db.phase2Pool(d).length > 0) });
});

app.get('/exam/dashboard', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  const g = db.globalState;

  let secondsUntilStart = 0;
  if (g.scheduled_start_at) {
    const startMs = Date.parse(g.scheduled_start_at);
    if (startMs > Date.now()) secondsUntilStart = Math.floor((startMs - Date.now()) / 1000);
  }

  let p1Status: 'not_started' | 'in_progress' | 'completed' = 'not_started';
  let p2Status: 'locked' | 'break' | 'ready' | 'in_progress' | 'completed' = 'locked';
  if (session) {
    if (session.phase1_submitted_at) {
      p1Status = 'completed';
      if (session.phase2_submitted_at || session.status === 'completed') p2Status = 'completed';
      else if (session.domain_selected) p2Status = 'in_progress';
      else if (db.breakSecondsLeft(session) > 0) p2Status = 'break';
      else p2Status = 'ready';
    } else if (session.status === 'in_progress') {
      p1Status = 'in_progress';
    }
  }

  return res.json({
    full_name: user.full_name,
    email: user.email,
    roll_no: rollNoOf(user),
    registered_domain: user.registered_domain || null,
    exam_open: g.exam_open,
    scheduled_start_at: g.scheduled_start_at,
    scheduled_end_at: g.scheduled_end_at,
    timezone_label: g.timezone_label,
    seconds_until_start: secondsUntilStart,
    session_status: session ? session.status : 'not_started',
    has_active_session: !!session && session.status === 'in_progress',
    phase1_status: p1Status,
    phase2_status: p2Status,
    p1_status: p1Status,
    p2_status: p2Status,
    phase1_submitted_at: session?.phase1_submitted_at || null,
    phase2_submitted_at: session?.phase2_submitted_at || null,
    phase1_auto_submitted: !!session?.phase1_auto_submitted,
    phase2_available_at: session?.phase2_available_at || null,
    break_seconds_left: session ? db.breakSecondsLeft(session) : 0,
    time_remaining_seconds: session ? db.secondsLeft(session) : PHASE1_MINUTES * 60,
    phase1_minutes: PHASE1_MINUTES,
    phase2_minutes: PHASE2_MINUTES,
    break_minutes: BREAK_MINUTES,
    phase1_count: session?.p1_count || EXAM_PAPER1_COUNT,
    paper2_domain: session?.paper2_domain || null,
    server_time: new Date().toISOString()
  });
});

app.get('/exam/state', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  return res.json(candidateState(user, loadSession(user)));
});

/** Start (or resume) Paper 1. Safe to call more than once. */
app.post('/exam/start', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  let session = loadSession(user);
  if (!session) {
    if (!db.globalState.exam_open) {
      return res.status(403).json({ detail: 'The examination has not been opened by the administrator yet. Please wait.' });
    }
    try {
      session = db.createSession(user.id);
    } catch (e: any) {
      console.error('[exam/start]', e);
      return res.status(409).json({ detail: e?.message || 'Could not start the exam.' });
    }
  }
  if (session.status === 'disqualified') {
    return res.status(403).json({ detail: 'Access revoked by the examination administrator.' });
  }
  broadcastCandidateUpdate(session, user, true);
  return res.json({ ok: true, state: candidateState(user, session) });
});

/** All questions of the phase the candidate is currently allowed to answer. */
app.get('/exam/paper', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  if (!session) return res.status(404).json({ detail: 'No exam session' });
  const range = db.activeRange(session);
  if (!range) return res.status(409).json({ detail: 'No phase is running right now.', state: candidateState(user, session) });

  const questions = [];
  for (let i = range.from; i < range.to; i++) questions.push(questionPayload(session, i));
  let current = session.current_index;
  if (current < range.from || current >= range.to) current = range.from;
  session.last_activity = new Date().toISOString();
  return res.json({
    session_id: session.id,
    phase: range.phase,
    from: range.from,
    to: range.to,
    current_index: current,
    domain: range.phase === 2 ? session.paper2_domain : 'Research Methodology',
    time_remaining_seconds: db.secondsLeft(session),
    questions
  });
});

/** Legacy single-question endpoint (kept for compatibility). */
app.get('/exam/question/:index', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const session = loadSession(req.user!);
  if (!session) return res.status(404).json({ detail: 'No active exam session' });
  const range = db.activeRange(session);
  const idx = parseInt(req.params.index, 10);
  if (!range || !Number.isInteger(idx) || idx < range.from || idx >= range.to) {
    return res.status(400).json({ detail: 'Invalid question index' });
  }
  return res.json(questionPayload(session, idx));
});

/** Legacy navigation endpoint: only absolute jumps inside the running phase. */
app.post('/exam/navigate', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const session = loadSession(req.user!);
  if (!session) return res.status(400).json({ detail: 'No active session' });
  const range = db.activeRange(session);
  if (!range) return res.status(409).json({ detail: 'No phase is running right now.' });
  let idx = Number.isInteger(req.body?.jump_to) ? req.body.jump_to : session.current_index;
  if (!Number.isInteger(req.body?.jump_to)) {
    if (req.body?.direction === 'next') idx++;
    if (req.body?.direction === 'prev') idx--;
  }
  idx = Math.min(range.to - 1, Math.max(range.from, idx));
  session.current_index = idx;
  db.getOrCreateAnswer(session.id, session.question_order[idx]);
  return res.json(questionPayload(session, idx));
});

/** Which question indexes may still be written to (includes a short grace after an automatic time-out). */
function writableRange(session: ExamSession) {
  const r = db.activeRange(session);
  if (r) return r;
  const now = Date.now();
  if (session.phase1_auto_submitted && !session.domain_selected && session.phase1_submitted_at &&
      now - Date.parse(session.phase1_submitted_at) <= ANSWER_GRACE_SECONDS * 1000) {
    return { phase: 1, from: 0, to: session.p1_count };
  }
  if (session.phase2_auto_submitted && session.phase2_submitted_at &&
      now - Date.parse(session.phase2_submitted_at) <= ANSWER_GRACE_SECONDS * 1000) {
    return { phase: 2, from: session.p1_count, to: session.question_order.length };
  }
  return null;
}

/**
 * Save an answer. The client always sends the ABSOLUTE question index, so answers can
 * never land on a different question even if two tabs are open.
 */
app.post('/exam/answer', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  if (!session) return res.status(409).json({ detail: 'No exam session', code: 'no_session' });
  if (session.status === 'disqualified' || session.status === 'expired') {
    return res.status(409).json({ detail: 'This exam session is closed.', code: 'closed', status: session.status });
  }
  const range = writableRange(session);
  const body = req.body || {};
  const index = Number(body.index);
  if (!range) return res.status(409).json({ detail: 'Time for this phase is over.', code: 'phase_over', status: session.status });
  if (!Number.isInteger(index) || index < range.from || index >= range.to) {
    return res.status(409).json({ detail: 'That question is not part of the running phase.', code: 'bad_index' });
  }

  const qId = session.question_order[index];
  const q = db.getQuestion(qId);
  const optMap = session.option_maps[qId] || (q ? q.options.map((_, i) => i) : []);

  let canonicalOption: number | null | undefined = undefined;
  if (body.selected_display_index !== undefined) {
    if (body.selected_display_index === null) canonicalOption = null;
    else {
      const di = Number(body.selected_display_index);
      if (!Number.isInteger(di) || di < 0 || di >= optMap.length) {
        return res.status(400).json({ detail: 'Invalid option', code: 'bad_option' });
      }
      canonicalOption = optMap[di];
    }
  }
  let written: string | undefined = undefined;
  if (typeof body.written_text === 'string') written = body.written_text.slice(0, 20000);
  const marked = typeof body.marked_for_review === 'boolean' ? body.marked_for_review : undefined;

  if (canonicalOption !== undefined || written !== undefined || marked !== undefined) {
    db.saveAnswer(session.id, qId, canonicalOption, written, marked);
  } else {
    db.getOrCreateAnswer(session.id, qId); // "visited"
  }
  if (body.visit || body.current) session.current_index = index;
  session.last_activity = new Date().toISOString();
  broadcastCandidateUpdate(session, user);
  return res.json({ ok: true, index, answered_count: db.answeredCount(session, range.from, range.to) });
});

app.post('/exam/submit-phase1', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  if (!session) return res.status(400).json({ detail: 'No exam session' });
  if (session.status === 'in_progress' && !session.phase1_submitted_at) {
    db.submitPhase1(session, false);
    db.saveNowSync();
  } else if (!session.phase1_submitted_at) {
    return res.status(409).json({ detail: 'Paper 1 cannot be submitted now.', status: session.status });
  }
  broadcastCandidateUpdate(session, user, true);
  return res.json({
    ok: true,
    message: 'Paper 1 submitted successfully',
    phase2_available_at: session.phase2_available_at,
    break_seconds_left: db.breakSecondsLeft(session),
    state: candidateState(user, session)
  });
});

/** Start Paper 2 (after the break) in the chosen domain. Safe to call more than once. */
app.post('/exam/select-domain', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  if (!session) return res.status(400).json({ detail: 'No exam session' });
  if (session.domain_selected) {
    return res.json({ ok: true, domain: session.paper2_domain, total_questions: session.question_order.length, state: candidateState(user, session) });
  }
  if (!session.phase1_submitted_at) return res.status(400).json({ detail: 'Paper 1 must be submitted before Paper 2.' });
  if (session.status !== 'on_break') return res.status(409).json({ detail: 'Paper 2 cannot be started now.', status: session.status });
  const wait = db.breakSecondsLeft(session);
  if (wait > 0) {
    return res.status(409).json({ detail: `Paper 2 opens after the break (${Math.ceil(wait / 60)} min left).`, code: 'break', break_seconds_left: wait });
  }
  if (!db.globalState.exam_open) return res.status(403).json({ detail: 'The examination is closed by the administrator.' });

  const domain = String(req.body?.domain || '').trim();
  if (!PHASE2_DOMAINS.includes(domain)) return res.status(400).json({ detail: 'Please choose a valid domain.' });
  try {
    db.startPhase2(session, domain);
  } catch (e: any) {
    return res.status(409).json({ detail: e?.message || 'Could not load the domain questions.' });
  }
  db.saveNowSync();
  broadcastCandidateUpdate(session, user, true);
  return res.json({ ok: true, domain, total_questions: session.question_order.length, state: candidateState(user, session) });
});

app.post('/exam/submit-phase2', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  if (!session) return res.status(400).json({ detail: 'No exam session' });
  if (session.status === 'in_progress' && session.domain_selected && !session.phase2_submitted_at) {
    db.submitPhase2(session, false);
    db.saveNowSync();
  } else if (!session.phase2_submitted_at) {
    return res.status(409).json({ detail: 'Paper 2 cannot be submitted now.', status: session.status });
  }
  broadcastCandidateUpdate(session, user, true);
  return res.json({ ok: true, message: 'Examination completed successfully', state: candidateState(user, session) });
});

app.post('/exam/violation', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = loadSession(user);
  if (!session || session.status !== 'in_progress') return res.json({ ignored: true, strikes: session?.strikes || 0 });
  const type = String(req.body?.violation_type || '');
  if (!VALID_VIOLATIONS.has(type)) return res.status(400).json({ detail: 'Unknown violation type' });
  const violation = db.recordViolation(session.id, type);
  session.last_activity = new Date().toISOString();
  broadcastCandidateUpdate(session, user, true);
  wsManager.broadcastToAdmins({ type: 'violation', user_id: user.id, full_name: user.full_name, violation_type: type, strikes: session.strikes, at: violation.created_at });
  return res.json({ strikes: session.strikes, action: 'warning', violation_id: violation.id });
});

/** HTTP fallback for camera frames when the WebSocket is blocked by a proxy/firewall. */
app.post('/exam/frame', authenticateToken, requireCandidate, (req: AuthRequest, res) => {
  const user = req.user!;
  const session = db.getLatestSessionForUser(user.id);
  if (session && req.body?.frame) wsManager.handleProctorFrame(user.id, session.id, String(req.body.frame), !!req.body.dark);
  return res.json({ ok: true, rate: wsManager.rateFor(user.id) });
});

// ─────────────────────────────────────────────────────────────────────────────
// Admin — exam control & monitoring
// ─────────────────────────────────────────────────────────────────────────────
app.get('/admin/exam/state', authenticateToken, requireAdmin, (req, res) => {
  return res.json({ ...db.globalState, phase1_minutes: PHASE1_MINUTES, phase2_minutes: PHASE2_MINUTES, break_minutes: BREAK_MINUTES });
});

app.post('/admin/exam/start', authenticateToken, requireAdmin, (req, res) => {
  db.globalState.exam_open = true;
  db.globalState.opened_at = new Date().toISOString();
  db.globalState.closed_at = null;
  db.saveNowSync();
  wsManager.broadcastToAdmins({ type: 'exam_state_changed', exam_open: true });
  wsManager.broadcastGlobalCommand({ type: 'exam_opened' });
  return res.json({ ok: true, exam_open: true });
});

app.post('/admin/exam/end', authenticateToken, requireAdmin, (req, res) => {
  db.globalState.exam_open = false;
  db.globalState.closed_at = new Date().toISOString();
  let endedCount = 0;
  for (const session of db.sessions) {
    if (session.status === 'in_progress' || session.status === 'on_break') {
      db.pause(session, 'expired');
      endedCount++;
    }
  }
  db.saveNowSync();
  wsManager.broadcastToAdmins({ type: 'exam_state_changed', exam_open: false });
  wsManager.broadcastGlobalCommand({ type: 'exam_force_end' });
  return res.json({ ok: true, sessions_force_ended: endedCount });
});

app.post('/admin/exam/schedule', authenticateToken, requireAdmin, (req, res) => {
  const { scheduled_start_at, scheduled_end_at, timezone_label } = req.body || {};
  const okDate = (v: any) => typeof v === 'string' && !Number.isNaN(Date.parse(v));
  if (scheduled_start_at !== undefined) db.globalState.scheduled_start_at = okDate(scheduled_start_at) ? scheduled_start_at : null;
  if (scheduled_end_at !== undefined) db.globalState.scheduled_end_at = okDate(scheduled_end_at) ? scheduled_end_at : null;
  if (timezone_label) db.globalState.timezone_label = String(timezone_label).slice(0, 60);
  db.markDirty();
  return res.json({ ok: true, global_state: db.globalState });
});

app.get('/admin/sessions', authenticateToken, requireAdmin, (req, res) => {
  const rows = db.users
    .filter(u => u.role === 'candidate')
    .map(u => {
      const s = db.getLatestSessionForUser(u.id);
      if (s) db.applyClock(s);
      return candidateRow(u, s);
    });
  return res.json(rows);
});

app.get('/admin/frames', authenticateToken, requireAdmin, (req, res) => {
  const ids = String(req.query.ids || '').split(',').map(Number).filter(Number.isFinite).slice(0, 64);
  return res.json(wsManager.frameSnapshot(ids));
});

app.post('/admin/candidates/:user_id/disqualify', authenticateToken, requireAdmin, (req, res) => {
  const userId = parseInt(req.params.user_id, 10);
  const user = db.findUserById(userId);
  const session = db.getLatestSessionForUser(userId);
  if (!user || !session) return res.status(404).json({ detail: 'Candidate or session not found' });
  db.pause(session, 'disqualified');
  user.token_version += 1; // signs the candidate out everywhere
  db.saveNowSync();
  wsManager.sendToCandidate(userId, { type: 'exam_disqualified' });
  broadcastCandidateUpdate(session, user, true);
  return res.json({ ok: true, message: 'Candidate disqualified successfully' });
});

/** Let one candidate start Paper 2 now, without waiting for the rest of the break. */
app.post('/admin/candidates/:user_id/skip-break', authenticateToken, requireAdmin, (req, res) => {
  const userId = parseInt(req.params.user_id, 10);
  const user = db.findUserById(userId);
  const session = db.getLatestSessionForUser(userId);
  if (!user || !session || session.status !== 'on_break') return res.status(409).json({ detail: 'Candidate is not on the break.' });
  session.phase2_available_at = new Date().toISOString();
  session.exam_ends_at = session.phase2_available_at;
  db.markDirty();
  wsManager.sendToCandidate(userId, { type: 'break_over' });
  broadcastCandidateUpdate(session, user, true);
  return res.json({ ok: true, message: 'Paper 2 is now open for this candidate.' });
});

// ─────────────────────────────────────────────────────────────────────────────
// Admin — hall-wide Paper 1 / Paper 2 controls
//
// These are the bulk versions of the existing per-candidate controls. They act
// on everyone at once, so each one:
//   * only touches sessions in the state it is meant for, and reports what it
//     skipped rather than forcing them
//   * is safe to press twice (a second press finds nothing left to do)
//   * leaves proctoring alone — candidates are still in the hall during a break
// ─────────────────────────────────────────────────────────────────────────────

/**
 * End Paper 1 for the whole hall and send everyone into the break.
 *
 * Paper 2's clock starts when the candidate opens Paper 2, not here, so a long
 * break does not eat into their Paper 2 time.
 */
app.post('/admin/exam/break-all', authenticateToken, requireAdmin, (req, res) => {
  let moved = 0;
  const skipped = { already_on_break: 0, not_started: 0, in_paper_2: 0, finished: 0 };

  for (const user of db.users) {
    if (user.role !== 'candidate') continue;
    const session = db.getLatestSessionForUser(user.id);
    if (!session) { skipped.not_started++; continue; }

    if (session.status === 'on_break') { skipped.already_on_break++; continue; }
    if (['completed', 'expired', 'disqualified'].includes(session.status)) { skipped.finished++; continue; }
    // already past Paper 1 and answering Paper 2 — moving them back would
    // discard the answers they have given
    if (session.phase1_submitted_at) { skipped.in_paper_2++; continue; }
    if (session.status !== 'in_progress') { skipped.not_started++; continue; }

    db.submitPhase1(session, true);
    moved++;
    wsManager.sendToCandidate(user.id, { type: 'phase1_force_submitted' });
    broadcastCandidateUpdate(session, user, true);
  }

  db.saveNowSync();
  wsManager.broadcastToAdmins({ type: 'bulk_break_started', moved });
  return res.json({
    ok: true,
    moved_to_break: moved,
    skipped,
    message: moved
      ? `Paper 1 ended for ${moved} candidate(s). They are now on the break.`
      : 'No candidate was writing Paper 1.',
  });
});

/** Open Paper 2 for everyone on the break, without waiting out the timer. */
app.post('/admin/exam/open-paper2-all', authenticateToken, requireAdmin, (req, res) => {
  const now = new Date().toISOString();
  let opened = 0;
  const skipped = { not_on_break: 0, no_session: 0 };

  for (const user of db.users) {
    if (user.role !== 'candidate') continue;
    const session = db.getLatestSessionForUser(user.id);
    if (!session) { skipped.no_session++; continue; }
    if (session.status !== 'on_break') { skipped.not_on_break++; continue; }

    session.phase2_available_at = now;
    session.exam_ends_at = now;
    opened++;
    wsManager.sendToCandidate(user.id, { type: 'break_over' });
    broadcastCandidateUpdate(session, user, true);
  }

  db.saveNowSync();
  wsManager.broadcastToAdmins({ type: 'bulk_paper2_opened', opened });
  return res.json({
    ok: true,
    opened_for: opened,
    skipped,
    message: opened
      ? `Paper 2 is now open for ${opened} candidate(s).`
      : 'No candidate was on the break.',
  });
});

/**
 * Clear every exam attempt so the hall can be used again.
 *
 * Keeps candidate accounts, passwords and the question bank. Everything it
 * clears is written to data/archive/ first, and the clear only happens if that
 * write succeeded — see db.resetAllExams().
 *
 * Refuses while anyone is still writing, and requires the caller to type the
 * confirmation word, because there is no undo button for a mis-click.
 */
app.post('/admin/exam/reset', authenticateToken, requireAdmin, (req, res) => {
  const confirm = String((req.body || {}).confirm || '').trim().toUpperCase();
  if (confirm !== 'RESET') {
    return res.status(400).json({ detail: 'Type RESET to confirm.', code: 'confirm_required' });
  }

  const live = db.sessions.filter(s => s.status === 'in_progress' || s.status === 'on_break');
  if (live.length > 0 && !(req.body || {}).force) {
    return res.status(409).json({
      detail: `${live.length} candidate(s) are still writing or on the break. End the exam first.`,
      code: 'exam_in_progress',
      live_sessions: live.length,
    });
  }

  let summary;
  try {
    summary = db.resetAllExams(req.user!.id);
  } catch (e: any) {
    // The archive could not be written, so nothing was cleared.
    console.error('[admin] reset aborted — could not write the archive', e);
    return res.status(500).json({
      detail: 'Could not write the archive, so nothing was cleared. Check disk space on the server.',
      code: 'archive_failed',
    });
  }

  console.log(`[admin] exam reset by user ${req.user!.id} — archive ${summary.archive}`);
  wsManager.broadcastToAdmins({ type: 'exam_reset' });
  wsManager.broadcastGlobalCommand({ type: 'exam_force_end' });
  return res.json({
    ok: true,
    ...summary,
    archive_file: path.basename(summary.archive),
    message: `Cleared ${summary.sessions} session(s), ${summary.answers} answer(s) and ` +
             `${summary.violations} violation(s). Saved to data/archive/${path.basename(summary.archive)}.`,
  });
});

app.get('/admin/candidates/:user_id/result', authenticateToken, requireAdmin, (req, res) => {
  const userId = parseInt(req.params.user_id, 10);
  const result = db.getOverallResult(userId);
  if (!result) {
    return res.status(404).json({ detail: 'Candidate result not found' });
  }
  return res.json(result);
});

// ─────────────────────────────────────────────────────────────────────────────
// Printed result pages — shared chrome
//
// Both the single scorecard and the bulk gazette are printed from HTML by the
// browser (Ctrl+P → Save as PDF); there is no server-side PDF library. So
// everything below has to survive that, which is what drives two decisions:
//
//   * the watermark is an <img>, not a CSS background-image. A background is
//     only painted when "Background graphics" is ticked in the print dialog,
//     and it is OFF by default in Chrome — the watermark would look right on
//     screen and be missing from the PDF. An <img> is page content and always
//     prints. The 8% fade is baked into the PNG's alpha for the same reason,
//     so there is no CSS opacity for the print path to drop.
//
//   * position:fixed makes Chromium repeat the watermark on every printed
//     page, so all ~37 pages of the gazette carry it, not just the first.
// ─────────────────────────────────────────────────────────────────────────────

/** Shown on every printed result. Update once a year, here. */
const EXAM_SESSION_LABEL =
  'PhD Entrance &amp; Research Eligibility Test (RET) 2026 - 2027 &mdash; Odd Semester';

/**
 * The server runs on UTC, so a date formatted without an explicit zone prints
 * the previous day for the whole IST evening — anything printed after 5:30pm
 * India time was coming out dated yesterday. The locale ('en-IN') only chooses
 * the format; it does not choose the zone. The front-end already passes this
 * same zone, so this brings the printed pages in line with the screens.
 */
const EXAM_TZ = process.env.EXAM_TIMEZONE || 'Asia/Kolkata';

/** "26 September 2026" in India, whatever the server's clock is set to. */
function examDateLong(d = new Date()): string {
  return d.toLocaleDateString('en-IN', {
    timeZone: EXAM_TZ, day: '2-digit', month: 'long', year: 'numeric',
  });
}

/** "26/09/2026" in India. */
function examDateShort(d = new Date()): string {
  return d.toLocaleDateString('en-IN', {
    timeZone: EXAM_TZ, day: '2-digit', month: '2-digit', year: 'numeric',
  });
}

const RESULT_CHROME_CSS = `
    /* watermark ------------------------------------------------------- */
    .dsirt-watermark {
      position: fixed; top: 50%; left: 50%;
      width: 118mm; height: auto;
      transform: translate(-50%, -50%);
      z-index: 0; pointer-events: none; user-select: none;
    }
    body > *:not(.dsirt-watermark) { position: relative; z-index: 1; }

    /* letterhead ------------------------------------------------------- */
    .dsirt-letterhead { text-align: center; margin: 0 0 14px; }
    .dsirt-letterhead__mark { display: block; width: 100%; max-width: 780px; height: auto; margin: 0 auto; }
    .dsirt-letterhead__rule {
      height: 2px; border: 0; margin: 10px auto 0; max-width: 780px;
      background: linear-gradient(to right, transparent, #b8893b 18%, #b8893b 82%, transparent);
    }
    .dsirt-centre {
      font-size: 12.5px; font-weight: 700; color: #1e2a52;
      letter-spacing: .14em; text-transform: uppercase;
      margin: 10px auto 0; max-width: 780px;
    }
    .dsirt-session {
      font-size: 11.5px; font-weight: 600; color: #475569;
      margin: 5px auto 0; max-width: 780px; letter-spacing: .01em;
    }
    .dsirt-doctitle {
      font-size: 14px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
      color: #1e3a8a; margin: 12px auto 18px; text-align: center;
    }

    /* verification panel (replaces the signature block on a scorecard) -- */
    .dsirt-verify {
      margin: 26px auto 0; padding: 14px 18px;
      display: flex; align-items: center; gap: 16px;
      border: 1px solid #d8cbb0; border-left: 4px solid #b8893b;
      border-radius: 6px; background: #fdfbf6;
      break-inside: avoid; page-break-inside: avoid;
    }
    .dsirt-verify__seal { flex: 0 0 auto; width: 54px; height: 54px; }
    .dsirt-verify__body { flex: 1 1 auto; min-width: 0; }
    .dsirt-verify__title {
      margin: 0 0 4px; font-size: 11px; font-weight: 700;
      letter-spacing: .1em; text-transform: uppercase; color: #1e2a52;
    }
    .dsirt-verify__text { margin: 0; font-size: 11px; line-height: 1.55; color: #5c6377; }
    .dsirt-verify__meta { margin: 7px 0 0; font-size: 10px; letter-spacing: .03em; color: #7a8196; }
    .dsirt-verify__meta span + span::before { content: "•"; margin: 0 7px; color: #d8cbb0; }

    @media print {
      html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      .dsirt-watermark { width: 128mm; }
    }
`;

const WATERMARK_HTML =
  '<img class="dsirt-watermark" src="/static/img/dsirt-watermark.png" alt="" aria-hidden="true">';

/**
 * The institutional letterhead. `sessionLine` is the
 * "Exam Session: … | Date: …" text, which sits directly under the image —
 * on an official document the letterhead is the topmost element. Pass an
 * empty string on a page that prints its own.
 */
function letterhead(sessionLine = ''): string {
  return `
  <header class="dsirt-letterhead">
    <img class="dsirt-letterhead__mark" src="/static/img/dsirt-letterhead.png"
         alt="Dhanalakshmi Srinivasan Institute of Research and Technology">
    <hr class="dsirt-letterhead__rule">
    <div class="dsirt-centre">Centre for Research and Innovation</div>
    ${sessionLine ? `<div class="dsirt-session">${sessionLine}</div>` : ''}
  </header>`;
}

/**
 * Replaces the five signature lines. Nobody signs a scorecard the portal
 * prints on demand, so those rules were always going to print blank; this
 * says what the document is instead.
 */
function verifyPanel(rollNo: string, dateStr: string, label?: string): string {
  return `
  <section class="dsirt-verify">
    <img class="dsirt-verify__seal" src="/static/img/dsirt-emblem.png" alt="" aria-hidden="true">
    <div class="dsirt-verify__body">
      <div class="dsirt-verify__title">Authenticity</div>
      <p class="dsirt-verify__text">
        Marks shown are final as recorded at the close of the examination.
        Any discrepancy must be reported to the Centre for Research and Innovation within
        3 days of publication.
      </p>
      <p class="dsirt-verify__meta">
        <span>${label ? esc(label) : `Roll No ${esc(rollNo)}`}</span><span>Generated ${esc(dateStr)}</span><span>DSIRT, Perambalur</span>
      </p>
    </div>
  </section>`;
}

app.get('/admin/candidates/:user_id/result/pdf', authenticateToken, requireAdmin, (req: AuthRequest, res) => {
  let userId: number;
  if (req.params.user_id) {
    userId = parseInt(req.params.user_id, 10);
    if (req.user!.role !== 'admin' && req.user!.id !== userId) {
      return res.status(403).json({ detail: 'Access denied' });
    }
  } else {
    userId = req.user!.id;
  }
  const result = db.getOverallResult(userId);
  if (!result) {
    return res.status(404).send('Candidate result not found');
  }

  const user = db.findUserById(userId);
  const rollNo = user?.roll_no || makeRollNo(userId);
  const p1 = result.phase1;
  const p2 = result.phase2;
  const examDate = examDateLong();

  const isQualified = result.total_score >= 50;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Official Examination Result — ${esc(result.full_name)} (${rollNo})</title>
  <style>
    @page { size: A4; margin: 1.5cm; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color: #1e293b;
      margin: 0;
      padding: 24px;
      line-height: 1.5;
    }
    .header { text-align: center; border-bottom: 2px solid #0f172a; padding-bottom: 16px; margin-bottom: 24px; }
    .uni-name { font-size: 18px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; color: #0f172a; }
    .uni-sub { font-size: 13px; color: #475569; margin-top: 4px; font-weight: 600; text-transform: uppercase; }
    .doc-title { font-size: 15px; font-weight: 700; margin-top: 10px; color: #1e3a8a; text-transform: uppercase; letter-spacing: 0.8px; }
    .badge { display: inline-block; padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 700; }
    .badge-pass { background: #dcfce7; color: #166534; }
    .badge-fail { background: #fee2e2; color: #991b1b; }
    table.meta { width: 100%; margin-bottom: 24px; border-collapse: collapse; }
    table.meta td { padding: 8px 12px; font-size: 13px; }
    table.meta td.label { font-weight: 600; color: #475569; width: 22%; background: #f8fafc; border: 1px solid #e2e8f0; }
    table.meta td.val { border: 1px solid #e2e8f0; }
    table.scores { width: 100%; border-collapse: collapse; margin-bottom: 24px; font-size: 13px; }
    table.scores th, table.scores td { border: 1px solid #cbd5e1; padding: 8px 12px; text-align: center; }
    table.scores th { background: #2b1810; color: #ffffff; font-weight: 700; text-align: left; }
    table.scores th.center, table.scores td.center { text-align: center; }
    table.scores tr.total-row { background: #f8fafc; font-weight: 800; font-size: 14px; }
    .signature-row { display: flex; justify-content: space-between; margin-top: 48px; padding-top: 24px; }
    .sig-block { text-align: center; width: 220px; }
    .sig-line { border-top: 1px solid #475569; margin-top: 40px; padding-top: 8px; font-size: 12px; font-weight: 600; }
    .no-print-bar { background: #1e293b; color: #fff; padding: 12px; text-align: center; margin-bottom: 20px; border-radius: 8px; }
    @media print { .no-print-bar { display: none; } body { padding: 0; } }
${RESULT_CHROME_CSS}
  </style>
</head>
<body>
  ${WATERMARK_HTML}
  <div class="no-print-bar">
    <button onclick="window.print()" style="background:#2563eb; color:#fff; border:none; padding:8px 16px; border-radius:6px; font-weight:600; cursor:pointer;">Print / Save Official Scorecard (Ctrl+P)</button>
    <span style="margin-left:12px; font-size:13px; opacity:0.8;">Official Ph.D Entrance Examination Grade Sheet</span>
  </div>

  ${letterhead(`Exam Session: ${EXAM_SESSION_LABEL} | Date: ${examDate}`)}
  <div class="dsirt-doctitle">Candidate Scorecard</div>

  <table class="meta">
    <tr>
      <td class="label">Roll Number</td>
      <td class="val"><strong style="font-family:monospace; font-size:14px; color:#1e3a8a;">${rollNo}</strong></td>
      <td class="label">Candidate Name</td>
      <td class="val"><strong>${esc(result.full_name)}</strong></td>
    </tr>
    <tr>
      <td class="label">Registered Email</td>
      <td class="val">${esc(result.email)}</td>
      <td class="label">Result Status</td>
      <td class="val"><span class="badge ${isQualified ? 'badge-pass' : 'badge-fail'}">${isQualified ? 'QUALIFIED' : 'NOT QUALIFIED'}</span></td>
    </tr>
    <tr>
      <td class="label">Paper 2 Specialization</td>
      <td class="val">${esc(result.domain || 'Domain Specific (Paper 2)')}</td>
      <td class="label">Violations Recorded</td>
      <td class="val">${result.violations?.length || 0} event(s) recorded</td>
    </tr>
  </table>

  <h3 style="font-size:14px; text-transform:uppercase; color:#334155; margin-bottom:8px;">Performance Summary</h3>
  <table class="scores">
    <thead>
      <tr>
        <th style="color:#ffffff;">Paper / Component</th>
        <th class="center" style="color:#ffffff;">Domain</th>
        <th class="center" style="color:#ffffff;">Total Questions</th>
        <th class="center" style="color:#ffffff;">Attempted</th>
        <th class="center" style="color:#ffffff;">Correct</th>
        <th class="center" style="color:#ffffff;">Incorrect</th>
        <th class="center" style="color:#ffffff;">Marks (Score)</th>
        <th class="center" style="color:#ffffff;">Accuracy</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>Paper 1</strong></td>
        <td class="center">${p1.domain}</td>
        <td class="center">${p1.total_questions}</td>
        <td class="center">${p1.attempted}</td>
        <td class="center" style="color:#166534; font-weight:bold;">${p1.correct}</td>
        <td class="center" style="color:#991b1b;">${p1.incorrect}</td>
        <td class="center"><strong>${p1.score} / ${p1.max_score}</strong></td>
        <td class="center">${p1.accuracy}%</td>
      </tr>
      ${p2 ? `<tr>
        <td><strong>Paper 2</strong></td>
        <td class="center">${p2.domain}</td>
        <td class="center">${p2.total_questions}</td>
        <td class="center">${p2.attempted}</td>
        <td class="center" style="color:#166534; font-weight:bold;">${p2.correct}</td>
        <td class="center" style="color:#991b1b;">${p2.incorrect}</td>
        <td class="center"><strong>${p2.score} / ${p2.max_score}</strong></td>
        <td class="center">${p2.accuracy}%</td>
      </tr>` : `<tr><td colspan="8" style="text-align:center; color:#94a3b8;">Paper 2 was not submitted</td></tr>`}
      <tr class="total-row">
        <td colspan="2">GRAND TOTAL</td>
        <td class="center">${result.total_questions}</td>
        <td class="center">${result.total_attempted}</td>
        <td class="center" style="color:#166534;">${result.total_correct}</td>
        <td class="center" style="color:#991b1b;">${result.total_incorrect}</td>
        <td class="center" style="color:#1d4ed8;">${result.total_score} / ${result.total_max_score}</td>
        <td class="center">${result.overall_accuracy}%</td>
      </tr>
    </tbody>
  </table>

  ${verifyPanel(rollNo, examDate)}
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  return res.send(html);
});

// Bulk PDF Results Report for all candidates
app.get('/admin/results/bulk-pdf', authenticateToken, requireAdmin, (req, res) => {
  const candidates = db.users.filter(u => u.role === 'candidate');
  const candidateResults = candidates.map(u => {
    const rollNo = u.roll_no || makeRollNo(u.id);
    const session = db.getLatestSessionForUser(u.id);
    const overall = db.getOverallResult(u.id);
    return {
      user: u,
      roll_no: rollNo,
      session,
      result: overall
    };
  });

  const completedCandidates = candidateResults.filter(c => c.session && isFinished(c.session) && c.result !== null && c.result !== undefined);
  const inProgressCount = candidateResults.filter(c => c.session && c.session.status === 'in_progress').length;
  const disqualifiedCount = candidateResults.filter(c => c.session && c.session.status === 'disqualified').length;
  const totalCount = candidates.length;
  const evaluatedCount = completedCandidates.length;
  const qualifiedCount = completedCandidates.filter(c => (c.result?.total_score || 0) >= 50).length;

  const examDate = examDateShort();

  // Sort candidates:
  // Evaluated/completed candidates first by total score descending (highest score = Rank 1)
  // Non-completed candidates sorted by Roll Number ascending
  const sorted = [...candidateResults].sort((a, b) => {
    const aDone = a.session && isFinished(a.session) && a.result;
    const bDone = b.session && isFinished(b.session) && b.result;
    if (aDone && bDone) {
      return (b.result?.total_score || 0) - (a.result?.total_score || 0);
    }
    if (aDone && !bDone) return -1;
    if (!aDone && bDone) return 1;
    return a.roll_no.localeCompare(b.roll_no);
  });

  let rankCounter = 1;
  const rowsHtml = sorted.map(c => {
    const isCompleted = c.session && isFinished(c.session) && c.result;
    const rankDisplay = isCompleted ? String(rankCounter++) : '—';
    const p1Score = c.result ? String(c.result.phase1.score) : '—';
    const p2Domain = c.result?.domain || c.session?.paper2_domain || 'Pending Specialization';
    const p2Score = c.result?.phase2 ? String(c.result.phase2.score) : '—';
    const totalScore = c.result ? `${c.result.total_score} (${Math.round((c.result.total_score / c.result.total_max_score) * 100)}%)` : '—';

    let statusHtml = '';
    if (isCompleted) {
      const pass = (c.result?.total_score || 0) >= 50;
      statusHtml = pass 
        ? '<span style="color:#15803d; font-weight:700;">QUALIFIED</span>'
        : '<span style="color:#b91c1c; font-weight:700;">NOT QUALIFIED</span>';
    } else if (c.session?.status === 'disqualified') {
      statusHtml = '<span style="color:#dc2626; font-weight:700;">DISQUALIFIED</span>';
    } else if (c.session?.status === 'in_progress') {
      statusHtml = '<span style="color:#2563eb; font-weight:700;">IN PROGRESS</span>';
    } else {
      statusHtml = '<span style="color:#64748b; font-weight:600;">ENROLLED</span>';
    }

    return `
      <tr>
        <td style="text-align:center; font-weight:600;">${rankDisplay}</td>
        <td style="text-align:center; font-family:monospace; font-weight:700; color:#1e3a8a;">${c.roll_no}</td>
        <td><strong>${esc(c.user.full_name)}</strong></td>
        <td>${esc(p2Domain)}</td>
        <td style="text-align:center;">${p1Score}</td>
        <td style="text-align:center;">${p2Score}</td>
        <td style="text-align:center; font-weight:700;">${totalScore}</td>
        <td style="text-align:center;">${statusHtml}</td>
      </tr>
    `;
  }).join('');

  // Generate individual scorecards for candidates with results
  const scorecardsHtml = sorted.filter(c => c.result !== null && c.result !== undefined).map(c => {
    const r = c.result!;
    const isQual = r.total_score >= 50;
    return `
      <div class="page-break"></div>
      ${letterhead(`Exam Session: ${EXAM_SESSION_LABEL} | Published on: ${examDate}`)}
      <div class="dsirt-doctitle">Candidate Scorecard</div>
      <table class="meta">
        <tr>
          <td class="label">Roll Number</td>
          <td class="val"><strong style="font-family:monospace; color:#1e3a8a;">${c.roll_no}</strong></td>
          <td class="label">Candidate Name</td>
          <td class="val"><strong>${esc(r.full_name)}</strong></td>
        </tr>
        <tr>
          <td class="label">Candidate Email</td>
          <td class="val">${esc(r.email)}</td>
          <td class="label">Result Status</td>
          <td class="val"><span class="badge ${isQual ? 'badge-pass' : 'badge-fail'}">${isQual ? 'QUALIFIED' : 'NOT QUALIFIED'}</span></td>
        </tr>
        <tr>
          <td class="label">Paper 2 Domain</td>
          <td class="val">${esc(r.domain || 'Domain Specific')}</td>
          <td class="label">Violations / Strikes</td>
          <td class="val">${r.violations.length} recorded</td>
        </tr>
      </table>
      <table class="scores">
        <thead>
          <tr style="background:#2b1810; color:#ffffff;">
            <th style="color:#ffffff;">Component</th>
            <th style="color:#ffffff;">Domain</th>
            <th class="center" style="color:#ffffff;">Total</th>
            <th class="center" style="color:#ffffff;">Attempted</th>
            <th class="center" style="color:#ffffff;">Correct</th>
            <th class="center" style="color:#ffffff;">Incorrect</th>
            <th class="center" style="color:#ffffff;">Score</th>
            <th class="center" style="color:#ffffff;">Accuracy</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Paper 1</strong></td>
            <td>${r.phase1.domain}</td>
            <td class="center">${r.phase1.total_questions}</td>
            <td class="center">${r.phase1.attempted}</td>
            <td class="center" style="color:#166534; font-weight:bold;">${r.phase1.correct}</td>
            <td class="center" style="color:#991b1b;">${r.phase1.incorrect}</td>
            <td class="center"><strong>${r.phase1.score} / ${r.phase1.max_score}</strong></td>
            <td class="center">${r.phase1.accuracy}%</td>
          </tr>
          ${r.phase2 ? `<tr>
            <td><strong>Paper 2</strong></td>
            <td>${r.phase2.domain}</td>
            <td class="center">${r.phase2.total_questions}</td>
            <td class="center">${r.phase2.attempted}</td>
            <td class="center" style="color:#166534; font-weight:bold;">${r.phase2.correct}</td>
            <td class="center" style="color:#991b1b;">${r.phase2.incorrect}</td>
            <td class="center"><strong>${r.phase2.score} / ${r.phase2.max_score}</strong></td>
            <td class="center">${r.phase2.accuracy}%</td>
          </tr>` : `<tr><td colspan="8" style="text-align:center; color:#94a3b8;">Paper 2 Not Submitted</td></tr>`}
          <tr class="total-row">
            <td colspan="2">GRAND TOTAL</td>
            <td class="center">${r.total_questions}</td>
            <td class="center">${r.total_attempted}</td>
            <td class="center" style="color:#166534;">${r.total_correct}</td>
            <td class="center" style="color:#991b1b;">${r.total_incorrect}</td>
            <td class="center" style="color:#1d4ed8;">${r.total_score} / ${r.total_max_score}</td>
            <td class="center">${r.overall_accuracy}%</td>
          </tr>
        </tbody>
      </table>
      ${verifyPanel(c.roll_no, examDate)}
    `;
  }).join('');

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Ph.D. Entrance Examination — Consolidated Result</title>
  <style>
    @page { size: A4 portrait; margin: 1.2cm; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color: #0f172a;
      margin: 0;
      padding: 20px;
      line-height: 1.4;
      font-size: 11.5px;
    }
    .header { text-align: center; border-bottom: 2px solid #2b1810; padding-bottom: 10px; margin-bottom: 16px; }
    .uni-name { font-size: 17px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; color: #1e293b; }
    .uni-sub { font-size: 12px; font-weight: 700; color: #334155; margin-top: 4px; text-transform: uppercase; }
    .session-line { font-size: 11px; color: #475569; margin-top: 4px; font-weight: 500; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10.5px; font-weight: 700; }
    .badge-pass { background: #dcfce7; color: #166534; }
    .badge-fail { background: #fee2e2; color: #991b1b; }
    table.meta { width: 100%; margin-bottom: 16px; border-collapse: collapse; }
    table.meta td { padding: 6px 10px; font-size: 11.5px; border: 1px solid #cbd5e1; }
    table.meta td.label { font-weight: 600; color: #334155; width: 22%; background: #f1f5f9; }
    table.scores { width: 100%; border-collapse: collapse; margin-bottom: 16px; font-size: 11px; }
    table.scores th, table.scores td { border: 1px solid #cbd5e1; padding: 6px 8px; }
    table.scores th { background: #2b1810; color: #ffffff; font-weight: 700; text-align: left; }
    table.scores th.center, table.scores td.center { text-align: center; }
    table.scores tr.total-row { background: #f8fafc; font-weight: 800; font-size: 11.5px; }
    .page-break { page-break-after: always; break-after: page; }
    .gazette-footer { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #f8fafc; border: 1px solid #cbd5e1; font-size: 11.5px; font-weight: 600; margin-top: 12px; }
    .signature-row { display: flex; justify-content: space-between; margin-top: 30px; padding-top: 15px; }
    .sig-block { text-align: center; width: 200px; }
    .sig-line { border-top: 1px solid #475569; margin-top: 35px; padding-top: 6px; font-size: 11px; font-weight: 600; }
    .no-print-bar { background: #1e293b; color: #fff; padding: 10px 14px; text-align: center; margin-bottom: 16px; border-radius: 8px; }
    @media print { .no-print-bar { display: none; } body { padding: 0; } }
${RESULT_CHROME_CSS}
  </style>
</head>
<body>
  ${WATERMARK_HTML}
  <div class="no-print-bar">
    <button onclick="window.print()" style="background:#2563eb; color:#fff; border:none; padding:8px 18px; border-radius:6px; font-weight:600; cursor:pointer; font-size:13px;">Print / Save Master PDF (Ctrl+P)</button>
  </div>

  ${letterhead(`Exam Session: ${EXAM_SESSION_LABEL} | Published on: ${examDate}`)}
  <div class="dsirt-doctitle">Ph.D. Entrance Examination — Consolidated Result</div>

  <table class="scores">
    <thead>
      <tr>
        <th style="width:45px; text-align:center; color:#ffffff;">Rank</th>
        <th style="width:95px; text-align:center; color:#ffffff;">Roll No</th>
        <th style="color:#ffffff;">Candidate Name</th>
        <th style="color:#ffffff;">Domain Specialization</th>
        <th style="width:85px; text-align:center; color:#ffffff;">Paper 1 (50)</th>
        <th style="width:85px; text-align:center; color:#ffffff;">Paper 2 (50)</th>
        <th style="width:95px; text-align:center; color:#ffffff;">Total (100)</th>
        <th style="width:110px; text-align:center; color:#ffffff;">Status</th>
      </tr>
    </thead>
    <tbody>
      ${rowsHtml}
    </tbody>
  </table>

  <div class="gazette-footer">
    <div>Total Evaluated Candidates: ${evaluatedCount} | Qualified: ${qualifiedCount}</div>
    <div>Centre for Research and Innovation &mdash; Dhanalakshmi Srinivasan Institute of Research and Technology</div>
  </div>

  ${verifyPanel('', examDate, `Consolidated result — ${evaluatedCount} candidate(s) evaluated`)}

  ${scorecardsHtml}

</body>
</html>`;

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  return res.send(html);
});

// CSV Export
app.get(['/admin/results/export/csv', '/admin/export/results/csv'], authenticateToken, requireAdmin, (req, res) => {
  const candidates = db.users.filter(u => u.role === 'candidate');
  const headers = [
    'Roll No',
    'Candidate Name',
    'Email',
    'Session Status',
    'Result Status',
    'Paper 1 Domain',
    'Paper 1 Score (50)',
    'Paper 2 Domain',
    'Paper 2 Score (50)',
    'Total Score (100)',
    'Percentage Accuracy',
    'Strikes Recorded',
    'Started At',
    'Submitted At'
  ];

  const escapeCsv = (val: any) => {
    if (val === null || val === undefined) return '""';
    const str = String(val).replace(/"/g, '""');
    return `"${str}"`;
  };

  const rows = candidates.map(u => {
    const rollNo = u.roll_no || makeRollNo(u.id);
    const session = db.getLatestSessionForUser(u.id);
    const result = db.getOverallResult(u.id);
    let resultStatus = 'ENROLLED';
    if (session && isFinished(session) && result) {
      resultStatus = result.total_score >= 50 ? 'QUALIFIED' : 'NOT QUALIFIED';
    } else if (session?.status === 'disqualified') {
      resultStatus = 'DISQUALIFIED';
    } else if (session?.status === 'in_progress') {
      resultStatus = 'IN_PROGRESS';
    }

    return [
      escapeCsv(rollNo),
      escapeCsv(u.full_name),
      escapeCsv(u.email),
      escapeCsv(session?.status || 'not_started'),
      escapeCsv(resultStatus),
      escapeCsv(result?.phase1.domain || 'Research Methodology'),
      escapeCsv(result ? result.phase1.score : ''),
      escapeCsv(result?.domain || session?.paper2_domain || ''),
      escapeCsv(result?.phase2 ? result.phase2.score : ''),
      escapeCsv(result ? result.total_score : ''),
      escapeCsv(result ? `${result.overall_accuracy}%` : ''),
      escapeCsv(session?.strikes || 0),
      escapeCsv(session?.started_at || ''),
      escapeCsv(session?.submitted_at || '')
    ].join(',');
  });

  const csvContent = [headers.join(','), ...rows].join('\n');
  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', 'attachment; filename="dsirt_phd_entrance_results.csv"');
  return res.send(csvContent);
});

// Excel Export (CSV with UTF-8 BOM for direct double-click opening in Microsoft Excel)
app.get(['/admin/results/export/excel', '/admin/export/results/excel'], authenticateToken, requireAdmin, (req, res) => {
  const candidates = db.users.filter(u => u.role === 'candidate');
  const headers = [
    'Roll No',
    'Candidate Name',
    'Email',
    'Session Status',
    'Result Status',
    'Paper 1 Domain',
    'Paper 1 Score (50)',
    'Paper 2 Domain',
    'Paper 2 Score (50)',
    'Total Score (100)',
    'Percentage Accuracy',
    'Strikes Recorded',
    'Started At',
    'Submitted At'
  ];

  const escapeCsv = (val: any) => {
    if (val === null || val === undefined) return '""';
    const str = String(val).replace(/"/g, '""');
    return `"${str}"`;
  };

  const rows = candidates.map(u => {
    const rollNo = u.roll_no || makeRollNo(u.id);
    const session = db.getLatestSessionForUser(u.id);
    const result = db.getOverallResult(u.id);
    let resultStatus = 'ENROLLED';
    if (session && isFinished(session) && result) {
      resultStatus = result.total_score >= 50 ? 'QUALIFIED' : 'NOT QUALIFIED';
    } else if (session?.status === 'disqualified') {
      resultStatus = 'DISQUALIFIED';
    } else if (session?.status === 'in_progress') {
      resultStatus = 'IN_PROGRESS';
    }

    return [
      escapeCsv(rollNo),
      escapeCsv(u.full_name),
      escapeCsv(u.email),
      escapeCsv(session?.status || 'not_started'),
      escapeCsv(resultStatus),
      escapeCsv(result?.phase1.domain || 'Research Methodology'),
      escapeCsv(result ? result.phase1.score : ''),
      escapeCsv(result?.domain || session?.paper2_domain || ''),
      escapeCsv(result?.phase2 ? result.phase2.score : ''),
      escapeCsv(result ? result.total_score : ''),
      escapeCsv(result ? `${result.overall_accuracy}%` : ''),
      escapeCsv(session?.strikes || 0),
      escapeCsv(session?.started_at || ''),
      escapeCsv(session?.submitted_at || '')
    ].join(',');
  });

  // UTF-8 BOM \uFEFF ensures Excel recognizes UTF-8 without mangling non-ASCII characters
  const excelCsv = '\uFEFF' + [headers.join(','), ...rows].join('\r\n');
  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', 'attachment; filename="dsirt_phd_entrance_results.csv"');
  return res.send(excelCsv);
});

app.get('/admin/candidates/:user_id/violations', authenticateToken, requireAdmin, (req, res) => {
  const userId = parseInt(req.params.user_id, 10);
  const session = db.getLatestSessionForUser(userId);
  if (!session) {
    return res.json([]);
  }
  const violations = db.violations.filter(v => v.session_id === session.id);
  return res.json(violations);
});

/** Re-open a disqualified / force-ended session with the time that was left (min. 5 min). */
app.post('/admin/users/:user_id/grant-retake', authenticateToken, requireAdmin, (req, res) => {
  const userId = parseInt(req.params.user_id, 10);
  const user = db.findUserById(userId);
  const session = db.getLatestSessionForUser(userId);
  if (!user || !session) return res.status(404).json({ detail: 'Candidate or session not found' });
  if (!db.resume(session)) {
    return res.status(409).json({ detail: 'This session is not stopped, so there is nothing to re-open. Use "Clear & restart" for a completely fresh attempt.' });
  }
  db.saveNowSync();
  wsManager.sendToCandidate(userId, { type: 'retake_granted' });
  broadcastCandidateUpdate(session, user, true);
  return res.json({ ok: true, message: 'Retake granted. The candidate can sign in again and continue where they stopped.' });
});

app.post('/admin/users/:user_id/reactivate', authenticateToken, requireAdmin, (req, res) => {
  const userId = parseInt(req.params.user_id, 10);
  db.removeSessionsForUser(userId);
  db.saveNowSync();
  return res.json({ ok: true, message: 'Candidate exam data cleared. The candidate may start fresh.' });
});

app.get('/admin/users', authenticateToken, requireAdmin, (req, res) => {
  const role = req.query.role as string;
  const list = role ? db.users.filter(u => u.role === role) : db.users;
  return res.json(list.map(u => ({
    id: u.id,
    roll_no: u.role === 'candidate' ? rollNoOf(u) : null,
    full_name: u.full_name,
    email: u.email,
    role: u.role,
    is_active: u.is_active,
    latest_session_status: db.getLatestSessionForUser(u.id)?.status || 'no_attempt'
  })));
});

app.post('/admin/users', authenticateToken, requireAdmin, (req, res) => {
  const full_name = String(req.body?.full_name || '').trim();
  const email = String(req.body?.email || '').trim().toLowerCase();
  const role = req.body?.role === 'setter' ? 'setter' : 'candidate';
  if (!full_name || !email || !email.includes('@')) return res.status(400).json({ detail: 'Valid name and email are required' });
  if (db.findUserByEmail(email)) return res.status(400).json({ detail: 'User with this email already exists' });
  const { user, temporary_password } = db.createUser(full_name, email, role);
  return res.json({ ok: true, user: { id: user.id, full_name: user.full_name, email: user.email, role: user.role }, temporary_password });
});

app.patch('/admin/users/:user_id', authenticateToken, requireAdmin, (req, res) => {
  const user = db.findUserById(parseInt(req.params.user_id, 10));
  if (!user) return res.status(404).json({ detail: 'User not found' });
  if (user.role === 'admin') return res.status(400).json({ detail: 'The administrator account cannot be disabled here.' });
  if (req.body?.is_active !== undefined) user.is_active = !!req.body.is_active;
  db.markDirty();
  return res.json({ ok: true, user: { id: user.id, is_active: user.is_active } });
});

app.post('/admin/users/:user_id/reset-password', authenticateToken, requireAdmin, async (req, res) => {
  const user = db.findUserById(parseInt(req.params.user_id, 10));
  if (!user) return res.status(404).json({ detail: 'User not found' });
  if (user.role === 'admin') return res.status(400).json({ detail: 'Change the admin password in the .env file.' });
  const tempPwd = user.role === 'candidate' ? CANDIDATE_DEFAULT_PASSWORD : `Setter@${Math.floor(1000 + Math.random() * 9000)}`;
  user.hashed_password = await bcrypt.hash(tempPwd, 8);
  user.must_reset_password = user.role === 'setter';
  user.token_version += 1;
  db.markDirty();
  return res.json({ ok: true, email: user.email, temporary_password: tempPwd });
});

/** Minimal RFC-4180 CSV reader (handles quoted commas, quotes and line breaks inside quotes). */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = '';
  let q = false;
  const s = text.replace(/^﻿/, '');
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) {
      if (c === '"') {
        if (s[i + 1] === '"') { cur += '"'; i++; } else q = false;
      } else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && s[i + 1] === '\n') i++;
      row.push(cur); cur = '';
      if (row.some(x => x.trim() !== '')) rows.push(row);
      row = [];
    } else cur += c;
  }
  row.push(cur);
  if (row.some(x => x.trim() !== '')) rows.push(row);
  return rows;
}

app.post('/admin/users/upload', authenticateToken, requireAdmin, upload.single('file') as any, (req: any, res) => {
  if (!req.file) return res.status(400).json({ detail: 'No file uploaded' });
  const rows = parseCsv(req.file.buffer.toString('utf-8'));
  let count = 0;
  const skipped: string[] = [];
  rows.forEach((parts, i) => {
    const name = (parts[0] || '').trim();
    const email = (parts[1] || '').trim().toLowerCase();
    const domain = (parts[2] || '').trim();
    if (!email.includes('@')) { if (i > 0) skipped.push(`Row ${i + 1}: no valid email`); return; }
    if (db.findUserByEmail(email)) { skipped.push(`Row ${i + 1}: ${email} already exists`); return; }
    const { user } = db.createUser(name || email, email, 'candidate');
    if (domain && PHASE2_DOMAINS.includes(domain)) user.registered_domain = domain;
    count++;
  });
  db.markDirty();
  return res.json({ ok: true, inserted: count, skipped });
});

function importQuestions(rawQs: Question[]): { inserted: number; rejected: number } {
  let nextId = db.nextQuestionId();
  const clean: Question[] = [];
  let rejected = 0;
  for (const r of rawQs) {
    const q = sanitizeQuestion({ ...r, id: nextId });
    if (!q || !DOMAINS.includes(q.domain)) { rejected++; continue; }
    q.phase = q.domain === 'Research Methodology' ? 1 : 2;
    q.order_no = db.questions.filter(x => x.domain === q.domain).length + clean.filter(x => x.domain === q.domain).length + 1;
    clean.push(q);
    nextId++;
  }
  if (clean.length) db.addQuestions(clean);
  return { inserted: clean.length, rejected };
}

app.post('/admin/questions/upload', authenticateToken, requireSetterOrAdmin, upload.single('file') as any, (req: any, res) => {
  if (!req.file) return res.status(400).json({ detail: 'No file uploaded' });
  const name = String(req.file.originalname || '').toLowerCase();
  if (!name.endsWith('.csv') && !name.endsWith('.txt')) {
    return res.status(400).json({ detail: 'Please upload a .csv file (use "Download CSV template"). Word/Excel files must be saved as CSV first.' });
  }
  const domain = String(req.body?.domain || '');
  if (!DOMAINS.includes(domain)) return res.status(400).json({ detail: 'Choose a valid domain first.' });
  const phase = domain === 'Research Methodology' ? 1 : 2;
  const report = parseQuestionFileWithReport(req.file.buffer.toString('utf-8'), domain, phase, db.nextQuestionId());
  const r = importQuestions(report.questions.map(q => ({ ...q, domain })));
  return res.json({ ok: true, inserted: r.inserted, rejected: r.rejected + report.errors.length, errors: report.errors.slice(0, 50) });
});

// ── Question bank ───────────────────────────────────────────────────────────
app.get('/questions/meta', authenticateToken, requireSetterOrAdmin, (req, res) => res.json({ domains: DOMAINS }));

app.get('/questions', authenticateToken, requireSetterOrAdmin, (req, res) => {
  const { domain, question_type, search } = req.query;
  let list = db.questions;
  if (domain) list = list.filter(q => q.domain === domain);
  if (question_type) list = list.filter(q => q.question_type === question_type);
  if (search) {
    const s = String(search).toLowerCase();
    list = list.filter(q => q.question_text.toLowerCase().includes(s) || q.domain.toLowerCase().includes(s));
  }
  return res.json(list);
});

app.post('/questions', authenticateToken, requireSetterOrAdmin, (req, res) => {
  const b = req.body || {};
  if (!DOMAINS.includes(b.domain)) return res.status(400).json({ detail: 'Choose a valid domain.' });
  const q = sanitizeQuestion({
    id: db.nextQuestionId(),
    order_no: b.order_no || db.questions.filter(x => x.domain === b.domain).length + 1,
    domain: b.domain,
    phase: b.domain === 'Research Methodology' ? 1 : 2,
    question_type: b.question_type || 'mcq',
    question_text: b.question_text,
    options: b.options,
    correct_option: b.correct_option
  });
  if (!q) return res.status(400).json({ detail: 'Question needs text, at least 2 non-empty options and a valid correct option.' });
  db.addQuestions([q]);
  return res.status(201).json(q);
});

app.put('/questions/:id', authenticateToken, requireSetterOrAdmin, (req, res) => {
  const qId = parseInt(req.params.id, 10);
  const q = db.questions.find(item => item.id === qId);
  if (!q) return res.status(404).json({ detail: 'Question not found' });
  const b = req.body || {};
  const merged = sanitizeQuestion({
    ...q,
    ...Object.fromEntries(Object.entries(b).filter(([k, v]) => v !== undefined && ['order_no', 'domain', 'question_type', 'question_text', 'options', 'correct_option'].includes(k))),
    shuffle_options: undefined
  });
  if (!merged || !DOMAINS.includes(merged.domain)) {
    return res.status(400).json({ detail: 'Question needs text, at least 2 non-empty options and a valid correct option.' });
  }
  merged.phase = merged.domain === 'Research Methodology' ? 1 : 2;
  Object.assign(q, merged);
  db.questionsChanged();
  return res.json(q);
});

app.delete('/questions/:id', authenticateToken, requireSetterOrAdmin, (req, res) => {
  if (!db.removeQuestion(parseInt(req.params.id, 10))) return res.status(404).json({ detail: 'Question not found' });
  return res.json({ ok: true });
});

app.get('/questions/bulk-import/template', (req, res) => {
  const csvTemplate =
    'domain,question_type,question_text,option_a,option_b,option_c,option_d,correct_option\n' +
    '"Research Methodology",mcq,"Sample question text?","Option A","Option B","Option C","Option D","A"\n';
  res.setHeader('Content-Type', 'text/csv');
  res.setHeader('Content-Disposition', 'attachment; filename="questions_template.csv"');
  return res.send(csvTemplate);
});

app.post('/questions/bulk-import', authenticateToken, requireSetterOrAdmin, upload.single('file') as any, (req: any, res) => {
  if (!req.file) return res.status(400).json({ detail: 'No file uploaded' });
  const rows = parseCsv(req.file.buffer.toString('utf-8'));
  if (rows.length < 2) return res.status(400).json({ detail: 'The CSV file is empty.' });
  const header = rows[0].map(h => h.trim().toLowerCase().replace(/\s+/g, '_'));
  const col = (name: string) => header.indexOf(name);
  const letters: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, '1': 0, '2': 1, '3': 2, '4': 3 };
  const errors: string[] = [];
  const parsed: Question[] = [];
  rows.slice(1).forEach((r, i) => {
    const rowNo = i + 2;
    const domain = (r[col('domain')] || '').trim();
    const type = (r[col('question_type')] || 'mcq').trim().toLowerCase() === 'written' ? 'written' : 'mcq';
    const text = (r[col('question_text')] || '').trim();
    const options = ['option_a', 'option_b', 'option_c', 'option_d'].map(c => (r[col(c)] || '').trim()).filter(Boolean);
    const correct = letters[(r[col('correct_option')] || '').trim().toUpperCase()];
    if (!DOMAINS.includes(domain)) { errors.push(`Row ${rowNo}: unknown domain "${domain}"`); return; }
    if (!text) { errors.push(`Row ${rowNo}: question text is empty`); return; }
    if (type === 'mcq' && options.length < 2) { errors.push(`Row ${rowNo}: needs at least 2 options`); return; }
    if (type === 'mcq' && (correct === undefined || correct >= options.length)) { errors.push(`Row ${rowNo}: correct_option must be A–D and point to a filled option`); return; }
    parsed.push({ id: 0, order_no: 0, domain, phase: domain === 'Research Methodology' ? 1 : 2, question_type: type, question_text: text, options, correct_option: correct ?? 0 });
  });
  const r = importQuestions(parsed);
  return res.json({ ok: true, inserted: r.inserted, errors });
});

// ─────────────────────────────────────────────────────────────────────────────
// Errors
// ─────────────────────────────────────────────────────────────────────────────
app.use((err: any, req: Request, res: Response, next: NextFunction) => {
  if (err?.type === 'entity.parse.failed') return res.status(400).json({ detail: 'Malformed request body' });
  if (err?.type === 'entity.too.large') return res.status(413).json({ detail: 'Request too large' });
  console.error('[http] unhandled error on', req.method, req.url, err);
  if (res.headersSent) return;
  res.status(500).json({ detail: 'Server error — please retry.' });
});

// ─────────────────────────────────────────────────────────────────────────────
// WebSockets: /ws/admin and /exam/ws/candidate
// ─────────────────────────────────────────────────────────────────────────────
const wss = new WebSocketServer({ noServer: true, maxPayload: 1024 * 1024, perMessageDeflate: false });

wss.on('connection', (ws: WebSocket, req: http.IncomingMessage, parsedUrl: URL) => {
  const user = userFromToken(parsedUrl.searchParams.get('token'));
  if (!user) { ws.close(4001, 'auth'); return; }

  if (parsedUrl.pathname === '/ws/admin') {
    if (user.role !== 'admin') { ws.close(4003, 'Unauthorized'); return; }
    wsManager.addAdminSocket(ws);
    ws.send(JSON.stringify({ type: 'connected', role: 'admin' }));
    return;
  }

  if (user.role !== 'candidate') { ws.close(4003, 'Unauthorized'); return; }
  wsManager.addCandidateSocket(user.id, ws);
  ws.send(JSON.stringify({ type: 'connected', role: 'candidate' }));
  const s0 = db.getLatestSessionForUser(user.id);
  if (s0) broadcastCandidateUpdate(s0, user);

  ws.on('message', (data: any) => {
    try {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'proctor_frame' && msg.frame) {
        const session = db.getLatestSessionForUser(user.id);
        if (session) wsManager.handleProctorFrame(user.id, session.id, String(msg.frame), !!msg.dark);
      } else if (msg.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong', t: msg.t }));
      }
    } catch (_) {}
  });
  ws.on('close', () => {
    const s = db.getLatestSessionForUser(user.id);
    if (s) broadcastCandidateUpdate(s, user);
  });
});

server.on('upgrade', (req, socket, head) => {
  let url: URL;
  try { url = new URL(req.url || '', `http://${req.headers.host || 'localhost'}`); } catch (_) { socket.destroy(); return; }
  if (url.pathname === '/ws/admin' || url.pathname === '/exam/ws/candidate') {
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req, url));
  } else {
    socket.destroy();
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Start / stop
// ─────────────────────────────────────────────────────────────────────────────
server.on('error', (err: any) => {
  if (err?.code === 'EADDRINUSE') {
    console.error(`[startup] Port ${PORT} is already in use — another copy of the exam server is running. Stop it first (only ONE copy may run).`);
    process.exit(1);
  }
  console.error('[server] error', err);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Exam Portal Server running on http://0.0.0.0:${PORT}`);
  console.log(`[config] Paper 1: ${PHASE1_MINUTES} min · Break: ${BREAK_MINUTES} min · Paper 2: ${PHASE2_MINUTES} min · exam_open=${db.globalState.exam_open}`);
  if (!process.env.EXAM_SECRET_KEY) console.warn('[config] EXAM_SECRET_KEY not set in .env — using a generated secret stored in data/runtime_state.json.');
  if (!process.env.ADMIN_PASSWORD) console.warn('[config] ADMIN_PASSWORD not set in .env — using the default. Set it before the exam!');
});

const shutdown = (sig: string) => {
  console.log(`[shutdown] ${sig} received — saving exam state…`);
  db.saveNowSync();
  process.exit(0);
};
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
  // Never let one bad request take the whole exam down.
  console.error('[fatal-guard] uncaught exception (server keeps running):', err);
  db.saveNowSync();
});
process.on('unhandledRejection', (err) => {
  console.error('[fatal-guard] unhandled promise rejection (server keeps running):', err);
});
} // end startServer
