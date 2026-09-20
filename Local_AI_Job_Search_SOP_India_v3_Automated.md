# Local AI Job Search & Application System — India (v4, Automated)

## What changed from v3

v3 introduced `pipeline.py` as an automated FIND + SCORE loop replacing manual portal-browsing.
v4 hardens and extends that same pipeline without changing the daily routine:

| Change | Detail |
|---|---|
| **Scoring spread** | Old pipeline anchored at score 85 for every APPLY job. New rubric-based prompt forces the model to construct the score from weighted axes, producing real spread (e.g. 92 for a perfect Chennai + target-title + A-company match vs 62 for a borderline role with unknown salary). |
| **Score breakdown in CSV** | Every row now includes a `score_breakdown` column (`base=50 \| location_fit=15 \| ...`) so you can see exactly why a job scored what it did. |
| **Company score column** | A separate Ollama call rates the *hiring company* (0–100, tier A/B/UNKNOWN) independent of role fit. Your `target-companies.csv` is now read at startup and used to anchor the company score for known priority companies. |
| **Job Cache & Reevaluation** | Full job descriptions are now securely cached in `automation/data/jobs_cache.jsonl` upon scraping. This allows you to reevaluate past shortlists using `--reevaluate` after you update your profile or preferences, without needing to re-scrape the boards! |
| **Resume capability** | If a run is interrupted (crash, Ctrl+C, power loss), re-running the same command picks up exactly where it left off. Two safety layers: `seen_jobs.csv` + a secondary check against the master shortlist.csv so a job is never re-scored even if `seen_jobs.csv` missed a write. |
| **Smart HTTP retry** | Transient scrape failures (429 rate-limit, 5xx server errors, network drops) are retried with exponential backoff. Permanent blocks (400, 403, 406) are skipped immediately — no wasted time retrying Glassdoor or Naukri. |
| **Active sites** | LinkedIn + Indeed (primary). Google Jobs aggregation recovers some Naukri-sourced postings. Glassdoor (400/403 blocked), Naukri (406 recaptcha), zip_recruiter (US-only) excluded from default config. |
| **PowerShell-safe output** | All console output is ASCII-safe; stdout is reconfigured to UTF-8 with `errors=replace` so Unicode in job titles never crashes the terminal. jobspy's internal INFO/ERROR logging is suppressed at the root level so PowerShell no longer reports exit-code 1. |

---

# Part 1 — Your target job strategy (unchanged)

1. **Chennai-compatible location**
2. **Good compensation**
3. **Good work culture / employee experience**
4. **Strong career growth**
5. **Good role fit**
6. **Company stability**

Do not let a famous company override the first three. A job is **not** a priority job simply because the company name is famous.

---

# Part 2 — One-time computer setup

## Step 1 — Create the folders

Open **PowerShell** and run:

```powershell
mkdir C:\CareerAI
cd C:\CareerAI
mkdir profile
mkdir applications
mkdir tracker
mkdir inbox
mkdir automation
mkdir tools
```

```text
C:\CareerAI\
├── profile\        (career-profile.md, job-preferences.md, resume.pdf, target-companies.csv)
├── automation\     (pipeline.py, config.json, seen_jobs.csv, shortlist\)
├── tools\          (Resume Matcher lives here)
├── applications\   (tailored resumes per job)
├── tracker\        (applications.csv)
└── inbox\
```

## Step 2 — Install the required tools

Install:

- **Ollama** — runs the local AI model.
- **Git** — downloads the tools.
- **Node.js 22+** — required by Resume Matcher's web interface.
- **Python 3.10+** (3.12+ recommended) — runs the pipeline script and Resume Matcher's backend.
- **uv** — a fast Python package manager Resume Matcher uses. Install from `astral.sh/uv` or `pip install uv`.

Verify each in a **new** PowerShell window:

```powershell
node --version
python --version
git --version
uv --version
```

## Step 3 — Install the Python packages the pipeline needs

```powershell
pip install -U python-jobspy requests json-repair
```

`python-jobspy` is the open-source scraping library that talks to LinkedIn, Indeed, and Google Jobs for you. Python 3.10 or newer is required.

