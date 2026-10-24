# Question bank — corrections made and items for setter review

File: `data/master_questions.json` (550 questions, 50 per domain).
All 550 were checked: every question has text, 4 non-empty options and a valid answer key.
(The CSVs in `questions files/` are *not* used — several are damaged: the Tamil CSV lost
its Tamil letters (`????`), the English CSV is actually a Word file, and Agronomy is missing
questions 1 and 5. The JSON bank is complete and correct.)

## A. Wrong answer keys — FIXED

| ID | Domain | Question | Was | Now |
|----|--------|----------|-----|-----|
| 100 | Mathematics | A PDE is called linear if | "constant coefficients only" | **dependent variable and its derivatives occur linearly** |
| 320 | Entomology | Neurons conducting impulses from sense organs to CNS | Efferent | **Afferent** |
| 325 | Entomology | True jaws / primary jaws | Labrum | **Mandibles** |
| 331 | Entomology | Male and female gonads in one organism | Gynandromorph | **Hermaphrodite** |
| 372 | Agronomy | 120 kg N needed, 100 kg DAP applied — extra N | 82 kg | **102 kg** (DAP = 18% N) |
| 379 | Agronomy | 120 N, 100 kg DAP + 50 kg MOP — extra N | 82 kg | **102 kg** |
| 453 | Commerce | Long-term source of finance | Bank Overdraft | **Equity Shares** |
| 454 | Commerce | First function of management | Controlling | **Planning** |
| 455 | Commerce | Current ratio, CA ₹6,00,000 / CL ₹3,00,000 | 1 : 1 | **2 : 1** |
| 456 | Commerce | Henri Fayol proposed | 10 principles | **14 principles** |
| 459 | Commerce | Money market instrument | Debenture | **Treasury Bill** |

## B. Broken text / options — FIXED

| ID | Problem | Fix |
|----|---------|-----|
| 51 | Option A read `cl(A)=A∪A` (prime lost) | `cl(A)=A∪A′` |
| 58 | `[a,b][a,b]` duplicated | `[a,b]` |
| 71 | integral missing `dz` | added |
| 73 | Options B `1/((b-a)^2)` and C `1/((a-b)^2)` are the **same value** — two correct answers | C changed to `−1/((b−a)^2)` |
| 75 | `ff` typo in options | `f` |
| 209 | Option B cut off ("…at individual") | "…at individual-cell resolution" |
| 273 | "Ralstonia solanacearu" | "solanacearum" |
| 373 | Said "For Question 22…" — meaningless because question order is shuffled | Rewritten to be self-contained |
| 455 | Rupee sign showed as `?6,00,000` | `₹6,00,000` |
| 473 | `""caveat emptor""` | `"caveat emptor"` |

## C. Options that refer to other options — option order now kept fixed

13 questions contain options like "Both A & B", "All the above", "None of the above".
Options are shuffled per candidate, which made "Both A & B" point at the wrong letters.
These 13 questions now keep their original option order (all others are still shuffled).

## D. Please review (NOT changed — needs a subject expert's decision)

| ID | Domain | Issue |
|----|--------|-------|
| 62 & 76 | Mathematics | Same question twice ("monotone increasing sequence bounded above") — every Maths candidate sees it twice. Replace one. |
| 314 & 322 | Entomology | Same question twice ("eggs of head louse → Nit"). Replace one. |
| 315 (= 532) | Entomology | "Intima is the lining of ___" key is **Mesenteron**, but the mesenteron (midgut) is the part **without** a cuticular intima; oesophagus, trachea and air sacs all have one. Question needs rewording. |
| 307 | Entomology | Chemical neurotransmitter: A Acetylcholine (key) vs D "Both A & B" (dopamine is also one). |
| 312 | Entomology | Pupa of dipteran fly: key "puparium"; many texts say "coarctate". |
| 323 | Entomology | "Incomplete metamorphosis present in" — cockroach, dragonfly and grasshopper are all correct. |
| 330 | Entomology | "Reflex bleeding present in" — key Aphids; classic examples are ladybird/blister beetles, grasshoppers. |
| 350 | Entomology | BPH resurgence insecticide: key Quinalphos vs "All the above". |

Note: the "Agriculture (General)" paper is built from Agronomy, Entomology and Pathology
questions, so fixes/issues above also apply there (IDs 501–550).
