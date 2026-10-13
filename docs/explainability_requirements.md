# Explainability Requirements

Every recommendation must come with understandable evidence.

## Rules
1. **Score is not probability.** Career scores are compatibility scores. The product must never present them as the probability of getting a job, being hired, or succeeding in a career. UI and API text should say "compatibility" or "match", never "chance" or "likelihood of employment".
2. **Show the evidence.** Each career recommendation lists matched skills, missing skills, and the reason each matters (importance in the career, and observed demand in sampled postings where available).
3. **Cite the data limits.** Demand figures are sample statistics from the acquired job corpus, not a definitive ranking of the Kenyan labour market. Say so wherever they are shown.
4. **Confidence is not calibration.** Any confidence indicator is a deterministic or explainable score, not a calibrated probability, and is labelled accordingly.
5. **Traceability.** Every stored recommendation records `model_version` and creation time so outputs can be reproduced and audited.
6. **Gap reasons.** Every skill gap states why it is prioritized (importance, demand, prerequisites) and links to something actionable (a resource or project) once Phase 6 is available.
7. **No hidden catalogs.** Career profiles, learning resources and projects live in editable reference data, not inside code.

## Example
**Data Analyst — 91% compatibility**
- Strong SQL match
- Strong Excel match
- Strong Power BI match
- SQL appears in a high share of sampled Data Analyst postings
- Potential gap: Statistics

*Compatibility score, not a probability of employment.*

## Required disclaimer text (UI/API)
"Scores show how closely your current skills match each career's typical requirements in our sample of job postings. They do not predict whether you will be hired."