---

# Part 3 — Set up Ollama exactly (unchanged)

## Step 1 — Start Ollama

```powershell
ollama serve
```

Leave this window open. Open a **second** PowerShell window for everything else.

> If Ollama is already running in the system tray, you don't need to run `ollama serve` again.

## Step 2 — Download the model

Use whichever model you have available. The pipeline reads `ollama_model` from `config.json` so you can change it any time without editing `pipeline.py`.

```powershell
# Good balance of speed and quality (12 GB+ RAM):
ollama pull qwen2.5:14b

# If you have more VRAM (27B):
ollama pull qwen3.8:27b

# Smaller machine (8 GB RAM):
ollama pull llama3.1:8b
```

To see which models are installed:

```powershell
ollama list
```

## Step 3 — Test the model

```powershell
ollama run qwen2.5:14b
```

Type `Reply with exactly: LOCAL AI READY`. When it replies correctly, press `Ctrl+C`. Your local AI is ready — every tool in this guide talks to the same running Ollama instance, so you only set this up once.

---

# Part 4 — Create the Career Profile without ambiguity (unchanged — this is the most important step)

This file is the **single source of truth** that both the pipeline script and Resume Matcher will read. Getting it right once means every downstream tool inherits the same discipline.

## Step 1 — Put your resume in the folder

```text
C:\CareerAI\profile\resume.pdf
```

Optionally also add `old-resumes\`, `certificates\`, `projects\`, `performance-reviews\` — only what you're comfortable keeping locally.

## Step 2 — Convert the resume into text

Open `resume.pdf` in Chrome → `Ctrl+A` → `Ctrl+C` → paste into Notepad → save as:

```text
C:\CareerAI\profile\resume-source.txt
```

## Step 3–4 — Ask Ollama to build the profile

```powershell
ollama run qwen2.5:14b
```

Paste your resume text, then paste this instruction:

```text
You are my Career Profile builder.

The resume text above is my source material.

Create a Career Profile using ONLY facts explicitly supported by the source text.

Create these sections:
1. Professional Summary
2. Employers
3. Job Titles
4. Employment Dates
5. Responsibilities
6. Achievements
7. Measurable Results
8. Technologies
9. Technical Skills
10. Leadership Experience
11. Architecture / System Design Experience
12. Cloud / Platform Experience
13. AI / GenAI Experience
14. Domains / Industries
15. Education
16. Certifications
17. Projects
18. Tools / Platforms
19. Keywords that accurately describe my experience

Rules:
- Never invent a fact, number, technology, title, certification, team size or business impact.
- Never convert exposure into expertise, participation into ownership, or mentoring into formal people management.

When something is missing, unclear or contradictory, write NEEDS CONFIRMATION.
At the end create a section called OPEN QUESTIONS listing every fact I should verify manually.

Do not write a polished resume. Do not exaggerate. The result must be a factual
career database, not marketing copy.
```

## Step 5 — Save the answer

Save it as `C:\CareerAI\profile\career-profile.md` (Notepad → Save As → type: All Files, encoding UTF-8 — watch out for an accidental `.md.txt`).

## Step 6 — Verify manually

```text
[ ] Company names, titles, dates, technologies correct
[ ] Leadership claims and numbers correct
[ ] Certifications correct
[ ] No invented achievements
[ ] No unexplained NEEDS CONFIRMATION items
```

If anything is wrong, fix the **source text** and regenerate — never hand-edit the profile into something the source doesn't support.

---

# Part 5 — Create your Chennai-first Job Preferences

Create `C:\CareerAI\profile\job-preferences.md`. This file is no longer just something you paste into a chat — **`pipeline.py` reads it on every run for both role scoring and company scoring**, so keep it accurate.

```text
TARGET LOCATION: Chennai, Tamil Nadu

LOCATION PRIORITY:
1. Chennai
2. Remote India
3. Bengaluru only if I explicitly approve relocation

RELOCATION: Do not assume relocation. Ask before recommending a relocation-only role.

WORK MODEL:
Preferred: Remote or Hybrid
Acceptable: Chennai office
Avoid: Mandatory relocation outside Chennai

