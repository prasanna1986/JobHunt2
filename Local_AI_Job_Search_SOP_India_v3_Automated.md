# Local AI Job Search & Application System — India (v3, Automated)

## What changed from v2

v2 was a **manual** system: you opened Naukri/LinkedIn/Indeed yourself, copy-pasted job text into an Ollama chat window, and copy-pasted the answer back into Notepad. It worked, but you did almost every step by hand.

v3 keeps the same philosophy (local AI, factual career profile, Chennai-first priorities, **you click Submit**) but replaces the manual steps with three real, open-source tools that do the heavy lifting for you:

| Job | Tool | What it replaces |
|---|---|---|
| Search Naukri + LinkedIn + Indeed at once | **JobSpy** (`speedyapply/JobSpy`) — a Python scraping library | Manually opening three portals and typing the same keywords into each |
| Score every new job against your profile & preferences | **`pipeline.py`** (built for you below, wraps JobSpy + your local Ollama) | Manually pasting each job description into Ollama one at a time |
| Tailor a resume + get an ATS score for a specific job | **Resume Matcher** (`srbhr/Resume-Matcher`) — a local web app | Manually pasting resumes into Ollama and eyeballing keyword fit |
| Track applications | A CSV tracker (kept from v2 — still the simplest reliable option) | — |

Your new daily loop is: **a script finds and scores jobs overnight → you open one ranked shortlist → you tailor the 1–2 you like in a local web app → you personally apply.**

> **What we deliberately did *not* use:** there is a whole category of GitHub projects (AIHawk-style bots, "auto-apply to 1,000 jobs" agents) that drive a real browser and click Submit for you. We're not using one of these, on purpose:
> - Several were built for **LinkedIn Easy Apply**, and LinkedIn has already pushed back on this category of automation (the original AIHawk project shut down its LinkedIn auto-apply feature after platform pressure and is now a different, proprietary product).
> - Mass-applying with a bot produces low-quality, generic applications that recruiters increasingly recognise and filter out — it works against the "fewer, better applications" principle this SOP is built on.
> - Automated form-filling can silently submit wrong answers (CTC, notice period, relocation) with no human check, which is exactly the failure mode this SOP exists to prevent.
>
> Everything below stops at "tailored, fact-checked, ATS-passed resume, ready for you to submit." That boundary is intentional, not a limitation.

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
├── automation\      (pipeline.py, config.json — the FIND+SCORE robot)
├── tools\           (Resume Matcher lives here)
├── applications\    (tailored resumes per job)
├── tracker\         (applications.csv)
└── inbox\
```

## Step 2 — Install the required tools

Install:

- **Ollama** — runs the local AI model.
- **Git** — downloads the tools.
- **Node.js 22+** — required by Resume Matcher's web interface.
- **Python 3.10+** (3.13+ recommended) — runs the pipeline script and Resume Matcher's backend.
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
pip install -U python-jobspy requests
```

`python-jobspy` is the open-source scraping library that talks to Naukri, LinkedIn and Indeed for you. Python 3.10 or newer is required.

---

# Part 3 — Set up Ollama exactly (unchanged)

## Step 1 — Start Ollama

```powershell
ollama serve
```

Leave this window open. Open a **second** PowerShell window for everything else.

> If Ollama is already running in the system tray, you don't need to run `ollama serve` again.

## Step 2 — Download the model

12 GB+ RAM available to Ollama:

```powershell
ollama pull qwen2.5:14b
```

Smaller machine:

```powershell
ollama pull llama3.1:8b
```

## Step 3 — Test the model

```powershell
ollama run qwen2.5:14b
```

Type `Reply with exactly: LOCAL AI READY`. When it replies correctly, press `Ctrl+C`. Your local AI is ready — and every tool in this guide (the pipeline script and Resume Matcher) will talk to this same running Ollama instance, so you only ever set this up once.

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

Create `C:\CareerAI\profile\job-preferences.md`. This file is no longer just something you paste into a chat — **`pipeline.py` reads it on every run**, so keep it accurate.

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

# Part 6 — Your Chennai target-company list (unchanged)

Create `C:\CareerAI\profile\target-companies.csv` with columns:

```text
Company,Priority,Chennai,RemoteIndia,ProductOrGCC,CompensationPotential,CultureCheck,OfficialCareersChecked,LinkedInChecked,NaukriChecked,LastChecked,Notes
```

