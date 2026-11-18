# NeuroGate: OSF pre-registration draft

Fill the bracketed fields before submitting to OSF. Do not change hypotheses or the analysis plan after data collection starts.

## 1. Study information

**Title:** Does EEG-timed scheduling of AI-agent approval requests improve human detection of bad agent actions?

**Research questions.** Does releasing agent approval requests when the reviewer's measured workload is low raise the rate at which reviewers reject planted bad actions? Does EEG add anything over a subtask-boundary scheduler that uses no EEG?

**Hypotheses**

- **H1.** NeuroGate (EEG + boundary) gives higher d′ than Immediate release, with a false-alarm rate no higher than Immediate's (non-inferiority margin: +0.03).
- **H2.** NeuroGate gives higher d′ than Boundary-only. *Primary confirmatory test.*
- **H3.** Primary-task score and NASA-TLX under NeuroGate are no worse than under Immediate (non-inferiority margins: −5% score, +5 TLX points).
- **H4.** Across all conditions, EEG load at decision time predicts missed bad actions (positive coefficient).

## 2. Design

- Within-subject, 5 conditions: Immediate, Fixed batch (10 min), Boundary-only, Modelled-fatigue budget, NeuroGate.
- Order counterbalanced with a 5×5 Latin square (balanced for first-order carryover with a Williams design when N is a multiple of 10).
- Each condition: about 16 minutes of the primary task (spreadsheet reconciliation + email replies). A seeded stream of agent approval requests runs alongside it, with 12% planted bad actions: wrong recipient, inflated amount, injection-triggered exfiltration, or wrong folder deleted.
- The same seed's structure is used in every condition, with surface details (names, amounts) permuted so participants can't learn specific items.
- Blinding: participants are told only that "the timing of requests may differ between blocks".

## 3. Sampling

- **N = 24** (+ replacements for exclusions), plus a separate 6-person pilot whose data is excluded from the confirmatory analysis.
- **Justification:** the simulation (`simulate.py`) and the pilot variance estimate. Target dz = 0.6 for H2, α = .05 one-sided, power .80, which needs N ≈ 19. 24 gives margin for exclusions.
- **Stopping rule:** stop at 24 included participants.

## 4. Variables

- **Manipulated:** scheduling policy (5 levels).
- **Measured:** each decision (approve/reject), ground truth (bad/good), review latency, agent waiting time, EEG load z at show and at decision, primary-task score, NASA-TLX per block, per-user decoder AUC on held-out calibration blocks.
- **Derived:** hit rate, false-alarm rate, d′ and criterion c (log-linear correction).

## 5. Analysis plan

- **H1/H2:** paired difference in per-participant d′. Linear mixed model `dprime ~ policy + (1|participant)`; planned contrasts NeuroGate−Immediate and NeuroGate−Boundary. Holm correction across the two contrasts. Bootstrap 95% CIs reported alongside.
- **H3:** two one-sided tests against the stated margins.
- **H4:** `glmer(missed ~ load_at_decision + policy + (1|participant) + (1|request), family=binomial)`, run on bad-action trials only.
- **Exclusions (decided in advance):**
  - decoder AUC < 0.55 on the held-out calibration block (NeuroGate condition is then uninformative; reported separately);
  - more than 30% of EEG windows rejected for artifacts;
  - failed attention checks in 2+ blocks.
- **Reported regardless of outcome:**
  - fraction of requests that were schedulable (not deadline-forced);
  - EMG check: correlation of load z with 20–45 Hz frontal power.

## 6. Other

- **Ethics approval:** [institution, protocol number].
- **Data handling:** raw EEG stays on the acquisition machine, and only pseudonymised band-power features plus decisions are shared publicly.
- **Exploratory, not confirmatory:**
  - a U-shaped load effect (underload);
  - review-latency variance as a fatigue signal;
  - the consumer-device replication.