SALARY:
Minimum acceptable CTC: [ENTER YOUR MINIMUM]
Target CTC: [ENTER YOUR TARGET]

NOTICE PERIOD: [ENTER YOUR REAL NOTICE PERIOD]
TARGET EXPERIENCE: [ENTER TOTAL YEARS]
TARGET ROLES: [ENTER 5–8 ROLES]

PRIMARY CAREER LANES:
1. [ROLE / LANE 1]
2. [ROLE / LANE 2]
3. [ROLE / LANE 3]

PREFERRED INDUSTRIES: Product software, SaaS, FinTech, Banking/FinTech, Cloud/infrastructure,
Enterprise technology, GCC engineering centres

COMPANIES TO PRIORITIZE:
[List your Priority A companies — pipeline uses this to boost company_score]

COMPANIES / ROLES TO AVOID:
Roles with unclear salary obviously below target
Mandatory relocation away from Chennai
Long-term night-shift roles unless explicitly approved
Third-party staffing when direct employment is available
Unpaid / pay-to-apply opportunities

DECISION RULE: Do not recommend APPLY unless location/remote requirement is
satisfied, compensation appears viable, role is a genuine career fit, and
company-quality signals are acceptable.
```

Replace every bracketed placeholder before you run the pipeline for the first time.

---

# Part 6 — Your Chennai target-company list

Create `C:\CareerAI\profile\target-companies.csv` with columns:

```text
Company,Priority,Chennai,RemoteIndia,ProductOrGCC,CompensationPotential,CultureCheck,OfficialCareersChecked,LinkedInChecked,NaukriChecked,LastChecked,Notes
```

Seed it with your Priority A/B companies. The pipeline now reads this file at startup — Priority A companies automatically get a boosted `company_score` floor of 82, Priority B companies get 70, so they sort higher in the shortlist even when the model underestimates them.

You still review this list weekly by hand (Part 10) — the automation covers job-board search, not "does this specific company have a Chennai office right now," which still needs a human glance at the official careers page.

---

# Part 7 — Install and run the automated FIND + SCORE pipeline

This is the core upgrade. Instead of you searching three portals with the same keywords, one script does it and hands you a ranked shortlist.

## Step 1 — Get the two files

Save these two files into `C:\CareerAI\automation\`:

**`config.json`** (edit the placeholders, then save):

```json
{
  "career_profile_path": "../profile/career-profile.md",
  "job_preferences_path": "../profile/job-preferences.md",
  "target_companies_path": "../profile/target-companies.csv",
  "tracker_path": "../tracker/applications.csv",
  "ollama_model": "qwen2.5:14b",
  "ollama_url": "http://localhost:11434/api/generate",
  "min_score_to_show": 60,
  "max_retries": 2,
  "searches": [
    {
      "search_term": "Senior Architect",
      "location": "Chennai, Tamil Nadu, India",
      "site_name": ["linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India"
    },
    {
      "search_term": "AI Architect",
      "location": "Chennai, Tamil Nadu, India",
      "site_name": ["linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India"
    },
    {
      "search_term": "Vice President Engineering",
      "location": "India",
      "site_name": ["linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India",
      "is_remote": true
    },
    {
      "search_term": "Architect",
      "google_search_term": "Senior Architect jobs in Chennai posted this week",
      "location": "Chennai, Tamil Nadu, India",
      "site_name": ["google"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India"
    }
  ]
}
```

Key `config.json` fields:

| Field | Purpose |
|---|---|
| `ollama_model` | Must match `ollama list` — change without editing pipeline.py |
| `max_retries` | Extra attempts on 429/5xx/network errors (default 2). 4xx blocks are never retried. |
| `min_score_to_show` | Role score threshold for the terminal digest (CSV always has everything) |
| `target_companies_path` | Feeds the company scoring call; Priority A/B rows get a score floor |
| `google_search_term` | Used instead of `search_term` when `site_name` is `["google"]` |

Add one `searches` entry per career lane from Appendix A — this is the automated equivalent of the old "create separate alerts" steps in LinkedIn/Indeed.

### Why Naukri isn't in the automated scrape

Naukri actively fingerprints and reCAPTCHA-blocks scraping traffic — you'll see `status code 406` in the log. This isn't a bug; it's Naukri's anti-bot layer. The pipeline detects 406 as a permanent block and skips it immediately (no retry). **Keep Naukri on its own native alerts instead:**

```text
Naukri → Job Alerts → create one alert per career lane, Location: Chennai
↓
Check the alert emails / app inbox for ~5 minutes as part of your daily routine
```

Google Jobs searches in `config.json` partially recover Naukri-originated postings via aggregation.

**`pipeline.py`** — the script itself. Read the comment at the top once; it explains exactly what it does and doesn't do.

## Step 2 — Test it without evaluating

```powershell
cd C:\CareerAI\automation
python pipeline.py --step scrape
```

This scrapes, de-duplicates, and caches jobs — without calling Ollama. You'll see:
- How many new jobs were found
- The job data gets saved into `jobs_cache.jsonl` and `pending_eval.jsonl` for later evaluation.

## Step 3 — Run it for real

```powershell
python pipeline.py
```

What happens, with zero further input from you:

```text
Reads career-profile.md, job-preferences.md, and target-companies.csv
↓
Scrapes LinkedIn + Indeed + Google Jobs for every search in config.json
(transient 429/5xx errors retried automatically with backoff)
↓
Drops jobs you've already seen or already applied to
↓
Saves new job descriptions to a local cache (jobs_cache.jsonl)
↓
For each new job:
  - Role evaluation: Ollama scores it 0-100 via an explicit weighted rubric
    with a full score_breakdown (not all 85 — real spread across the range)
  - Company evaluation: separate Ollama call scores the hiring company 0-100,
    anchored to your target-companies.csv for known priority companies
↓
Writes to the master shortlist\shortlist.csv, sorted by role score
↓
Prints a terminal digest grouped by APPLY / HOLD / SKIP / CLOSED
```

### Shortlist CSV columns

| Column | What it is |
|---|---|
| `score` | Role fit score 0–100 (rubric-computed, real spread) |
| `company_score` | Company desirability 0–100 (separate call) |
| `company_tier` | A / B / UNKNOWN from target-companies.csv |
| `decision` | APPLY / HOLD / SKIP / CLOSED |
| `score_breakdown` | `base=50 \| location_fit=15 \| role_title_fit=10 \| ...` — full audit trail |
| `top_reasons` | Why the model gave this verdict |
| `red_flags` | Hard blockers detected |
| `missing_information` | What the job posting didn't disclose |
| `company_notes` | One-line rationale for the company score |

### Resume flags

| Flag | What it does |
|---|---|
| `--step scrape` | Scrape + cache only; no Ollama calls |
| `--step evaluate` | Score jobs that were cached by the scrape step |
| `--reevaluate TARGETS` | Rescore jobs based on new preferences (pass 'all' or a list of URLs) |
| `--sync-csv` | Scan shortlists for manual `REEVALUATE` or `CHECK` triggers in the `decision` column |
| `--limit N` | Score only the next N new jobs this run (deprecated, use steps instead) |
| `--date YYYY-MM-DD` | (Deprecated) All data is now written to a single unified shortlist.csv |

### Resuming after interruption

If the script is interrupted (Ctrl+C, crash, power loss), just re-run the same command. Two safety layers ensure no job is re-scored or double-written:
1. `seen_jobs.csv` — written per-job immediately after shortlist write
2. The master shortlist CSV is also read at startup — any URL already in it is skipped even if `seen_jobs.csv` missed the write

### Manual triggers directly from the CSV

You can use the CSV itself to instruct the pipeline to re-check or re-score specific jobs. If you manually edit a job's `decision` value to one of the trigger words below, save the CSV, and run `python pipeline.py --sync-csv`, the pipeline will process those rows and update the file:

| `decision` trigger | What it does when you run `--sync-csv` |
|---|---|
| `REEVALUATE` | Reloads the job's original description from the cache, re-runs the Ollama scoring with your latest preferences, and overwrites the row with a new score and decision (APPLY/HOLD/SKIP). |
| `CHECK` | Performs a live check against the job URL to see if it returns a 404 or contains obvious "job is closed" banners. If closed, the decision is updated to `CLOSED`. If it appears open, it reverts back to `HOLD`. *(Note: Job boards often block automated checks, so this check may falsely assume a blocked job is still OPEN.)* |

## Step 4 — Schedule it to run every morning (optional but recommended)

Windows Task Scheduler → **Create Basic Task** → Trigger: Daily, e.g. 7:00 AM → Action: Start a program:

```text
Program: python
Arguments: C:\CareerAI\automation\pipeline.py
Start in: C:\CareerAI\automation
```

Now your shortlist is waiting for you before your first coffee.

### A few important cautions

- **Rate limits, not stealth.** JobSpy is a scraping library; job boards can rate-limit or temporarily block an IP that scrapes too aggressively. Keep `results_wanted` modest (20–30), run once or twice a day, not in a tight loop.
- **This reads job boards, it doesn't log in as you.** It never touches your Naukri/LinkedIn credentials.
- **Personal use only.** This is you automating your own job search, not building a scraping service for others.

---

# Part 8 — Install Resume Matcher (replaces the old copy-paste tailoring/ATS steps)

Once a job clears your shortlist as `APPLY`, this local web app takes the job description and your master resume and does the tailoring + ATS scoring interactively, using the same local Ollama model.

## Step 1 — Clone it

```powershell
cd C:\CareerAI\tools
git clone https://github.com/srbhr/Resume-Matcher.git
cd Resume-Matcher
```

Prerequisites: Python 3.13+, Node.js 22+, `uv` (all installed in Part 2).

## Step 2 — Start the backend (Terminal 1)

```powershell
cd apps\backend
copy .env.example .env
```

Open `.env` and point the AI provider setting at your local Ollama (the repo's `.env.example` documents the exact variable name — set model to match what you have in `config.json`). Then:

```powershell
uv sync
uv run app
```

## Step 3 — Start the frontend (Terminal 2, new window)

```powershell
cd C:\CareerAI\tools\Resume-Matcher\apps\frontend
npm install
npm run dev
```

Open the local URL it prints (typically `http://localhost:3000`).

## Step 4 — Use it per job

```text
Upload your master resume (PDF/DOCX — export career-profile.md to a clean
Word doc once, or use resume.pdf)
↓
Paste the job description from your shortlist CSV
↓
Review the match score, keyword gaps, and AI-tailored content
↓
Generate the matching cover letter
↓
Export the tailored resume as PDF
```

## Step 5 — Keep the fact-check guardrail (do not skip this)

Resume Matcher optimises for ATS keyword match — it does **not** independently verify that every rewritten line is still literally true against your Career Profile. Before you save the final version, paste the tailored resume back into Ollama with:

```text
FACT CHECK THIS RESUME.

For EVERY bullet:
1. Identify the underlying Career Profile fact.
2. Say DIRECT / REWORDED / INFERRED.
3. Flag any claim that increases my scope or seniority.
4. Flag every number, technology, leadership claim and business-impact claim.

Return: FACT CHECK: PASS or FAIL
If FAIL, list exactly what must be removed or corrected.
```

Only save the resume to `C:\CareerAI\applications\Company_Role_YYYY-MM-DD\resume.pdf` once you get `PASS`.

---

# Part 9 — Track every application (unchanged)

`C:\CareerAI\tracker\applications.csv`, columns:

```text
DateApplied,Company,Role,Source,JobURL,Location,WorkModel,SalaryCTC,FixedPay,
Variable,Stock,NoticeRequirement,MyNoticePeriod,FitScore,CompanyScore,
ResumeVersion,Status,Recruiter,NextAction,NextActionDate,InterviewStage,
Outcome,Notes
```

Statuses: `Saved → Company Review → Shortlisted → Preparing → Applied →
Recruiter Contacted → Recruiter Screen → Interview 1 → Interview 2 → Final
Round → Offer / Rejected / Withdrawn / Closed`.

Copy the `JobURL` straight from the shortlist CSV — this is also the field `pipeline.py` checks against so it never re-scores a job you've already applied to.

---

# Part 10 — Your new daily / weekly / monthly routine

## Every day (10–15 minutes of your time, not 45–60)

```text
5 min  → Check Naukri alert emails/app (the one portal the script can't reach directly)
5–10 min → Open automation\shortlist\shortlist_<today>.csv
           (already scraped and scored overnight, grouped APPLY / HOLD / SKIP / CLOSED)
↓
Scan the CLOSED section first — deprioritise any outreach on those roles
↓
Skim top APPLY rows; read score_breakdown, top_reasons, red_flags
↓
Pick your 1–2 genuinely strong matches (role score AND company score both high)
↓
Resume Matcher: tailor → fact-check → ATS score
↓
YOU review every line, open the official application, YOU click Submit
↓
Add the row to tracker\applications.csv
```

## Every week

```text
Open target-companies.csv → check Priority A companies' official careers pages
directly (the pipeline covers job boards, not every company's own site)
↓
Skim recruiter responses
↓
Adjust config.json searches if a career lane is producing nothing useful
```

## Every month (after ~20 applications)

Paste your `applications.csv` into Ollama with the same analysis prompt as before:

```text
Analyze my application tracker. Use only the recorded data.
1. Which companies respond most often?
2. Which roles produce the most interviews / no response?
3. Which salary levels and locations produce responses?
4. Which skills appear repeatedly in successful jobs but are weak in my profile?
5. Should any company move up/down my priority list, or should I change my
   resume lane? What one change should I make next month?
Do not make conclusions where the dataset is too small.
```

---

# Appendix A — Chennai/AI/architecture search keywords (feed these into `config.json`)

```text
Engineering Manager · Senior Engineering Manager · Software Engineering Manager
Backend Engineering Manager · Engineering Director · Head of Engineering
Senior Architect · Principal Architect · Enterprise Architect · Staff Architect
Solutions Architect · Backend Architect · Distributed Systems Architect
Platform Architect · Cloud Architect · Microservices Architect
AI Architect · GenAI Architect · AI Engineering Manager · AI Platform Architect
Machine Learning Architect · Generative AI Lead · LLM Architect
Vice President Engineering · Senior Vice President Engineering
```

---

# Appendix B — Company research prompt (now partially automated — `company_score` handles a first pass)

The pipeline now produces a `company_score` and `company_notes` for every job automatically. For companies that score high or that you're seriously considering, run a deeper manual check:

```text
Evaluate this company as a potential employer for me.
My priorities: Chennai/remote compatibility, compensation, culture, growth, stability.
Use only the evidence I provide. Do not guess.

Return: COMPANY / CHENNAI PRESENCE (PASS/FAIL/UNKNOWN) / COMPENSATION SIGNAL /
CULTURE SIGNAL / STABILITY SIGNAL / CAREER GROWTH / TOP POSITIVES / TOP RISKS /
QUESTIONS FOR THE RECRUITER / FINAL SCORE OUT OF 100
```

---

# Appendix C — Red flags (unchanged)

```text
Recruiter asks for payment · Training fee before interview
Bank/card details requested too early · Suspicious email domain
No identifiable company website · Unclear employer-of-record
Guaranteed job promise · Pressure to resign before written offer
Request to install unknown software · Long-term unpaid trial work
```

Never pay a normal recruiter to process a normal job application.

---

# Appendix D — Before accepting an offer, confirm in writing

```text
Designation · Work location · Hybrid/remote policy · Fixed pay · Variable pay
Joining bonus · Stock/RSU terms · Notice period · Probation
Employment entity · Payroll entity · Expected joining date
Background-check requirements
```

Do not resign based only on a verbal recruiter statement.

---

# Appendix E — If your salary target is high

```text
Your current compensation → Comparable role/level → Chennai compensation
evidence → Target company compensation → Expected total compensation →
Final salary decision
```

Levels.fyi's Chennai leaderboard is one useful benchmark, but it's built from self-reported submissions and varies a lot by company, role and level: `https://www.levels.fyi/leaderboard/Software-Engineer/Software-Engineer/city/Chennai/`

---

# Appendix F — Privacy notes for this v4 stack

- **Ollama**: your career profile, preferences and every job description are processed by a model running on your own machine — nothing leaves it during scoring.
- **`pipeline.py`**: only talks to (a) the public job-board pages JobSpy fetches and (b) your local Ollama endpoint. It writes plain CSVs to your own disk.
- **Resume Matcher**: designed to run locally against Ollama; if you ever switch its `.env` to a cloud provider (OpenAI/Gemini/etc.) instead of Ollama, that provider will receive the resume/job text you paste in — check its `.env.example` comments before you decide.
- Naukri, LinkedIn, Indeed and employer portals remain online systems. Anything you eventually submit *there* is not "local" just because your AI is local.

---

# Appendix G — Troubleshooting

**`status code 406` (Naukri) or `status code 403` (Glassdoor)** → expected permanent blocks; the pipeline skips these immediately without retry and logs `[FAIL] HTTP 4xx (no retry)`. Use Naukri's native alerts. Glassdoor is excluded from `config.json`.

**`status code 429` or `5xx` on LinkedIn/Indeed** → the pipeline retries automatically with exponential backoff (10s → 20s → 40s for 5xx; 60s → 120s for 429). If it still fails after `max_retries` attempts, that search is skipped for this run and will retry next run.

**`python-jobspy` import error** → `pip install -U python-jobspy` (package name has a hyphen; the Python import is `from jobspy import scrape_jobs`, no hyphen).

**Pipeline returns 0 new jobs every day** → check `automation\seen_jobs.csv`; delete it if you want to reprocess everything from scratch, or it means your searches genuinely aren't finding new postings — widen `hours_old` or add more `searches` entries from Appendix A.

**All jobs are getting the same score (85)** → you're running an old version of `pipeline.py`. The v4 pipeline uses a weighted rubric prompt that forces real score spread. Pull the latest file.

**Resume Matcher backend won't start** → re-check `.env` for a valid AI provider block; re-run `uv sync` inside `apps/backend`.

**Model is too slow** → switch `ollama_model` in `config.json` to `llama3.1:8b` (no other file changes needed). Also match the model in Resume Matcher's `.env`.

**AI is inventing details anywhere in the stack** → don't loosen the prompt; strengthen the Career Profile (Part 4), and re-run the strict fact-check prompt (Part 8, Step 5) before saving any resume.

---

# Final setup checklist

```text
[ ] Ollama installed, model pulled, LOCAL AI READY test passed
[ ] Python 3.10+, Node 22+, Git, uv installed
[ ] python-jobspy and requests installed (pip install -U python-jobspy requests)
[ ] resume.pdf → resume-source.txt → career-profile.md created and verified
[ ] job-preferences.md filled in with real numbers (incl. COMPANIES TO PRIORITIZE)
[ ] target-companies.csv seeded with Priority A/B companies
[ ] automation\config.json edited:
    [ ] ollama_model matches ollama list
    [ ] target_companies_path set
    [ ] searches tailored to your career lanes
[ ] pipeline.py --step scrape tested: shows new jobs (no errors)
[ ] pipeline.py --step evaluate run: verify score spread (not all 85), company_score column present
[ ] (optional) Task Scheduler entry created for daily automatic run
[ ] Resume Matcher cloned, backend + frontend running against local Ollama
[ ] tracker\applications.csv created
```

---

## Current-reference note

Software changes over time — re-check the live repo README when a command or setting differs from this document.

Key references used for this version:

- JobSpy: `https://github.com/speedyapply/JobSpy`
- Resume Matcher: `https://github.com/srbhr/Resume-Matcher`
- Levels.fyi Chennai leaderboard: `https://www.levels.fyi/leaderboard/Software-Engineer/Software-Engineer/city/Chennai/`
- Naukri job alerts (for the "check the official portal too" habit): `https://resume.naukri.com/frequently-asked-questions-faq/how-to-create-free-job-alerts/`

**Final principle, unchanged from v2:** the goal is not maximum applications — it's maximum quality per application, with the tedious 90% now handled by scripts instead of you.
