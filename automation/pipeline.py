#!/usr/bin/env python3
"""
CareerAI Pipeline -- local job discovery + AI scoring
=====================================================

What this does, end to end, with no manual portal-browsing:

 1. FIND    -- uses the open-source `python-jobspy` library to pull fresh
              job postings from LinkedIn, Indeed, ZipRecruiter, Bayt, and
              Google Jobs aggregation in one run.
 2. FILTER  -- drops jobs you've already seen or already applied to.
 3. SCORE   -- sends each new job, together with your career-profile.md
              and job-preferences.md, to your local Ollama model and asks
              for a structured APPLY / HOLD / SKIP verdict + rubric score.
 4. COMPANY -- makes a second, lightweight Ollama call to score the hiring
              company against your company priorities (separate from role fit).
 5. OUTPUT  -- writes a shortlist CSV + prints a digest. Daily routine =
              "open one CSV" instead of "browse job boards".

Requirements
------------
    pip install -U python-jobspy requests

Ollama must already be running locally (`ollama serve`) with a model
pulled (`ollama pull qwen2.5:14b`).

Usage
-----
    python pipeline.py                       # full run (scrape + evaluate)
    python pipeline.py --step scrape         # scrape + filter + cache only
    python pipeline.py --step evaluate       # evaluate pending cached jobs
    python pipeline.py --reevaluate all      # rescore all shortlists based on current preferences
    python pipeline.py --reevaluate 2026-09-18 # rescore a specific shortlist
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

# Force UTF-8 output so Unicode chars in job descriptions don't crash the console
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests

try:
    from jobspy import scrape_jobs
except ImportError:
    sys.exit("python-jobspy is not installed. Run: pip install -U python-jobspy")

try:
    import json_repair
except ImportError:
    json_repair = None

class _SuppressJobSpy(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("JobSpy")

logging.root.addFilter(_SuppressJobSpy())
if not logging.root.handlers:
    logging.root.addHandler(logging.NullHandler())

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_JOBS_PATH = HERE / "seen_jobs.csv"
SHORTLIST_DIR = HERE / "shortlist"
DATA_DIR = HERE / "data"

# Ensure data dir exists
DATA_DIR.mkdir(exist_ok=True)
JOBS_CACHE_PATH = DATA_DIR / "jobs_cache.jsonl"
PENDING_EVAL_PATH = DATA_DIR / "pending_eval.jsonl"

SHORTLIST_FIELDNAMES = [
    "score", "company_score", "company_tier",
    "decision", "title", "company", "location", "job_url",
    "min_amount", "max_amount", "currency", "site",
    "top_reasons", "red_flags", "missing_information",
    "company_notes", "score_breakdown",
]

# ---------------------------------------------------------------------------
# ROLE EVALUATION PROMPT
# ---------------------------------------------------------------------------
EVAL_INSTRUCTIONS = """You are evaluating a job for a candidate.
Use ONLY the CAREER PROFILE and JOB PREFERENCES provided as ground truth.
Use ONLY the JOB POSTING text for facts about the job.
Never invent salary, culture or requirements -- if not stated, mark UNKNOWN.

-- EVALUATION ORDER ------------------------------------------------------
Evaluate each axis in order:
  (1) location_fit     -- does the job location match the candidate's priority?
  (2) role_title_fit   -- is the title within the target roles list?
  (3) skills_depth     -- how many of the job's required skills are in the profile?
  (4) seniority_fit    -- does the years-of-experience / seniority match?
  (5) compensation     -- is the salary (if stated) within or above the minimum CTC?
  (6) ai_ml_fit        -- does the role involve AI/ML/GenAI where the profile matches?
  (7) red_flags        -- count hard blockers (relocation, junior level, unrelated domain)
  (8) unknowns         -- list meaningful unknowns (salary, remote policy, stack, etc.)

-- SCORE CONSTRUCTION (build the integer score step by step) -------------
Start from a base of 50. Apply ALL of the following adjustments and SUM them:

  location_fit      +15 if PASS,  0 if UNKNOWN,  -20 if FAIL
  role_title_fit    +10 if exact target role, +5 if adjacent/senior-adjacent, -5 if off-target
  skills_depth      +10 if strong (>=4 key skills match), +5 if moderate (2-3 match), -5 if weak (<2)
  seniority_fit     +5 if 15yr+ / architect / VP-level match, 0 if borderline, -10 if junior mismatch
  compensation      +5 if GOOD (disclosed & >= minimum CTC), -5 if POOR (disclosed & below), 0 if UNKNOWN
  ai_ml_fit         +5 if role explicitly involves AI/ML/LLM and candidate profile matches, 0 otherwise
  red_flags_penalty -5 per distinct hard red flag (mandatory relocation, night-shift only, etc.)
  unknowns_penalty  -2 per significant unknown, maximum deduction -10