Seed it with your Priority A/B/C companies (Amazon, Walmart Global Tech, PayPal, Freshworks, Zoho, Wells Fargo, Citi, Qualcomm, Workday, Cisco, and your B/C lists). You still review this list weekly by hand (Part 10) — the automation in Part 7 covers job-board search, not "does this specific company have a Chennai office right now," which still needs a human glance at the official careers page.

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
  "tracker_path": "../tracker/applications.csv",
  "ollama_model": "qwen2.5:14b",
  "ollama_url": "http://localhost:11434/api/generate",
  "min_score_to_show": 60,
  "searches": [
    {
      "search_term": "Engineering Manager",
      "location": "Chennai, Tamil Nadu, India",
      "site_name": ["naukri", "linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India"
    },
    {
      "search_term": "Java Architect",
      "location": "Chennai, Tamil Nadu, India",
      "site_name": ["naukri", "linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India"
    },
    {
      "search_term": "AI Architect",
      "location": "Chennai, Tamil Nadu, India",
      "site_name": ["naukri", "linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India"
    },
    {
      "search_term": "Engineering Manager",
      "location": "India",
      "site_name": ["naukri", "linkedin", "indeed"],
      "results_wanted": 25,
      "hours_old": 72,
      "country_indeed": "India",
      "is_remote": true
    }
  ]
}
```

Add one `searches` entry per career lane from Appendix A — this is the automated equivalent of the old "create separate alerts" steps in LinkedIn/Indeed.

### Why Naukri isn't in the automated scrape

Naukri actively fingerprints and reCAPTCHA-blocks scraping traffic — you'll see `Naukri API response status code 406 - recaptcha required` in the log. This isn't a bug in your setup; it's Naukri's anti-bot layer working as designed. Even paid commercial Naukri-scraping services need rotating **residential proxies** (real Indian home IPs, at real cost) to get past it reliably, and it still isn't guaranteed. That's a lot of complexity and money for a portal that already has a good native alert system.

So `config.json` above deliberately only scrapes `linkedin` and `indeed`. **Keep Naukri on its own native alerts instead** — this is the one place v2's manual setup was actually the right tool:

```text
Naukri → Profile → keep Current location = Chennai, Preferred locations = Chennai
first, salary/notice period accurate
↓
Naukri → Job Alerts → create one alert per career lane (Engineering Manager,
Java Architect, AI Architect, etc.), Location: Chennai
↓
Check the alert emails / app inbox for ~5 minutes as part of your daily routine
```

If you later want Naukri automated too, the only reliable route is paying for a residential-proxy scraping service and passing the proxy list to JobSpy's `proxies` parameter — worth doing only if Naukri consistently surfaces roles the other two boards miss.

**`pipeline.py`** — the script itself (provided as a download alongside this guide). Read the comment at the top once; it explains exactly what it does and doesn't do.

## Step 2 — Test it without using the AI

```powershell
cd C:\CareerAI\automation
python pipeline.py --dry-run
```

This only scrapes and de-duplicates, so you can confirm your searches return real Chennai/remote results before you spend Ollama time scoring them.

## Step 3 — Run it for real

```powershell
python pipeline.py
```

What happens, with zero further input from you:

```text
Reads career-profile.md and job-preferences.md
↓
Scrapes Naukri + LinkedIn + Indeed for every search in config.json
↓
Drops jobs you've already seen or already applied to
↓
Sends each new job to your local Ollama model for scoring
↓
Writes automation\shortlist\shortlist_YYYY-MM-DD.csv, ranked highest score first
↓
Prints the jobs that cleared your score bar straight to the terminal
```

Each row already carries the same verdict shape as the old manual Part 14 prompt: `decision` (APPLY/HOLD/SKIP), `score`, `location_fit`, `compensation`, `role_fit`, `top_reasons`, `red_flags`. Open the CSV in Excel and sort/filter as you like.

## Step 4 — Schedule it to run every morning (optional but recommended)

Windows Task Scheduler → **Create Basic Task** → Trigger: Daily, e.g. 7:00 AM → Action: Start a program:

```text
Program: python
Arguments: C:\CareerAI\automation\pipeline.py
Start in: C:\CareerAI\automation
```

Now your shortlist is waiting for you before your first coffee, instead of you opening three tabs.

### A few important cautions

- **Rate limits, not stealth.** JobSpy is a scraping library; job boards can rate-limit or temporarily block an IP that scrapes too aggressively. Keep `results_wanted` modest (20–30), run once or twice a day, not in a tight loop.
- **This reads job boards, it doesn't log in as you.** It never touches your Naukri/LinkedIn credentials, so there's no account-ban risk from the scraping itself — the risk category above (auto-*applying*) is a different tool category we're avoiding.
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

Open `.env` and point the AI provider setting at your local Ollama (the repo's `.env.example` documents the exact variable name and the Ollama option — set model to `qwen2.5:14b` or `llama3.1:8b` to match what you already pulled). Then:

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

Resume Matcher optimises for ATS keyword match — it does **not** independently verify that every rewritten line is still literally true against your Career Profile. Before you save the final version, paste the tailored resume back into your Ollama window (or Resume Matcher's own chat) with:

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
5 min  → Check Naukri alert emails/app (the one portal the script can't reach)
5–10 min → Open automation\shortlist\shortlist_<today>.csv (LinkedIn + Indeed,
           already scraped and scored overnight)
↓
Skim top-ranked rows; read the AI's top_reasons and red_flags columns
↓
Pick your 1–2 genuinely strong matches (from either source)
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
Java Architect · Backend Architect · Solutions Architect · Principal Engineer Java
Staff Engineer Java · Microservices Architect · Platform Architect · Cloud Architect
AI Architect · GenAI Architect · AI Engineering Manager · AI Platform Architect
Machine Learning Architect · Generative AI Lead · LLM Architect
```

---

# Appendix B — Company research prompt (still manual — run this when checking a specific company)

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

# Appendix F — Privacy notes for this v3 stack

- **Ollama**: your career profile, preferences and every job description are processed by a model running on your own machine — nothing leaves it during scoring.
- **`pipeline.py`**: only talks to (a) the public job-board pages JobSpy fetches and (b) your local Ollama endpoint. It writes plain CSVs to your own disk.
- **Resume Matcher**: designed to run locally against Ollama; if you ever switch its `.env` to a cloud provider (OpenAI/Gemini/etc.) instead of Ollama, that provider will receive the resume/job text you paste in — check its `.env.example` comments before you decide.
- Naukri, LinkedIn, Indeed and employer portals remain online systems. Anything you eventually submit *there* is not "local" just because your AI is local.

---

# Appendix G — Troubleshooting

**`Naukri API response status code 406 - recaptcha required`** → expected and not fixable by retrying — see "Why Naukri isn't in the automated scrape" in Part 7. Use Naukri's native alerts instead; the pipeline still covers LinkedIn + Indeed.

**`python-jobspy` import error** → `pip install -U python-jobspy` (package name has a hyphen; the Python import is `from jobspy import scrape_jobs`, no hyphen).

**Pipeline returns 0 new jobs every day** → check `automation\seen_jobs.csv`; delete it if you want to reprocess everything from scratch, or it means your searches genuinely aren't finding new postings — widen `hours_old` or add more `searches` entries from Appendix A.

**Ollama call in `pipeline.py` times out or errors** → confirm `ollama serve` is running and `ollama_url`/`ollama_model` in `config.json` match what you pulled (`ollama list` to check installed models).

**Resume Matcher backend won't start** → re-check `.env` for a valid AI provider block; re-run `uv sync` inside `apps/backend`.

**Model is too slow** → switch both `config.json` and Resume Matcher's `.env` to `llama3.1:8b`.

**AI is inventing details anywhere in the stack** → don't loosen the prompt; strengthen the Career Profile (Part 4), and re-run the strict fact-check prompt (Part 8, Step 5) before saving any resume.

---

# Final setup checklist

```text
[ ] Ollama installed, model pulled, LOCAL AI READY test passed
[ ] Python 3.10+, Node 22+, Git, uv installed
[ ] python-jobspy and requests installed (pip install -U python-jobspy requests)
[ ] resume.pdf → resume-source.txt → career-profile.md created and verified
[ ] job-preferences.md filled in with real numbers
[ ] target-companies.csv seeded
[ ] automation\config.json edited with your real search terms
[ ] pipeline.py --dry-run tested successfully
[ ] pipeline.py full run produced a shortlist CSV
[ ] (optional) Task Scheduler entry created for a daily automatic run
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
