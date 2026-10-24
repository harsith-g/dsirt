export type UserRole = 'admin' | 'candidate' | 'setter';

export interface User {
  id: number;
  roll_no?: string;
  email: string;
  full_name: string;
  hashed_password: string;
  role: UserRole;
  approval_status: string;
  must_reset_password: boolean;
  is_active: boolean;
  token_version: number;
  registered_domain?: string;
}

export type QuestionType = 'mcq' | 'written';

export interface Question {
  id: number;
  order_no: number;
  domain: string;
  phase: number;
  question_type: QuestionType;
  question_text: string;
  options: string[];
  correct_option: number; // 0-indexed: 0=A, 1=B, 2=C, 3=D
  /** false for questions whose options refer to each other ("Both A & B", "All the above") */
  shuffle_options?: boolean;
}

/**
 * Session lifecycle:
 *   in_progress (paper 1 running)
 *     -> on_break   (paper 1 submitted or its time ran out; break timer running)
 *     -> in_progress (paper 2 running, after the break + domain selected)
 *     -> completed  (paper 2 submitted or its time ran out)
 *   Any state -> disqualified (admin) / expired (admin "End exam for all")
 */
export type ExamSessionStatus = 'not_started' | 'in_progress' | 'on_break' | 'completed' | 'expired' | 'disqualified';

export interface ExamSession {
  id: number;
  user_id: number;
  status: ExamSessionStatus;
  current_phase: number;
  domain_selected: boolean;
  paper2_domain: string | null;
  question_order: number[]; // question IDs: phase-1 block then phase-2 block
  option_maps: Record<number, number[]>; // questionId -> original option indices in display order
  p1_count: number; // how many of question_order belong to paper 1
  current_index: number;
  strikes: number;
  started_at: string;
  phase1_started_at: string;
  phase1_ends_at: string;
  phase1_submitted_at: string | null;
  phase1_auto_submitted: boolean;
  phase2_available_at: string | null;
  phase2_started_at: string | null;
  phase2_ends_at: string | null;
  phase2_submitted_at: string | null;
  phase2_auto_submitted: boolean;
  submitted_at: string | null;
  exam_ends_at: string; // end of the currently running phase (kept for admin screens)
  last_activity: string;
  retake_granted: boolean;
  /** seconds that were left in the running phase when the session was stopped by the admin */
  paused_remaining_seconds: number | null;
  /** status to return to when a retake is granted */
  paused_status: ExamSessionStatus | null;
}

export interface Answer {
  session_id: number;
  question_id: number;
  selected_option: number | null; // canonical option index (0..3)
  written_text: string | null;
  marked_for_review: boolean;
  visited: boolean;
  updated_at: string;
}

export interface Violation {
  id: number;
  session_id: number;
  violation_type: string;
  strike_number: number;
  created_at: string;
}

export interface ExamGlobalState {
  exam_open: boolean;
  opened_at: string | null;
  closed_at: string | null;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  timezone_label: string;
}

export interface CandidateResultDetail {
  order_no: number;
  question_id: number;
  question_text: string;
  selected_option: number | null;
  correct_option: number;
  is_correct: boolean;
  is_answered: boolean;
  candidate_answer_text: string | null;
  correct_answer_text: string | null;
  options: string[];
}

export interface PhaseResult {
  phase: number;
  domain: string;
  total_questions: number;
  attempted: number;
  correct: number;
  incorrect: number;
  unanswered: number;
  score: number;
  max_score: number;
  accuracy: number;
  submitted_at: string | null;
  question_detail?: CandidateResultDetail[];
}