Clamp the final score to [0, 100].

-- RESPONSE FORMAT -------------------------------------------------------
Respond with ONLY a single JSON object. No prose, no markdown fences.

{
  "decision": "APPLY" | "HOLD" | "SKIP",
  "score": <integer 0-100>,
  "score_breakdown": {
    "base": 50,
    "location_fit": <int>,
    "role_title_fit": <int>,
    "skills_depth": <int>,
    "seniority_fit": <int>,
    "compensation": <int>,
    "ai_ml_fit": <int>,
    "red_flags_penalty": <int>,
    "unknowns_penalty": <int>
  },
  "location_fit": "PASS" | "FAIL" | "UNKNOWN",
  "compensation": "GOOD" | "ACCEPTABLE" | "POOR" | "UNKNOWN",
  "role_fit": "STRONG" | "MODERATE" | "WEAK",
  "top_reasons": ["...", "...", "..."],
  "red_flags": ["..."],
  "missing_information": ["..."]
}

CRITICAL: The "score" field MUST equal the arithmetic sum of all score_breakdown values,
clamped to [0, 100]. Never default to 85. Compute strictly from the rubric above.
"""

# ---------------------------------------------------------------------------
# COMPANY EVALUATION PROMPT
# ---------------------------------------------------------------------------
COMPANY_EVAL_INSTRUCTIONS = """You are rating a company as an employer for this specific candidate.

Use ONLY:
  - The company name and any size/funding/product/domain signals visible in the job description.
  - The candidate's COMPANY PREFERENCES section below.

Do NOT use external knowledge not present in the provided text.
If the company name is unrecognisable from the job description text alone, base the score
purely on industry/domain/size signals you can observe.

RATING SCALE:
  86-100 -> On the candidate's Priority A list OR multiple strong quality signals
           (high comp potential, product/GCC, stable, Chennai presence confirmed)
  71-85  -> On the Priority B list OR solid independent signals
  56-70  -> Solid mid-tier employer; not on priority list; some positive signals
  41-55  -> Neutral / unclear; limited information
  0-40   -> Red flags: staffing/consulting middleman, below-target industry, poor signals

