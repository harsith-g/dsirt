import fs from 'fs';
import path from 'path';
import { Question } from './types.js';

export interface ParseReport {
  questions: Question[];
  errors: string[];
}

/** Backwards-compatible wrapper: returns only the questions that parsed cleanly. */
export function parseQuestionFile(content: string, domain: string, phase: number, startId: number): Question[] {
  return parseQuestionFileWithReport(content, domain, phase, startId).questions;
}

export function parseQuestionFileWithReport(content: string, domain: string, phase: number, startId: number): ParseReport {
  const lines = content
    .split(/\r?\n/)
    .map(line => {
      let l = line.trim();
      if (l.startsWith('"') && l.endsWith('"')) {
        l = l.slice(1, -1).trim();
      }
      return l;
    })
    .filter(line => line.length > 0);

  // Check if it's a standard CSV table with headers like Question,Option A...
  const firstLine = lines[0] || '';
  const fl = firstLine.toLowerCase().replace(/_/g, ' ');
  if (fl.includes('option a') && fl.includes('question')) {
    return parseTableCsv(lines, domain, phase, startId);
  }

  const questions: Array<{
    parsed_no: number;
    question_text: string;
    options: string[];
  }> = [];

  let currentQ: { parsed_no: number; question_text: string; options: string[] } | null = null;
  const answers: Record<number, number> = {};
  let isAnswerKey = false;

  for (const text of lines) {
    if (text.toUpperCase().includes('ANSWER KEY') || text.toUpperCase().startsWith('ANSWERS')) {
      isAnswerKey = true;
      continue;
    }

    if (isAnswerKey) {
      const matches = text.matchAll(/(\d+)[\.\s\:\-]+([A-Da-d])/g);
      for (const m of matches) {
        const qNum = parseInt(m[1], 10);
        const letter = m[2].toUpperCase();
        answers[qNum] = letter.charCodeAt(0) - 'A'.charCodeAt(0);
      }
      continue;
    }

    // Check for question header like "1. A TCP connection..."
    const qMatch = text.match(/^(\d+)[\.\)]\s*(.+)/);
    if (qMatch) {
      if (currentQ) {
        questions.push(currentQ);
      }
      currentQ = {
        parsed_no: parseInt(qMatch[1], 10),
        question_text: qMatch[2].trim(),
        options: []
      };
      continue;
    }

    // Check for option like "A. Flow control..." or "A) Flow..." or "(A) Flow..."
    const optMatch = text.match(/^(?:[A-Da-dஅஆஇஈ][\.\)]|\([A-Da-dஅஆஇஈ]\))\s*(.+)/);
    if (optMatch) {
      if (!currentQ) {
        currentQ = {
          parsed_no: questions.length + 1,
          question_text: 'Question',
          options: []
        };
      }
      currentQ.options.push(optMatch[1].trim());
      continue;
    }

    // Ignore known institutional headers and titles
    const upper = text.toUpperCase();
    if (
      upper.includes('DHANALAKSHMI SRINIVASAN') ||
      upper.includes('PERAMBALUR') ||
      upper.includes('ENTRANCE EXAMINATION') ||
      upper.includes('TOTAL QUESTIONS') ||
      upper.includes('INSTRUCTIONS:') ||
      upper === 'PAPER- 2' ||
      upper === 'MATHEMATICS' ||
      upper === 'ENTOMOLOGY'
    ) {
      continue;
    }

    // If it's extra text:
    if (!currentQ || currentQ.options.length > 0) {
      // If we already have options for currentQ, a new unnumbered line could be a new question
      if (currentQ) {
        questions.push(currentQ);
      }
      currentQ = {
        parsed_no: questions.length + 1,
        question_text: text,
        options: []
      };
    } else {
      // Continuation of current question text
      currentQ.question_text += ' ' + text;
    }
  }

  if (currentQ) {
    questions.push(currentQ);
  }

  const result: Question[] = [];
  const errors: string[] = [];
  let idCounter = startId;

  questions.forEach((q) => {
    const correctOpt = answers[q.parsed_no];
    if (q.options.length < 2) {
      errors.push(`Q${q.parsed_no}: fewer than 2 options found — skipped`);
      return;
    }
    if (correctOpt === undefined || correctOpt < 0 || correctOpt >= q.options.length) {
      errors.push(`Q${q.parsed_no}: no answer key found — skipped (add an "ANSWER KEY" section, e.g. "${q.parsed_no}. B")`);
      return;
    }
    result.push({
      id: idCounter++,
      order_no: result.length + 1,
      domain,
      phase,
      question_type: 'mcq',
      question_text: q.question_text,
      options: q.options,
      correct_option: correctOpt
    });
  });

  return { questions: result, errors };
}

