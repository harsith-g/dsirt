import bcrypt from 'bcryptjs';
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import {
  User,
  Question,
  ExamSession,
  Answer,
  Violation,
  ExamGlobalState,
  PhaseResult,
  CandidateResultDetail
} from './types.js';
import { loadAllQuestionsFromDisk } from './ingestion.js';

// ─────────────────────────────────────────────────────────────────────────────
// Configuration (override any of these in .env)
// ─────────────────────────────────────────────────────────────────────────────
function envInt(name: string, fallback: number): number {
  const v = parseInt(process.env[name] || '', 10);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}
function envMinutes(name: string, fallback: number): number {
  const v = parseFloat(process.env[name] || '');
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

export const DOMAINS = [
  'Research Methodology',
  'Agriculture',
  'Agricultural - Agronomy',
  'Agricultural - Entomology',
  'Agricultural - Pathology',
  'Bio-Science',
  'Computer Science',
  'Commerce & Management Studies',
  'English',
  'Mathematics',
  'Tamil'
];
export const PHASE2_DOMAINS = DOMAINS.filter(d => d !== 'Research Methodology');

export const PHASE1_MINUTES = envMinutes('PHASE1_MINUTES', 60);
export const PHASE2_MINUTES = envMinutes('PHASE2_MINUTES', 60);
export const BREAK_MINUTES = envMinutes('BREAK_MINUTES', 15);
/** kept for older screens that show a single duration */
export const EXAM_DURATION_MINUTES = PHASE1_MINUTES + PHASE2_MINUTES;
export const EXAM_PAPER1_COUNT = envInt('EXAM_PAPER1_COUNT', 50);
export const EXAM_PAPER2_COUNT = envInt('EXAM_PAPER2_COUNT', 50);
export const MAX_STRIKES = 3;

/**
 * Prefix for generated roll numbers. Changing this changes only roll numbers
 * issued from now on — candidates already in data/runtime_state.json keep the
 * number they were given. See RESULT_CHANGES.md before renumbering them.
 */
export const ROLL_PREFIX = process.env.ROLL_PREFIX || 'PHD2026';

/** `PHD2026-007` */
export function makeRollNo(n: number): string {
  return `${ROLL_PREFIX}-${String(n).padStart(3, '0')}`;
}
/** answers arriving this many seconds after a phase ends are still accepted (network lag) */
export const ANSWER_GRACE_SECONDS = 20;

const DATA_DIR = path.join(process.cwd(), 'data');
const STATE_FILE = path.join(DATA_DIR, 'runtime_state.json');
const STATE_VERSION = 2;

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
export function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = crypto.randomInt(0, i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const OPTION_REFERENCE_RE = /\bboth\b|all\s+(of\s+)?the\s+above|none\s+of\s+the\s+above|all\s+of\s+these|^none$/i;

/** Clean a question and decide whether it is safe to show. Returns null if it is broken. */
export function sanitizeQuestion(raw: any): Question | null {
  if (!raw || typeof raw !== 'object') return null;
  const text = String(raw.question_text ?? '').replace(/\s+/g, ' ').trim();
  if (text.length < 3) return null;
  const type = raw.question_type === 'written' ? 'written' : 'mcq';
  let options: string[] = Array.isArray(raw.options)
    ? raw.options.map((o: any) => String(o ?? '').replace(/\s+/g, ' ').trim())
    : [];
  let correct = Number(raw.correct_option);
  if (type === 'mcq') {
    if (options.length < 2 || options.some(o => o.length === 0)) return null;
    if (!Number.isInteger(correct) || correct < 0 || correct >= options.length) return null;
  } else {
    options = [];
    correct = 0;
  }
  const shuffleOk = raw.shuffle_options === false ? false : !options.some(o => OPTION_REFERENCE_RE.test(o));
  return {
    id: Number(raw.id),
    order_no: Number(raw.order_no) || 0,
    domain: String(raw.domain || '').trim(),
    phase: Number(raw.phase) === 1 ? 1 : 2,
    question_type: type,
    question_text: text,
    options,
    correct_option: correct,
    shuffle_options: shuffleOk
  };
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

// ─────────────────────────────────────────────────────────────────────────────
// Database (in memory, snapshotted to data/runtime_state.json)
// ─────────────────────────────────────────────────────────────────────────────
class Database {
  users: User[] = [];
  questions: Question[] = [];
  sessions: ExamSession[] = [];
  answers: Answer[] = [];
  violations: Violation[] = [];
  globalState: ExamGlobalState = {
    exam_open: false,
    opened_at: null,
    closed_at: null,
    scheduled_start_at: null,
    scheduled_end_at: null,
    timezone_label: 'Asia/Kolkata'
  };
  jwtSecret = '';
  questionsModified = false;

  private answerMap = new Map<string, Answer>();
  private questionMap = new Map<number, Question>();
  private latestSession = new Map<number, ExamSession>();
  private userIdCounter = 1;
  private sessionIdCounter = 1;
  private violationIdCounter = 1;

  private dirty = false;
  private saving = false;
  private candidateHash = '';

  constructor() {
    this.init();
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  init() {
    const candidateDefaultPassword = process.env.CANDIDATE_DEFAULT_PASSWORD || 'dsirt@123';
    this.candidateHash = bcrypt.hashSync(candidateDefaultPassword, 8);

    const restored = this.loadState();
    if (!restored) {
      this.seedUsers();
    }
    this.syncAdminFromEnv();
    this.syncCandidatesFromFile();

    if (!this.questionsModified || this.questions.length === 0) {
      this.loadQuestionBank();
    }
    this.rebuildIndexes();

    if (!this.jwtSecret) {
      this.jwtSecret = process.env.EXAM_SECRET_KEY || crypto.randomBytes(32).toString('hex');
    }
    if (process.env.EXAM_SECRET_KEY) this.jwtSecret = process.env.EXAM_SECRET_KEY;

    this.markDirty();
    this.saveNowSync();

    // Periodic snapshot (only writes when something changed)
    setInterval(() => this.flush(), 2000).unref();
  }

  private seedUsers() {
    const setterHash = bcrypt.hashSync(process.env.SETTER_PASSWORD || 'Setter@12345', 8);
    this.users.push({
      id: this.userIdCounter++,
      email: 'setter@examportal.edu',
      full_name: 'Exam Committee Setter',
      hashed_password: setterHash,
      role: 'setter',
      approval_status: 'approved',
      must_reset_password: false,
      is_active: true,
      token_version: 1
    });
    this.users.push({
      id: this.userIdCounter++,
      roll_no: makeRollNo(0),
      email: 'candidate@examportal.edu',
      full_name: 'Priya Sharma (Demo)',
      hashed_password: this.candidateHash,
      role: 'candidate',
      approval_status: 'approved',
      must_reset_password: false,
      is_active: true,
      token_version: 1
    });
  }

  /** The admin account always follows ADMIN_EMAIL / ADMIN_PASSWORD from .env */
  private syncAdminFromEnv() {
    const adminEmail = (process.env.ADMIN_EMAIL || 'admin@examportal.edu').trim().toLowerCase();
    const adminPassword = process.env.ADMIN_PASSWORD || 'Admin@123';
    let admin = this.users.find(u => u.role === 'admin');
    if (!admin) {
      admin = {
        id: this.userIdCounter++,
        email: adminEmail,
        full_name: 'System Administrator',
        hashed_password: '',
        role: 'admin',
        approval_status: 'approved',
        must_reset_password: false,
        is_active: true,
        token_version: 1
      };
      this.users.unshift(admin);
    }
    admin.email = adminEmail;
    admin.is_active = true;
    if (!admin.hashed_password || !bcrypt.compareSync(adminPassword, admin.hashed_password)) {
      admin.hashed_password = bcrypt.hashSync(adminPassword, 10);
    }
  }

  private syncCandidatesFromFile() {
    try {
      const candsPath = path.join(DATA_DIR, 'candidates.json');
      if (!fs.existsSync(candsPath)) return;
      const cands: Array<{ full_name: string; email: string; domain?: string }> = JSON.parse(fs.readFileSync(candsPath, 'utf-8'));
      let added = 0;
      cands.forEach((c, idx) => {
        if (!c || !c.email) return;
        const rollNo = makeRollNo(idx + 1);
        const existing = this.findUserByEmail(c.email);
        if (!existing) {
          this.users.push({
            id: this.userIdCounter++,
            roll_no: rollNo,
            registered_domain: c.domain || undefined,
            email: c.email.trim().toLowerCase(),
            full_name: (c.full_name || c.email).trim(),
            hashed_password: this.candidateHash,
            role: 'candidate',
            approval_status: 'approved',
            must_reset_password: false,
            is_active: true,
            token_version: 1
          });
          added++;
        } else if (existing.role === 'candidate') {
          existing.roll_no = existing.roll_no || rollNo;
          if (c.domain) existing.registered_domain = c.domain;
        }
      });
      console.log(`[db] candidates.json: ${cands.length} listed, ${added} new accounts created.`);
    } catch (e) {
      console.error('[db] Error loading data/candidates.json', e);
    }
  }

  private loadQuestionBank() {
    let raw: any[] = [];
    try {
      const qPath = path.join(DATA_DIR, 'master_questions.json');
      if (fs.existsSync(qPath)) raw = JSON.parse(fs.readFileSync(qPath, 'utf-8'));
    } catch (e) {
      console.error('[db] master_questions.json is not valid JSON', e);
    }
    if (!raw.length) raw = loadAllQuestionsFromDisk(process.cwd());

    const clean: Question[] = [];
    const seen = new Set<number>();
    let rejected = 0;
    for (const r of raw) {
      const q = sanitizeQuestion(r);
      if (!q || !Number.isFinite(q.id) || seen.has(q.id)) {
        rejected++;
        console.warn('[db] Skipping broken question:', JSON.stringify(r).slice(0, 160));
        continue;
      }
      seen.add(q.id);
      clean.push(q);
    }
    this.questions = clean;
    this.questionsModified = false;
    const perDomain: Record<string, number> = {};
    clean.forEach(q => { perDomain[q.domain] = (perDomain[q.domain] || 0) + 1; });
    console.log(`[db] Question bank: ${clean.length} questions loaded, ${rejected} rejected.`, perDomain);
  }

  rebuildIndexes() {
    this.answerMap.clear();
    for (const a of this.answers) this.answerMap.set(`${a.session_id}:${a.question_id}`, a);
    this.questionMap.clear();
    for (const q of this.questions) this.questionMap.set(q.id, q);
    this.latestSession.clear();
    for (const s of this.sessions) this.latestSession.set(s.user_id, s); // sessions are in creation order
    this.userIdCounter = Math.max(this.userIdCounter, ...this.users.map(u => u.id + 1), 1);
    this.sessionIdCounter = Math.max(this.sessionIdCounter, ...this.sessions.map(s => s.id + 1), 1);
    this.violationIdCounter = Math.max(this.violationIdCounter, ...this.violations.map(v => v.id + 1), 1);
  }

  // ── Persistence ─────────────────────────────────────────────────────────
  markDirty() {
    this.dirty = true;
  }

  private snapshot(): string {
    return JSON.stringify({
      version: STATE_VERSION,
      saved_at: new Date().toISOString(),
      jwt_secret: this.jwtSecret,
      users: this.users,
      sessions: this.sessions,
      answers: this.answers,
      violations: this.violations,
      globalState: this.globalState,
      questionsModified: this.questionsModified,
      questions: this.questionsModified ? this.questions : undefined
    });
  }

  private loadState(): boolean {
    try {
      if (!fs.existsSync(STATE_FILE)) return false;
      const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
      if (!s || s.version !== STATE_VERSION) {
        console.warn('[db] runtime_state.json is from an older version — starting fresh (old file kept as .old).');
        fs.renameSync(STATE_FILE, STATE_FILE + '.old');
        return false;
      }
      this.users = s.users || [];
      this.sessions = s.sessions || [];
      this.answers = s.answers || [];
      this.violations = s.violations || [];
      this.globalState = { ...this.globalState, ...(s.globalState || {}) };
      this.jwtSecret = s.jwt_secret || '';
      this.questionsModified = !!s.questionsModified;
      if (this.questionsModified && Array.isArray(s.questions)) {
        this.questions = s.questions.map(sanitizeQuestion).filter(Boolean) as Question[];
      }
      console.log(`[db] Restored state from ${s.saved_at}: ${this.users.length} users, ${this.sessions.length} sessions, ${this.answers.length} answers.`);
      return true;
    } catch (e) {
      console.error('[db] Could not read runtime_state.json — starting fresh. The unreadable file is kept as .corrupt', e);
      try { fs.renameSync(STATE_FILE, STATE_FILE + '.corrupt-' + Date.now()); } catch (_) {}
      return false;
    }
  }

  async flush() {
    if (!this.dirty || this.saving) return;
    this.saving = true;
    this.dirty = false;
    const tmp = STATE_FILE + '.tmp';
    try {
      await fs.promises.writeFile(tmp, this.snapshot());
      await fs.promises.rename(tmp, STATE_FILE);
    } catch (e) {
      this.dirty = true;
      console.error('[db] Failed to save state', e);
    } finally {
      this.saving = false;
    }
  }

  saveNowSync() {
    try {
      const tmp = STATE_FILE + '.tmp';
      fs.writeFileSync(tmp, this.snapshot());
      fs.renameSync(tmp, STATE_FILE);
      this.dirty = false;
    } catch (e) {
      console.error('[db] Failed to save state synchronously', e);
    }
  }

  // ── Users ───────────────────────────────────────────────────────────────
  findUserByEmail(email: string): User | undefined {
    const clean = (email || '').trim().toLowerCase();
    return this.users.find(u => u.email.trim().toLowerCase() === clean);
  }

  findUserById(id: number): User | undefined {
    return this.users.find(u => u.id === id);
  }

  createUser(full_name: string, email: string, role: 'candidate' | 'setter'): { user: User; temporary_password: string } {
    const temporary_password = role === 'candidate'
      ? (process.env.CANDIDATE_DEFAULT_PASSWORD || 'dsirt@123')
      : `Setter@${crypto.randomInt(1000, 10000)}`;
    const candCount = this.users.filter(u => u.role === 'candidate').length;
    const user: User = {
      id: this.userIdCounter++,
      roll_no: role === 'candidate' ? makeRollNo(candCount + 1) : undefined,
      email: email.trim().toLowerCase(),
      full_name: full_name.trim(),
      hashed_password: role === 'candidate' ? this.candidateHash : bcrypt.hashSync(temporary_password, 8),
      role,
      approval_status: 'approved',
      must_reset_password: role === 'setter',
      is_active: true,
      token_version: 1
    };
    this.users.push(user);
    this.markDirty();
    return { user, temporary_password };
  }

  // ── Questions ───────────────────────────────────────────────────────────
  getQuestion(id: number): Question | undefined {
    return this.questionMap.get(id);
  }

  addQuestions(qs: Question[]) {
    for (const q of qs) {
      this.questions.push(q);
      this.questionMap.set(q.id, q);
    }
    this.questionsModified = true;
    this.markDirty();
  }

  removeQuestion(id: number): boolean {
    const idx = this.questions.findIndex(q => q.id === id);
    if (idx < 0) return false;
    this.questions.splice(idx, 1);
    this.questionMap.delete(id);
    this.questionsModified = true;
    this.markDirty();
    return true;
  }

  questionsChanged() {
    this.questionMap.clear();
    for (const q of this.questions) this.questionMap.set(q.id, q);
    this.questionsModified = true;
    this.markDirty();
  }

  nextQuestionId(): number {
    let max = 0;
    for (const q of this.questions) if (q.id > max) max = q.id;
    return max + 1;
  }

  private pickQuestions(pool: Question[], count: number): Question[] {
    const valid = pool.filter(q => sanitizeQuestion(q) !== null);
    return shuffle(valid).slice(0, count);
  }

  private optionMapFor(q: Question): number[] {
    const idx = q.options.map((_, i) => i);
    if (q.question_type !== 'mcq') return [];
    return q.shuffle_options === false ? idx : shuffle(idx);
  }

  phase2Pool(domain: string): Question[] {
    let pool = this.questions.filter(q => q.domain === domain && q.phase === 2);
    if (pool.length === 0 && domain === 'Agriculture') {
      pool = this.questions.filter(q => q.domain.startsWith('Agricultural -') && q.phase === 2);
    }
    return pool;
  }

  // ── Sessions ────────────────────────────────────────────────────────────
  getLatestSessionForUser(userId: number): ExamSession | undefined {
    return this.latestSession.get(userId);
  }

  /** Creates the session and starts Paper 1. Throws if the bank is empty. */
  createSession(userId: number): ExamSession {
    const user = this.findUserById(userId);
    if (!user) throw new Error('User not found');

    const p1 = this.pickQuestions(this.questions.filter(q => q.domain === 'Research Methodology' && q.phase === 1), EXAM_PAPER1_COUNT);
    if (p1.length === 0) throw new Error('No Paper 1 questions are available in the question bank.');

    const optionMaps: Record<number, number[]> = {};
    for (const q of p1) optionMaps[q.id] = this.optionMapFor(q);

    const now = Date.now();
    const ends = iso(now + PHASE1_MINUTES * 60_000);
    const session: ExamSession = {
      id: this.sessionIdCounter++,
      user_id: userId,
      status: 'in_progress',
      current_phase: 1,
      domain_selected: false,
      paper2_domain: null,
      question_order: p1.map(q => q.id),
      option_maps: optionMaps,
      p1_count: p1.length,
      current_index: 0,
      strikes: 0,
      started_at: iso(now),
      phase1_started_at: iso(now),
      phase1_ends_at: ends,
      phase1_submitted_at: null,
      phase1_auto_submitted: false,
      phase2_available_at: null,
      phase2_started_at: null,
      phase2_ends_at: null,
      phase2_submitted_at: null,
      phase2_auto_submitted: false,
      submitted_at: null,
      exam_ends_at: ends,
      last_activity: iso(now),
      retake_granted: false,
      paused_remaining_seconds: null,
      paused_status: null
    };
    this.sessions.push(session);
    this.latestSession.set(userId, session);
    this.getOrCreateAnswer(session.id, session.question_order[0]);
    this.markDirty();
    return session;
  }

  /** Starts Paper 2 in the chosen domain. */
  startPhase2(session: ExamSession, domain: string): void {
    const pool = this.phase2Pool(domain);
    const p2 = this.pickQuestions(pool, EXAM_PAPER2_COUNT);
    if (p2.length === 0) throw new Error(`No questions are available for ${domain}.`);
    session.question_order = session.question_order.slice(0, session.p1_count).concat(p2.map(q => q.id));
    for (const q of p2) session.option_maps[q.id] = this.optionMapFor(q);

    const now = Date.now();
    session.domain_selected = true;
    session.paper2_domain = domain;
    session.current_phase = 2;
    session.status = 'in_progress';
    session.phase2_started_at = iso(now);
    session.phase2_ends_at = iso(now + PHASE2_MINUTES * 60_000);
    session.exam_ends_at = session.phase2_ends_at;
    session.current_index = session.p1_count;
    session.last_activity = iso(now);
    this.getOrCreateAnswer(session.id, p2[0].id);
    this.markDirty();
  }

  submitPhase1(session: ExamSession, auto: boolean, atMs = Date.now()) {
    if (session.phase1_submitted_at) return;
    session.phase1_submitted_at = iso(atMs);
    session.phase1_auto_submitted = auto;
    session.phase2_available_at = iso(atMs + BREAK_MINUTES * 60_000);
    session.status = 'on_break';
    session.current_phase = 2;
    session.exam_ends_at = session.phase2_available_at;
    session.last_activity = iso(Date.now());
    this.markDirty();
  }

  submitPhase2(session: ExamSession, auto: boolean, atMs = Date.now()) {
    if (session.phase2_submitted_at) return;
    session.phase2_submitted_at = iso(atMs);
    session.phase2_auto_submitted = auto;
    session.submitted_at = session.phase2_submitted_at;
    session.status = 'completed';
    session.last_activity = iso(Date.now());
    this.markDirty();
  }

  /**
   * Applies the clock to a session: auto-submits a phase whose time is over.
   * Returns true when the session changed.
   */
  applyClock(session: ExamSession, nowMs = Date.now()): boolean {
    if (session.status !== 'in_progress') return false;
    if (session.current_phase === 1 && !session.phase1_submitted_at) {
      const end = Date.parse(session.phase1_ends_at);
      if (nowMs >= end) { this.submitPhase1(session, true, end); return true; }
    } else if (session.domain_selected && !session.phase2_submitted_at && session.phase2_ends_at) {
      const end = Date.parse(session.phase2_ends_at);
      if (nowMs >= end) { this.submitPhase2(session, true, end); return true; }
    }
    return false;
  }

  /** Index range [from, to) of the phase the candidate may currently answer, or null. */
  activeRange(session: ExamSession): { phase: number; from: number; to: number } | null {
    if (session.status !== 'in_progress') return null;
    if (!session.phase1_submitted_at) return { phase: 1, from: 0, to: session.p1_count };
    if (session.domain_selected && !session.phase2_submitted_at) {
      return { phase: 2, from: session.p1_count, to: session.question_order.length };
    }
    return null;
  }

  secondsLeft(session: ExamSession, nowMs = Date.now()): number {
    if (session.status !== 'in_progress') return 0;
    const end = session.current_phase === 1 ? session.phase1_ends_at : session.phase2_ends_at;
    if (!end) return 0;
    return Math.max(0, Math.floor((Date.parse(end) - nowMs) / 1000));
  }

  breakSecondsLeft(session: ExamSession, nowMs = Date.now()): number {
    if (session.status !== 'on_break' || !session.phase2_available_at) return 0;
    return Math.max(0, Math.ceil((Date.parse(session.phase2_available_at) - nowMs) / 1000));
  }

  /** Stop the running clock (disqualify / force end) remembering what was left. */
  pause(session: ExamSession, newStatus: 'disqualified' | 'expired') {
    if (['completed', 'expired', 'disqualified'].includes(session.status)) {
      if (newStatus === 'disqualified') session.status = 'disqualified';
      this.markDirty();
      return;
    }
    session.paused_status = session.status;
    session.paused_remaining_seconds = session.status === 'in_progress' ? this.secondsLeft(session) : null;
    session.status = newStatus;
    session.submitted_at = session.submitted_at || iso(Date.now());
    this.markDirty();
  }

  /** Re-open a stopped session. Gives back the time that was left (at least 5 minutes). */
  resume(session: ExamSession): boolean {
    const back = session.paused_status;
    if (!back) return false;
    const now = Date.now();
    session.status = back;
    session.submitted_at = null;
    session.strikes = 0;
    session.retake_granted = true;
    if (back === 'in_progress') {
      const secs = Math.max(300, session.paused_remaining_seconds || 0);
      const end = iso(now + secs * 1000);
      if (session.current_phase === 1 && !session.phase1_submitted_at) session.phase1_ends_at = end;
      else session.phase2_ends_at = end;
      session.exam_ends_at = end;
    }
    session.paused_status = null;
    session.paused_remaining_seconds = null;
    session.last_activity = iso(now);
    this.markDirty();
    return true;
  }

  /**
   * Clears every exam attempt so the hall can be used again, after writing
   * what is being cleared to data/archive/.
   *
   * Kept: candidate and admin accounts, the question bank, passwords.
   * Cleared: every session, every answer, every violation.
   *
   * The archive is written FIRST and the clear only happens if that write
   * succeeded — a reset that cannot be undone is not one worth offering.
   * Returns the archive path plus what was in it.
   */
  resetAllExams(byUserId: number): {
    archive: string;
    sessions: number;
    answers: number;
    violations: number;
  } {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const dir = path.join(DATA_DIR, 'archive');
    fs.mkdirSync(dir, { recursive: true });
    const archive = path.join(dir, `exam-${stamp}.json`);

    const counts = {
      sessions: this.sessions.length,
      answers: this.answers.length,
      violations: this.violations.length,
    };

    // Results are derived from sessions+answers, so they have to be computed
    // before the clear or they are gone for good.
    const results = this.users
      .filter(u => u.role === 'candidate')
      .map(u => ({ user_id: u.id, roll_no: u.roll_no, result: this.getOverallResult(u.id) }))
      .filter(r => r.result);

    const payload = {
      archived_at: new Date().toISOString(),
      archived_by_user_id: byUserId,
      roll_prefix: ROLL_PREFIX,
      global_state: this.globalState,
      results,
      sessions: this.sessions,
      answers: this.answers,
      violations: this.violations,
    };

    // Write to a temp file and rename, so a half-written archive can never be
    // mistaken for a good one.
    const tmp = archive + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), 'utf-8');
    fs.renameSync(tmp, archive);

    this.sessions = [];
    this.answers = [];
    this.violations = [];
    this.globalState.exam_open = false;
    this.globalState.opened_at = null;
    this.globalState.closed_at = new Date().toISOString();
    this.rebuildIndexes();
    this.saveNowSync();

    return { archive, ...counts };
  }

  removeSessionsForUser(userId: number) {
    const ids = new Set(this.sessions.filter(s => s.user_id === userId).map(s => s.id));
    this.sessions = this.sessions.filter(s => s.user_id !== userId);
    this.answers = this.answers.filter(a => !ids.has(a.session_id));
    this.violations = this.violations.filter(v => !ids.has(v.session_id));
    this.rebuildIndexes();
    this.markDirty();
  }

  answeredCount(session: ExamSession, from = 0, to = session.question_order.length): number {
    let n = 0;
    for (let i = from; i < to; i++) {
      const a = this.answerMap.get(`${session.id}:${session.question_order[i]}`);
      if (a && (a.selected_option !== null || (a.written_text && a.written_text.trim()))) n++;
    }
    return n;
  }

  // ── Answers ─────────────────────────────────────────────────────────────
  getOrCreateAnswer(sessionId: number, questionId: number): Answer {
    const key = `${sessionId}:${questionId}`;
    let ans = this.answerMap.get(key);
    if (!ans) {
      ans = {
        session_id: sessionId,
        question_id: questionId,
        selected_option: null,
        written_text: null,
        marked_for_review: false,
        visited: true,
        updated_at: new Date().toISOString()
      };
      this.answers.push(ans);
      this.answerMap.set(key, ans);
      this.markDirty();
    } else if (!ans.visited) {
      ans.visited = true;
      this.markDirty();
    }
    return ans;
  }

  getAnswer(sessionId: number, questionId: number): Answer | undefined {
    return this.answerMap.get(`${sessionId}:${questionId}`);
  }

  saveAnswer(
    sessionId: number,
    questionId: number,
    canonicalOption: number | null | undefined,
    writtenText: string | null | undefined,
    markedForReview: boolean | undefined
  ): Answer {
    const ans = this.getOrCreateAnswer(sessionId, questionId);
    if (canonicalOption !== undefined) ans.selected_option = canonicalOption;
    if (writtenText !== undefined) ans.written_text = writtenText;
    if (markedForReview !== undefined) ans.marked_for_review = !!markedForReview;
    ans.updated_at = new Date().toISOString();
    this.markDirty();
    return ans;
  }

  // ── Violations ──────────────────────────────────────────────────────────
  recordViolation(sessionId: number, type: string): Violation {
    const session = this.sessions.find(s => s.id === sessionId);
    if (!session) throw new Error('Session not found');
    session.strikes += 1; // warnings only — never auto-submits
    const v: Violation = {
      id: this.violationIdCounter++,
      session_id: sessionId,
      violation_type: type,
      strike_number: session.strikes,
      created_at: new Date().toISOString()
    };
    this.violations.push(v);
    this.markDirty();
    return v;
  }

  // ── Results ─────────────────────────────────────────────────────────────
  calculatePhaseResult(session: ExamSession, phase: number): PhaseResult {
    const p1Count = session.p1_count ?? EXAM_PAPER1_COUNT;
    const qIds = phase === 1 ? session.question_order.slice(0, p1Count) : session.question_order.slice(p1Count);
    const domain = phase === 1 ? 'Research Methodology' : (session.paper2_domain || 'Domain Specific');

    let attempted = 0, correct = 0, incorrect = 0, unanswered = 0;
    const details: CandidateResultDetail[] = [];

    qIds.forEach((qId, idx) => {
      const q = this.getQuestion(qId);
      const ans = this.getAnswer(session.id, qId);
      const hasAnswer = !!ans && ans.selected_option !== null && ans.selected_option !== undefined;
      const isCorrect = hasAnswer && !!q && ans!.selected_option === q.correct_option;
      if (hasAnswer) { attempted++; if (isCorrect) correct++; else incorrect++; } else unanswered++;
      if (q) {
        details.push({
          order_no: (phase === 1 ? 0 : p1Count) + idx + 1,
          question_id: q.id,
          question_text: q.question_text,
          selected_option: ans ? ans.selected_option : null,
          correct_option: q.correct_option,
          is_correct: isCorrect,
          is_answered: hasAnswer,
          candidate_answer_text: hasAnswer ? (q.options[ans!.selected_option as number] ?? null) : null,
          correct_answer_text: q.options[q.correct_option] ?? null,
          options: q.options
        });
      }
    });

    const totalQuestions = qIds.length;
    return {
      phase,
      domain,
      total_questions: totalQuestions,
      attempted,
      correct,
      incorrect,
      unanswered,
      score: correct,
      max_score: totalQuestions,
      accuracy: totalQuestions > 0 ? parseFloat(((correct / totalQuestions) * 100).toFixed(2)) : 0,
      submitted_at: phase === 1 ? session.phase1_submitted_at : session.phase2_submitted_at,
      question_detail: details
    };
  }

  getOverallResult(userId: number) {
    const user = this.findUserById(userId);
    if (!user) return null;
    const session = this.getLatestSessionForUser(userId);
    if (!session) return null;

    const p1 = this.calculatePhaseResult(session, 1);
    const p2 = session.domain_selected ? this.calculatePhaseResult(session, 2) : null;

    const total_questions = p1.total_questions + (p2 ? p2.total_questions : 0);
    const total_attempted = p1.attempted + (p2 ? p2.attempted : 0);
    const total_correct = p1.correct + (p2 ? p2.correct : 0);
    const total_incorrect = p1.incorrect + (p2 ? p2.incorrect : 0);
    const total_unanswered = p1.unanswered + (p2 ? p2.unanswered : 0);
    const total_score = p1.score + (p2 ? p2.score : 0);
    const total_max_score = p1.max_score + (p2 ? p2.max_score : 0);
    const overall_accuracy = total_max_score > 0 ? parseFloat(((total_correct / total_max_score) * 100).toFixed(2)) : 0;

    const violations = this.violations
      .filter(v => v.session_id === session.id)
      .map(v => ({ id: v.id, violation_type: v.violation_type, strike_number: v.strike_number, created_at: v.created_at }));

    return {
      user_id: user.id,
      full_name: user.full_name,
      email: user.email,
      session_id: session.id,
      session_status: session.status,
      started_at: session.started_at,
      submitted_at: session.submitted_at,
      domain: session.paper2_domain,
      phase1: p1,
      phase2: p2,
      total_questions,
      total_attempted,
      total_correct,
      total_incorrect,
      total_unanswered,
      total_score,
      total_max_score,
      overall_accuracy,
      violations
    };
  }
}

export const db = new Database();