Respond with ONLY this JSON, no prose, no fences:
{
  "company_score": <integer 0-100>,
  "company_tier": "A" | "B" | "UNKNOWN",
  "company_notes": "<one concise sentence rationale>"
}
"""

# ---------------------------------------------------------------------------
# Config & file helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH}. Copy config.example.json to config.json first.")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def load_text(path_str: str) -> str:
    p = Path(path_str)
    if not p.is_absolute():
        p = (HERE / path_str).resolve()
    if not p.exists():
        sys.exit(f"Required file not found: {p}")
    return p.read_text(encoding="utf-8", errors="ignore")

def load_target_companies(path_str: str) -> dict:
    p = Path(path_str)
    if not p.is_absolute():
        p = (HERE / path_str).resolve()
    if not p.exists():
        return {}
    result = {}
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("Company") or "").strip().lower()
            if name:
                result[name] = {
                    "priority": (row.get("Priority") or "").strip().upper(),
                    "compensation_potential": (row.get("CompensationPotential") or "").strip(),
                    "chennai": (row.get("Chennai") or "").strip(),
                    "product_or_gcc": (row.get("ProductOrGCC") or "").strip(),
                }
    return result

def load_seen_job_urls() -> set:
    if not SEEN_JOBS_PATH.exists():
        return set()
    with SEEN_JOBS_PATH.open(newline="", encoding="utf-8") as f:
        return {row["job_url"] for row in csv.DictReader(f) if row.get("job_url")}

def mark_job_seen(url: str) -> None:
    is_new = not SEEN_JOBS_PATH.exists()
    with SEEN_JOBS_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["job_url", "date_processed"])
        writer.writerow([url, date.today().isoformat()])

def load_applied_urls(tracker_path: str) -> set:
    p = Path(tracker_path)
    if not p.is_absolute():
        p = (HERE / tracker_path).resolve()
    if not p.exists():
        return set()
    with p.open(newline="", encoding="utf-8") as f:
        return {row.get("JobURL", "") for row in csv.DictReader(f)}

# ---------------------------------------------------------------------------
# Data Caching Helpers
# ---------------------------------------------------------------------------

def save_jobs_to_jsonl(path: Path, jobs: list[dict], append: bool = True) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j, default=str) + "\n")

def load_jobs_from_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    jobs = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                jobs.append(json.loads(line))
    return jobs

def load_jobs_cache_as_dict(path: Path) -> dict:
    """Returns {job_url: job_dict} for O(1) lookup."""
    cache = {}
    for j in load_jobs_from_jsonl(path):
        url = j.get("job_url")
        if url:
            cache[url] = j
    return cache

def pop_pending_job(path: Path) -> dict | None:
    """Reads the first job, rewrites the file without it, returns the job."""
    jobs = load_jobs_from_jsonl(path)
    if not jobs:
        return None
    job = jobs.pop(0)
    save_jobs_to_jsonl(path, jobs, append=False)
    return job

# ---------------------------------------------------------------------------
# Job scraping -- with smart retry / error classification
# ---------------------------------------------------------------------------

_PERMANENT_BLOCK_CODES: frozenset[int] = frozenset({400, 401, 403, 404, 406, 407, 451})
_RETRYABLE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 524})

def _extract_status_code(exc: Exception) -> int | None:
    msg = str(exc)
    matches = re.findall(r"\b([2-5]\d{2})\b", msg)
    for m in matches:
        code = int(m)
        if 200 <= code <= 599:
            return code
    return None

def _is_retryable(exc: Exception) -> tuple[bool, int | None]:
    code = _extract_status_code(exc)
    if code is not None:
        if code in _RETRYABLE_CODES:
            return True, code
        if code in _PERMANENT_BLOCK_CODES:
            return False, code
        if 400 <= code < 500:
            return False, code
        if 500 <= code < 600:
            return True, code
    exc_type = type(exc).__name__.lower()
    if any(t in exc_type for t in ("connection", "timeout", "reset", "eof")):
        return True, None
    msg_lower = str(exc).lower()
    if any(t in msg_lower for t in ("connection", "timeout", "network", "reset", "eof")):
        return True, None
    return True, None

def _backoff_seconds(attempt: int, code: int | None) -> float:
    if code == 429:
        return 60.0 * (2 ** (attempt - 1))
    return 10.0 * (2 ** (attempt - 1))

def _scrape_one(cfg: dict, max_retries: int = 2):
    sites = cfg.get("site_name", ["linkedin", "indeed"])
    is_google_only = sites == ["google"] or sites == "google"
    search_term = (
        cfg.get("google_search_term", cfg["search_term"])
        if is_google_only else cfg["search_term"]
    )
    kwargs = dict(
        site_name=sites,
        search_term=search_term,
        location=cfg["location"],
        results_wanted=cfg.get("results_wanted", 20),
        hours_old=cfg.get("hours_old", 72),
        country_indeed=cfg.get("country_indeed", "India"),
        is_remote=cfg.get("is_remote", False),
        linkedin_fetch_description=True,
    )

    for attempt in range(1, max_retries + 2):
        try:
            return scrape_jobs(**kwargs)
        except Exception as exc:
            retryable, code = _is_retryable(exc)
            if not retryable:
                reason = f"HTTP {code}" if code else "permanent error"
                print(f"    [FAIL] {reason} (no retry): {exc}")
                return None
            if attempt > max_retries:
                reason = f"HTTP {code}" if code else type(exc).__name__
                print(f"    [FAIL] Failed after {max_retries} retries ({reason}): {exc}")
                return None
            wait = _backoff_seconds(attempt, code)
            reason = f"HTTP {code}" if code else type(exc).__name__
            print(f"    [retry] Attempt {attempt} failed ({reason}). "
                  f"Retrying in {wait:.0f}s... [{exc}]")
            time.sleep(wait)
    return None

def scrape_all(search_configs, max_retries: int = 2) -> list:
    import pandas as pd
    frames = []
    for cfg in search_configs:
        sites = cfg.get("site_name", ["linkedin", "indeed"])
        print(f"  scraping: {cfg['search_term']!r} @ {cfg['location']!r} on {sites}")
        df = _scrape_one(cfg, max_retries=max_retries)
        if df is None:
            pass
        elif df.empty:
            print(f"    -> 0 results")
        else:
            frames.append(df)
            print(f"    -> {len(df)} results")
        time.sleep(2)
    if not frames:
        return []
    combined = pd.concat(frames, ignore_index=True)
    # Fill NaN with empty string to avoid JSON serialisation errors
    combined = combined.fillna("")
    combined = combined.drop_duplicates(subset=["job_url"])
    return combined.to_dict(orient="records")

# ---------------------------------------------------------------------------
# Ollama calls
# ---------------------------------------------------------------------------

def call_ollama(model: str, url: str, prompt: str, timeout: int | None = None, retries: int = 2) -> dict:
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url,
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 3000
                    },
                    "think": False,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = (resp.json().get("response") or "").strip()
            
            if not raw:
                if attempt < retries:
                    print(f"    ! Ollama returned an empty response, retrying ({attempt+1}/{retries})...")
                    time.sleep(1)
                    continue
                print("    ! Ollama returned an empty response")
                return {}
                
            # Robust JSON extraction to handle conversational fluff or markdown
            start_idx = raw.find('{')
            end_idx = raw.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                raw = raw[start_idx:end_idx+1]
            
            if json_repair:
                parsed = json_repair.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            
            return json.loads(raw)
            
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt < retries:
                print(f"    ! Ollama call failed ({e}), retrying ({attempt+1}/{retries})...")
                time.sleep(2)
                continue
            print(f"    ! Ollama call failed after {retries} retries: {e}")
            return {}

def evaluate_job(job: dict, career_profile: str, job_preferences: str,
                 model: str, ollama_url: str, timeout: int | None = None) -> dict:
    description = (job.get("description") or "")[:3500]
    prompt = (
        f"{EVAL_INSTRUCTIONS}\n\n"
        f"JOB POSTING:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nSalary info: min={job.get('min_amount')} "
        f"max={job.get('max_amount')} currency={job.get('currency')}\n"
        f"Description:\n{description}\n\n"
        f"CAREER PROFILE:\n{career_profile}\n\n"
        f"JOB PREFERENCES:\n{job_preferences}\n"
    )
    result = call_ollama(model, ollama_url, prompt, timeout=timeout)
    if not result:
        return {
            "decision": "HOLD", "score": 0,
            "top_reasons": ["Model returned no output -- review manually."],
            "red_flags": [], "missing_information": [],
            "location_fit": "UNKNOWN", "compensation": "UNKNOWN",
            "role_fit": "MODERATE",
            "score_breakdown": {
                "base": 50, "location_fit": 0, "role_title_fit": 0,
                "skills_depth": 0, "seniority_fit": 0, "compensation": 0,
                "ai_ml_fit": 0, "red_flags_penalty": 0, "unknowns_penalty": -50,
            },
        }

    breakdown = result.get("score_breakdown", {})
    if breakdown:
        def _to_int(val, default=0):
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        computed = (
            _to_int(breakdown.get("base"), 50)
            + _to_int(breakdown.get("location_fit"), 0)
            + _to_int(breakdown.get("role_title_fit"), 0)
            + _to_int(breakdown.get("skills_depth"), 0)
            + _to_int(breakdown.get("seniority_fit"), 0)
            + _to_int(breakdown.get("compensation"), 0)
            + _to_int(breakdown.get("ai_ml_fit"), 0)
            + _to_int(breakdown.get("red_flags_penalty"), 0)
            + _to_int(breakdown.get("unknowns_penalty"), 0)
        )
        clamped = max(0, min(100, computed))
        if abs(result.get("score", clamped) - clamped) > 5:
            result["score"] = clamped

    return result

def evaluate_company(job: dict, job_preferences: str, target_companies: dict,
                     model: str, ollama_url: str, timeout: int | None = None) -> dict:
    company_name = (job.get("company") or "").strip()
    description_snippet = (job.get("description") or "")[:1500]

    lookup = target_companies.get(company_name.lower(), {})
    lookup_hint = ""
    if lookup:
        lookup_hint = (
            f"\nKNOWN COMPANY DATA (from candidate's curated list):\n"
            f"  Priority tier: {lookup.get('priority', 'unknown')}\n"
            f"  Compensation potential: {lookup.get('compensation_potential', 'unknown')}\n"
            f"  Chennai presence: {lookup.get('chennai', 'unknown')}\n"
            f"  Type: {lookup.get('product_or_gcc', 'unknown')}\n"
        )

    prompt = (
        f"{COMPANY_EVAL_INSTRUCTIONS}\n\n"
        f"COMPANY NAME: {company_name}\n"
        f"JOB DESCRIPTION EXCERPT:\n{description_snippet}\n"
        f"{lookup_hint}\n"
        f"COMPANY PREFERENCES:\n{job_preferences}\n"
    )
    result = call_ollama(model, ollama_url, prompt, timeout=timeout)
    if not result:
        if lookup:
            tier = lookup.get("priority", "UNKNOWN")
            base_score = 82 if tier == "A" else (70 if tier == "B" else 50)
            return {
                "company_score": base_score,
                "company_tier": tier if tier in ("A", "B") else "UNKNOWN",
                "company_notes": (f"From target-companies.csv (Priority {tier}). "
                                  f"Model returned no output."),
            }
        return {
            "company_score": 50,
            "company_tier": "UNKNOWN",
            "company_notes": "Could not evaluate -- model returned no output.",
        }

    if lookup and lookup.get("priority") in ("A", "B"):
        result["company_tier"] = lookup["priority"]
        if lookup["priority"] == "A":
            result["company_score"] = max(result.get("company_score", 0), 82)
        elif lookup["priority"] == "B":
            result["company_score"] = max(result.get("company_score", 0), 70)

    return result

# ---------------------------------------------------------------------------
# Shortlist I/O
# ---------------------------------------------------------------------------

def get_shortlist_path() -> Path:
    return SHORTLIST_DIR / "shortlist.csv"

def ensure_shortlist_header(path: Path) -> None:
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES).writeheader()

def append_shortlist_row(path: Path, row: dict) -> None:
    flat = dict(row)
    for k in ("top_reasons", "red_flags", "missing_information"):
        if isinstance(flat.get(k), list):
            flat[k] = " | ".join(flat[k])
    if isinstance(flat.get("score_breakdown"), dict):
        bd = flat["score_breakdown"]
        flat["score_breakdown"] = " | ".join(f"{k}={v}" for k, v in bd.items())
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES, extrasaction="ignore")
        writer.writerow(flat)

def read_shortlist_rows(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def load_shortlist_job_urls(path: Path) -> set:
    return {r["job_url"] for r in read_shortlist_rows(path) if r.get("job_url")}

def rewrite_shortlist(path: Path, rows: list[dict]) -> None:
    """Overwrites the shortlist file entirely."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for k in ("top_reasons", "red_flags", "missing_information"):
                if isinstance(flat.get(k), list):
                    flat[k] = " | ".join(flat[k])
            if isinstance(flat.get("score_breakdown"), dict):
                bd = flat["score_breakdown"]
                flat["score_breakdown"] = " | ".join(f"{k}={v}" for k, v in bd.items())
            writer.writerow(flat)