function parseTableCsv(lines: string[], domain: string, phase: number, startId: number): ParseReport {
  const header = splitCsvLine(lines[0]).map(h => h.trim().toLowerCase().replace(/_/g, ' '));
  const qIdx = header.findIndex(h => h.includes('question') && !h.includes('type'));
  const aIdx = header.findIndex(h => h.includes('option a'));
  const bIdx = header.findIndex(h => h.includes('option b'));
  const cIdx = header.findIndex(h => h.includes('option c'));
  const dIdx = header.findIndex(h => h.includes('option d'));
  const ansIdx = header.findIndex(h => h.includes('correct') || h.includes('answer'));
  const domIdx = header.findIndex(h => h === 'domain');
  const phaseIdx = header.findIndex(h => h === 'phase');

  const result: Question[] = [];
  const errors: string[] = [];
  let idCounter = startId;

  for (let i = 1; i < lines.length; i++) {
    const row = splitCsvLine(lines[i]);
    const qText = (row[qIdx] || '').trim();
    if (!qText) continue;
    const options = [aIdx, bIdx, cIdx, dIdx].filter(k => k >= 0).map(k => (row[k] || '').trim()).filter(o => o.length > 0);
    if (options.length < 2) { errors.push(`Row ${i + 1}: fewer than 2 options — skipped`); continue; }

    const val = ansIdx >= 0 ? (row[ansIdx] || '').trim().toUpperCase() : '';
    const map: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, '1': 0, '2': 1, '3': 2, '4': 3 };
    let correctOpt = map[val];
    if (correctOpt === undefined && /^[0-3]$/.test(val) && ansIdx >= 0 && header[ansIdx].includes('index')) correctOpt = Number(val);
    if (correctOpt === undefined || correctOpt >= options.length) { errors.push(`Row ${i + 1}: missing/invalid correct option "${val}" — skipped`); continue; }

    const rowDomain = domIdx >= 0 && row[domIdx] ? row[domIdx].trim() : domain;
    const rowPhase = phaseIdx >= 0 && row[phaseIdx] ? (Number(row[phaseIdx]) === 1 ? 1 : 2) : (rowDomain === 'Research Methodology' ? 1 : phase);
    result.push({
      id: idCounter++,
      order_no: result.length + 1,
      domain: rowDomain,
      phase: rowPhase,
      question_type: 'mcq',
      question_text: qText,
      options,
      correct_option: correctOpt
    });
  }

  return { questions: result, errors };
}

function splitCsvLine(line: string): string[] {
  const result: string[] = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') { current += '"'; i++; continue; }
      inQuotes = !inQuotes;
    } else if (char === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  result.push(current.trim());
  return result;
}

export function loadAllQuestionsFromDisk(baseDir: string): Question[] {
  const questionsDir = path.join(baseDir, 'questions files');
  const allQuestions: Question[] = [];
  let currentId = 1;

  const fileMappings = [
    { file: 'DSU_Research_Methodology_Set_II-_1_.csv', domain: 'Research Methodology', phase: 1 },
    { file: 'COMPUTER-SCIENCE-ENTRANCE-TEST-QUESTIONS-_1_.csv', domain: 'Computer Science', phase: 2 },
    { file: 'MATHS.csv', domain: 'Mathematics', phase: 2 },
    { file: 'LIF-SCIENCE.csv', domain: 'Bio-Science', phase: 2 },
    { file: 'AGRONOMY-Final.csv', domain: 'Agricultural - Agronomy', phase: 2 },
    { file: 'entomology.csv', domain: 'Agricultural - Entomology', phase: 2 },
    { file: 'Pathology.csv', domain: 'Agricultural - Pathology', phase: 2 },
    { file: 'PhD- Entrance  Eng QP.csv', domain: 'English', phase: 2 },
    { file: 'PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.csv', domain: 'Commerce & Management Studies', phase: 2 },
    { file: 'Ph.D-ENTRANCE-EXAM-TAMILTAMILQuestion-_1_.csv', domain: 'Tamil', phase: 2 }
  ];

  for (const mapping of fileMappings) {
    const filePath = path.join(questionsDir, mapping.file);
    if (fs.existsSync(filePath)) {
      try {
        const content = fs.readFileSync(filePath, 'utf-8');
        const parsed = parseQuestionFile(content, mapping.domain, mapping.phase, currentId);
        allQuestions.push(...parsed);
        currentId += parsed.length;
      } catch (err) {
        console.error(`Error parsing ${mapping.file}:`, err);
      }
    }
  }

  return allQuestions;
}