# ---------------------------------------------------------------------------
# Main Execution Blocks
# ---------------------------------------------------------------------------

def do_scrape(cfg: dict, out_path: Path):
    print("Loading history (already-seen and already-applied jobs)...")
    seen = load_seen_job_urls()
    applied = load_applied_urls(cfg.get("tracker_path", "../tracker/applications.csv"))
    already_shortlisted = load_shortlist_job_urls(out_path)
    if already_shortlisted:
        print(f"  {len(already_shortlisted)} job(s) already in today's shortlist")

    skip_urls = seen | applied | already_shortlisted

    print("Scraping job boards (this can take a few minutes)...")
    max_retries = cfg.get("max_retries", 2)
    jobs = scrape_all(cfg["searches"], max_retries=max_retries)
    print(f"  {len(jobs)} unique postings scraped across all boards")

    new_jobs = [j for j in jobs if j.get("job_url") and j["job_url"] not in skip_urls]
    print(f"  {len(new_jobs)} are new (not previously seen or applied to)")

    if new_jobs:
        print("Caching new job data...")
        save_jobs_to_jsonl(JOBS_CACHE_PATH, new_jobs, append=True)
        save_jobs_to_jsonl(PENDING_EVAL_PATH, new_jobs, append=True)
    return new_jobs


def do_evaluate(cfg: dict, out_path: Path, career_profile: str, job_preferences: str, target_companies: dict, role_timeout: int|None, company_timeout: int|None):
    pending_jobs = load_jobs_from_jsonl(PENDING_EVAL_PATH)
    if not pending_jobs:
        print("No pending jobs to evaluate.")
        return

    ensure_shortlist_header(out_path)
    model = cfg.get("ollama_model", "qwen2.5:14b")
    ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")

    print(f"\nScoring {len(pending_jobs)} pending job(s)...")
    scored_this_run = 0

    try:
        while True:
            # Pop job one by one so crashes don't lose the queue
            j = pop_pending_job(PENDING_EVAL_PATH)
            if not j:
                break
            
            print(f"  [{scored_this_run+1}] {j.get('title')} @ {j.get('company')}")
            try:
                verdict = evaluate_job(
                    j, career_profile, job_preferences, model, ollama_url,
                    timeout=role_timeout)
            except Exception as e:
                print(f"    ! role scoring failed, re-queueing ({e})")
                # Put back in queue at front
                existing = load_jobs_from_jsonl(PENDING_EVAL_PATH)
                save_jobs_to_jsonl(PENDING_EVAL_PATH, [j] + existing, append=False)
                break

            try:
                company_verdict = evaluate_company(
                    j, job_preferences, target_companies, model, ollama_url,
                    timeout=company_timeout)
            except Exception as e:
                print(f"    ! company scoring failed, using fallback ({e})")
                company_verdict = {
                    "company_score": 50, "company_tier": "UNKNOWN",
                    "company_notes": f"Scoring error: {e}",
                }

            print(f"    -> role={verdict.get('score')} "
                  f"({verdict.get('decision')})  "
                  f"company={company_verdict.get('company_score')}")

            append_shortlist_row(out_path, {**j, **verdict, **company_verdict})
            mark_job_seen(j["job_url"])
            scored_this_run += 1

    except KeyboardInterrupt:
        print(f"\nStopped early. {scored_this_run} job(s) scored and saved "
              f"to {out_path.name} before you stopped.")
        print("Run the same command again to continue -- nothing is lost.")
        sys.exit(0)

def do_reevaluate(cfg: dict, targets: list, career_profile: str, job_preferences: str, target_companies: dict, role_timeout: int|None, company_timeout: int|None):
    """Reevaluates jobs in shortlists using their cached descriptions."""
    print("Loading job cache...")
    cache = load_jobs_cache_as_dict(JOBS_CACHE_PATH)
    if not cache:
        print("  ! Job cache is empty. Cannot reevaluate without descriptions.")
        return

    target_urls = set()
    csvs_to_process = set()
    evaluate_all_in_csv = False

    for t in targets:
        if t.lower() == "all":
            evaluate_all_in_csv = True
            csvs_to_process.add(get_shortlist_path())
        else:
            try:
                target_date = date.fromisoformat(t)
                p = get_shortlist_path()
                if p.exists():
                    csvs_to_process.add(p)
                    evaluate_all_in_csv = True
            except ValueError:
                target_urls.add(t)
                
    if target_urls and not evaluate_all_in_csv:
        csvs_to_process.add(get_shortlist_path())

    if not csvs_to_process:
        print("No shortlists found to reevaluate.")
        return
        
    csvs_to_process = sorted(list(csvs_to_process))

    model = cfg.get("ollama_model", "qwen2.5:14b")
    ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")

    for csv_path in csvs_to_process:
        print(f"\nReevaluating {csv_path.name}...")
        rows = read_shortlist_rows(csv_path)
        updated_rows = []
        rewrote_any = False
        for r in rows:
            url = r.get("job_url")
            
            # If we're looking for specific URLs and this isn't one, skip evaluation
            if target_urls and not evaluate_all_in_csv and url not in target_urls:
                updated_rows.append(r)
                continue
                
            if not url or url not in cache:
                print(f"  [SKIP] Job description not in cache: {r.get('title')} @ {r.get('company')}")
                updated_rows.append(r)
                continue
            
            job = cache[url]
            print(f"  [RE-SCORE] {job.get('title')} @ {job.get('company')}")
            rewrote_any = True
            
            try:
                verdict = evaluate_job(
                    job, career_profile, job_preferences, model, ollama_url,
                    timeout=role_timeout)
            except Exception as e:
                print(f"    ! role scoring failed ({e}), keeping old score.")
                updated_rows.append(r)
                continue
                
            try:
                company_verdict = evaluate_company(
                    job, job_preferences, target_companies, model, ollama_url,
                    timeout=company_timeout)
            except Exception as e:
                print(f"    ! company scoring failed ({e}), using fallback.")
                company_verdict = {
                    "company_score": 50, "company_tier": "UNKNOWN",
                    "company_notes": f"Scoring error: {e}",
                }
            
            print(f"    -> role={verdict.get('score')} "
                  f"({verdict.get('decision')})  "
                  f"company={company_verdict.get('company_score')}")
            
            updated_row = {**r, **verdict, **company_verdict}
            updated_rows.append(updated_row)
        
        # Only rewrite the CSV if we actually reevaluated something
        if not evaluate_all_in_csv and not rewrote_any:
            continue
            
        print(f"Writing updated {csv_path.name}...")
        rewrite_shortlist(csv_path, updated_rows)

    print("\nReevaluation complete.")

def check_if_open(url: str) -> bool:
    """Returns True if the job appears open, False if it appears closed."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return False
            
        text = resp.text.lower()
        if "no longer accepting applications" in text:
            return False
        if "this job is closed" in text:
            return False
        if "no longer available" in text:
            return False
            
        return True
    except Exception as e:
        print(f"    ! Error checking URL {url}: {e}")
        return True # Default to open if we can't tell

def do_sync_csv(cfg: dict, career_profile: str, job_preferences: str, target_companies: dict, role_timeout: int|None, company_timeout: int|None):
    """Scans shortlists for REEVALUATE or CHECK decisions and processes them."""
    print("Scanning shortlists for REEVALUATE or CHECK markers...")
    
    cache = load_jobs_cache_as_dict(JOBS_CACHE_PATH)
    if not cache:
        print("  ! Job cache is empty. Will not be able to reevaluate jobs.")
    
    model = cfg.get("ollama_model", "qwen2.5:14b")
    ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")

    csvs = [get_shortlist_path()]
    
    for csv_path in csvs:
        rows = read_shortlist_rows(csv_path)
        
        for i, r in enumerate(rows):
            decision = (r.get("decision") or "").strip().upper()
            url = r.get("job_url")
            
            if decision == "REEVALUATE":
                if not url or url not in cache:
                    print(f"  [SKIP REEVALUATE] Description not in cache: {r.get('title')} @ {r.get('company')}")
                    # Revert to a safe decision so it doesn't get stuck in a REEVALUATE loop
                    rows[i]["decision"] = "HOLD" 
                    rewrite_shortlist(csv_path, rows)
                    continue
                
                job = cache[url]
                print(f"  [RE-SCORE] {job.get('title')} @ {job.get('company')}")
                
                try:
                    verdict = evaluate_job(
                        job, career_profile, job_preferences, model, ollama_url,
                        timeout=role_timeout)
                except Exception as e:
                    print(f"    ! role scoring failed ({e}), keeping old score.")
                    rows[i]["decision"] = "HOLD"
                    rewrite_shortlist(csv_path, rows)
                    continue
                    
                try:
                    company_verdict = evaluate_company(
                        job, job_preferences, target_companies, model, ollama_url,
                        timeout=company_timeout)
                except Exception as e:
                    print(f"    ! company scoring failed ({e}), using fallback.")
                    company_verdict = {
                        "company_score": 50, "company_tier": "UNKNOWN",
                        "company_notes": f"Scoring error: {e}",
                    }
                
                print(f"    -> role={verdict.get('score')} "
                      f"({verdict.get('decision')})  "
                      f"company={company_verdict.get('company_score')}")
                updated_row = {**r, **verdict, **company_verdict}
                rows[i] = updated_row
                rewrite_shortlist(csv_path, rows)
                
            elif decision == "CHECK":
                if not url:
                    rows[i]["decision"] = "HOLD"
                    rewrite_shortlist(csv_path, rows)
                    continue
                    
                print(f"  [CHECK OPEN] {r.get('title')} @ {r.get('company')}")
                is_open = check_if_open(url)
                if is_open:
                    print("    -> Appears OPEN (changed decision to HOLD)")
                    rows[i]["decision"] = "HOLD"
                else:
                    print("    -> Appears CLOSED (changed decision to CLOSED)")
                    rows[i]["decision"] = "CLOSED"
                    
                rewrite_shortlist(csv_path, rows)

    print("CSV sync complete.")

def display_summary(out_path: Path, cfg: dict):
    rows = read_shortlist_rows(out_path)
    min_score = cfg.get("min_score_to_show", 60)

    def score_of(r):
        try:
            return int(r.get("score") or 0)
        except ValueError:
            return 0

    def sort_key(r):
        decision = (r.get("decision") or "").upper()
        return (0 if decision == "CLOSED" else 1, score_of(r))

    rows.sort(key=sort_key, reverse=True)

    print(f"\n{'-'*60}")
    print(f"Today's shortlist: {out_path}")
    print(f"{len(rows)} row(s) total | showing score >= {min_score}\n")

    apply_rows = [r for r in rows if (r.get("decision") or "").upper() == "APPLY"
                  and score_of(r) >= min_score]
    hold_rows  = [r for r in rows if (r.get("decision") or "").upper() == "HOLD"
                  and score_of(r) >= min_score]
    skip_rows  = [r for r in rows if (r.get("decision") or "").upper() == "SKIP"
                  and score_of(r) >= min_score]

    def print_rows(label, group):
        if not group:
            return
        print(f"  -- {label} --")
        for r in group:
            cs = r.get("company_score", "-")
            print(f"  [role={r.get('score', '-'):>3}  co={cs:>3}] "
                  f"{r.get('decision', '')}: {r.get('title')} @ {r.get('company')} "
                  f"({r.get('location')})")
            print(f"       {r.get('job_url')}")
        print()

    print_rows("APPLY", apply_rows)
    print_rows("HOLD",  hold_rows)
    print_rows("SKIP",  skip_rows)

    if not any([apply_rows, hold_rows, skip_rows]):
        print("  (nothing to show yet)")


def main():
    parser = argparse.ArgumentParser(
        description="CareerAI pipeline -- scrape, score, and track job postings.")
    parser.add_argument("--step", choices=["scrape", "evaluate", "full"], default="full",
                        help="Run only a specific step of the pipeline.")
    parser.add_argument("--reevaluate", nargs="+", default=None,
                        help="Reevaluate past jobs. Pass 'all', dates (YYYY-MM-DD), or specific job URLs.")
    parser.add_argument("--sync-csv", action="store_true",
                        help="Scan all shortlists for rows with decision=REEVALUATE or CHECK and process them.")
    parser.add_argument("--limit", type=int, default=None,
                        help="max number of new jobs to score this run (deprecated, use steps instead)")
    parser.add_argument("--date", type=str, default=None,
                        help="YYYY-MM-DD -- force a specific shortlist date (reruns next morning)")
    args = parser.parse_args()

    cfg = load_config()
    SHORTLIST_DIR.mkdir(exist_ok=True)

    if args.date:
        print(f"Warning: --date is deprecated. All data is now written to shortlist.csv.")

    out_path = get_shortlist_path()

    print("Loading career profile and preferences...")
    career_profile = load_text(cfg["career_profile_path"])
    job_preferences = load_text(cfg["job_preferences_path"])

    print("Loading target companies list...")
    companies_path = cfg.get("target_companies_path", "../profile/target-companies.csv")
    target_companies = load_target_companies(companies_path)
    print(f"  {len(target_companies)} companies loaded from priority list")

    def _parse_timeout(val) -> int | None:
        if val is None or val == 0:
            return None
        return int(val)
    role_timeout    = _parse_timeout(cfg.get("ollama_timeout_role"))
    company_timeout = _parse_timeout(cfg.get("ollama_timeout_company"))

    if args.sync_csv:
        do_sync_csv(cfg, career_profile, job_preferences, target_companies, role_timeout, company_timeout)
        return

    if args.reevaluate:
        do_reevaluate(cfg, args.reevaluate, career_profile, job_preferences, target_companies, role_timeout, company_timeout)
        return

    if args.step in ["scrape", "full"]:
        do_scrape(cfg, out_path)

    if args.step in ["evaluate", "full"]:
        do_evaluate(cfg, out_path, career_profile, job_preferences, target_companies, role_timeout, company_timeout)

    display_summary(out_path, cfg)


if __name__ == "__main__":
    main()
