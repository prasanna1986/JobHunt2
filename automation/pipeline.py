#!/usr/bin/env python3
"""
CareerAI Pipeline -- local job discovery + AI scoring
=====================================================

What this does, end to end, with no manual portal-browsing:

 1. FIND    -- uses the open-source `python-jobspy` library to pull fresh
              job postings from LinkedIn, Indeed, ZipRecruiter, Bayt, and
              Google Jobs aggregation in one run.
 2. FILTER  -- drops jobs you've already seen or already applied to.
 3. STALE   -- checks every APPLY/HOLD job from all previous shortlists
              against the current scrape. Any that no longer appear are
              written into today's shortlist with decision="CLOSED".
 4. SCORE   -- sends each new job, together with your career-profile.md
              and job-preferences.md, to your local Ollama model and asks
              for a structured APPLY / HOLD / SKIP verdict + rubric score.
 5. COMPANY -- makes a second, lightweight Ollama call to score the hiring
              company against your company priorities (separate from role fit).
 6. OUTPUT  -- writes a shortlist CSV + prints a digest. Daily routine =
              "open one CSV" instead of "browse job boards".

Nothing here submits an application or messages a recruiter. That step
stays yours, on purpose (see the SOP for why).

RESUMABLE BY DESIGN
--------------------
Every job's row is written to today's shortlist CSV, and its URL is marked
"seen", IMMEDIATELY after it is scored -- not in one batch at the end. If the
script crashes, loses network, or you Ctrl+C it partway through, nothing
already-scored is lost. Just run the exact same command again: already-seen
jobs are skipped instantly and it picks up with whatever's left.

Two additional safety layers:
  (a) At startup the script also reads all job_url values already present in
      today's shortlist CSV. Even if seen_jobs.csv missed a write due to an
      abrupt kill, the shortlist is the authoritative deduplicate source.
  (b) Stale / closed position detection: after each run you'll see a CLOSED
      row in today's shortlist for any APPLY/HOLD job from prior days that
      no longer appears in the current scrape -- so you know immediately to
      deprioritise it in your outreach.

Requirements
------------
    pip install -U python-jobspy requests

Ollama must already be running locally (`ollama serve`) with a model
pulled (`ollama pull qwen2.5:14b`).

Usage
-----
    python pipeline.py                       # full run (resumes automatically)
    python pipeline.py --dry-run             # scrape + filter + stale-check; skip AI scoring
    python pipeline.py --limit 15            # only score the next 15 new jobs
    python pipeline.py --date 2026-09-13     # force a specific shortlist date (reruns)
    python pipeline.py --no-stale-check      # skip closed-position detection
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

# Force UTF-8 output so Unicode chars in job descriptions don't crash the
# Windows console (which defaults to cp1252). Must be set before first print().
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

# Silence jobspy's per-site INFO/ERROR chatter on stderr.
# jobspy's create_logger() resets individual logger levels on every scrape
# call, so setLevel() on named loggers is overridden each time. Instead we
# attach a filter to the ROOT logger that silently drops every record whose
# logger name starts with "JobSpy". This is the only reliable suppression
# point that survives jobspy's internal logger re-initialisation.
class _SuppressJobSpy(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("JobSpy")

logging.root.addFilter(_SuppressJobSpy())
# Also ensure the root handler exists so the filter has something to attach to.
if not logging.root.handlers:
    logging.root.addHandler(logging.NullHandler())

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_JOBS_PATH = HERE / "seen_jobs.csv"
SHORTLIST_DIR = HERE / "shortlist"

# Columns: score + company_score together at front so they sort/filter easily in Excel/Sheets.
# score_breakdown stays in CSV as a pipe-separated string for full auditability.
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
# The rubric forces the model to *construct* a score step-by-step rather than
# anchor at a comfortable round number like 85. Produces real spread:
#   * perfect match (Chennai + target title + deep skill match + A-company) -> ~92-95
#   * partial match (Chennai + OK title + unknown salary)                   -> ~65-70
#   * mismatch (wrong domain / junior / relocated)                          -> ~15-35
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
    """Return {company_name_lower: {priority, compensation_potential, ...}}."""
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
    """Append one URL to seen_jobs.csv immediately -- called right after that
    job's row is safely written to the shortlist, so a crash can never mark
    a job seen without also having saved its scored result."""
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
# Previous shortlist helpers -- for stale / closed-position detection
# ---------------------------------------------------------------------------

def load_all_previous_shortlists(today_path: Path) -> list[dict]:
    """Return all APPLY/HOLD rows from every shortlist CSV except today's.

    These are jobs the candidate found interesting in prior runs. We'll check
    whether they're still available in the current scrape and flag any that
    have disappeared as CLOSED.
    """
    watchlist = []
    for csv_path in sorted(SHORTLIST_DIR.glob("shortlist_*.csv")):
        if csv_path == today_path:
            continue
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                decision = (row.get("decision") or "").strip().upper()
                url = (row.get("job_url") or "").strip()
                if decision in ("APPLY", "HOLD") and url:
                    watchlist.append(row)
    # Deduplicate by URL (keep most recent occurrence)
    seen_urls: set = set()
    deduped = []
    for row in reversed(watchlist):
        url = row["job_url"]
        if url not in seen_urls:
            seen_urls.add(url)
            deduped.append(row)
    return deduped


def find_closed_positions(watchlist: list[dict], scraped_urls: set,
                          already_in_today: set) -> list[dict]:
    """Return watchlist rows whose URL did NOT appear in the current scrape
    and has NOT already been written to today's shortlist as CLOSED.

    A job that disappears from scrape results is almost certainly filled,
    expired, or delisted -- worth surfacing so the candidate can deprioritise
    any pending outreach.
    """
    closed = []
    for row in watchlist:
        url = row["job_url"]
        if url not in scraped_urls and url not in already_in_today:
            closed.append(row)
    return closed


# ---------------------------------------------------------------------------
# Job scraping -- with smart retry / error classification
# ---------------------------------------------------------------------------

# Status codes that are permanent blocks -- no point retrying, the site won't
# suddenly change its mind. Log once and move on.
_PERMANENT_BLOCK_CODES: frozenset[int] = frozenset({
    400,  # Bad request -- usually bad search params or an anti-bot page
    401,  # Unauthorized
    403,  # Forbidden -- site is blocking the scraper (e.g. Glassdoor)
    404,  # Not found
    406,  # Not Acceptable -- Naukri's captcha / RECAPTCHA wall
    407,  # Proxy authentication required
    451,  # Unavailable for legal reasons
})

# Status codes where backing off and retrying is appropriate
_RETRYABLE_CODES: frozenset[int] = frozenset({
    429,  # Too Many Requests -- rate-limited; needs a longer sleep
    500,  # Internal Server Error -- transient server fault
    502,  # Bad Gateway -- transient proxy/CDN fault
    503,  # Service Unavailable -- server overloaded or in maintenance
    504,  # Gateway Timeout -- transient upstream timeout
    524,  # Cloudflare timeout (seen on some job boards)
})


def _extract_status_code(exc: Exception) -> int | None:
    """Try to parse an HTTP status code from an exception message.

    jobspy catches HTTP errors internally and raises them with messages like:
      "Glassdoor response status code 400"
      "Glassdoor: bad response status code: 403"
      "status_code=429"
    We fish out the 3-digit code so we can classify the failure.
    """
    msg = str(exc)
    # Look for any 3-digit sequence that plausibly is an HTTP code (2xx-5xx)
    matches = re.findall(r"\b([2-5]\d{2})\b", msg)
    for m in matches:
        code = int(m)
        if 200 <= code <= 599:
            return code
    return None


def _is_retryable(exc: Exception) -> tuple[bool, int | None]:
    """Return (should_retry, http_status_code_or_None).

    Decision table:
      * 429 / 5xx          -> retry (transient)
      * 4xx (not 429)      -> don't retry (permanent block)
      * ConnectionError    -> retry (network blip)
      * Timeout            -> retry (server slow, try again)
      * Unknown exception  -> retry once (give benefit of the doubt)
    """
    code = _extract_status_code(exc)
    if code is not None:
        if code in _RETRYABLE_CODES:
            return True, code
        if code in _PERMANENT_BLOCK_CODES:
            return False, code
        if 400 <= code < 500:
            # Uncategorised 4xx -> permanent, don't retry
            return False, code
        if 500 <= code < 600:
            # Uncategorised 5xx -> transient, retry
            return True, code

    # No status code found -- check exception type by name
    exc_type = type(exc).__name__.lower()
    if any(t in exc_type for t in ("connection", "timeout", "reset", "eof")):
        return True, None
    msg_lower = str(exc).lower()
    if any(t in msg_lower for t in ("connection", "timeout", "network", "reset", "eof")):
        return True, None

    # Unknown -- be optimistic and allow one retry
    return True, None


def _backoff_seconds(attempt: int, code: int | None) -> float:
    """Return how many seconds to wait before the next attempt.

    * 429 (rate-limit): start at 60s, double each attempt (60->120->240)
    * 5xx / network:    start at 10s, double each attempt (10->20->40)
    """
    if code == 429:
        return 60.0 * (2 ** (attempt - 1))   # 60, 120, 240
    return 10.0 * (2 ** (attempt - 1))        # 10, 20, 40


def _scrape_one(cfg: dict, max_retries: int = 2) -> "pd.DataFrame | None":
    """Scrape a single search config with retry logic.

    Returns a DataFrame (possibly empty) on success, or None if the search
    failed permanently and should be skipped entirely.

    Retry policy:
      * retryable errors (429, 5xx, network) -> retry up to max_retries times
        with exponential backoff
      * permanent errors (4xx except 429)    -> log once, return None immediately
      * empty result (0 jobs)                -> NOT retried; the site just has
        no matches right now, which is a valid answer
    """
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

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 2):   # attempts: 1 ... max_retries+1
        try:
            return scrape_jobs(**kwargs)

        except Exception as exc:
            last_exc = exc
            retryable, code = _is_retryable(exc)

            if not retryable:
                # Permanent block -- log with clear reason, don't retry
                reason = f"HTTP {code}" if code else "permanent error"
                print(f"    [FAIL] {reason} (no retry): {exc}")
                return None

            if attempt > max_retries:
                # Exhausted retries
                reason = f"HTTP {code}" if code else type(exc).__name__
                print(f"    [FAIL] Failed after {max_retries} retries ({reason}): {exc}")
                return None

            # Retryable -- back off and try again
            wait = _backoff_seconds(attempt, code)
            reason = f"HTTP {code}" if code else type(exc).__name__
            print(f"    [retry] Attempt {attempt} failed ({reason}). "
                  f"Retrying in {wait:.0f}s... [{exc}]")
            time.sleep(wait)

    return None  # unreachable, but satisfies type checker


def scrape_all(search_configs, max_retries: int = 2) -> list:
    """Run all configured searches and return a deduplicated list of job dicts.

    Each search gets up to max_retries extra attempts on transient failures.
    Permanent blocks (403, 400, etc.) are skipped immediately without retry.
    """
    import pandas as pd

    frames = []
    for cfg in search_configs:
        sites = cfg.get("site_name", ["linkedin", "indeed"])
        print(f"  scraping: {cfg['search_term']!r} @ {cfg['location']!r} on {sites}")

        df = _scrape_one(cfg, max_retries=max_retries)

        if df is None:
            # Permanent failure already logged by _scrape_one
            pass
        elif df.empty:
            print(f"    -> 0 results")
        else:
            frames.append(df)
            print(f"    -> {len(df)} results")

        time.sleep(2)  # polite pause between searches regardless of outcome


    if not frames:
        return []
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["job_url"])
    return combined.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Ollama calls
# ---------------------------------------------------------------------------

def call_ollama(model: str, url: str, prompt: str, timeout: int | None = None) -> dict:
    try:
        resp = requests.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "{}")
        return json.loads(raw)
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"    ! Ollama call failed: {e}")
        return {}


def evaluate_job(job: dict, career_profile: str, job_preferences: str,
                 model: str, ollama_url: str, timeout: int | None = None) -> dict:
    """Score the role fit. Returns the model's parsed JSON dict with rubric enforcement."""
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

    # Enforce the rubric: recompute score from breakdown if model drifted.
    breakdown = result.get("score_breakdown", {})
    if breakdown:
        computed = (
            breakdown.get("base", 50)
            + breakdown.get("location_fit", 0)
            + breakdown.get("role_title_fit", 0)
            + breakdown.get("skills_depth", 0)
            + breakdown.get("seniority_fit", 0)
            + breakdown.get("compensation", 0)
            + breakdown.get("ai_ml_fit", 0)
            + breakdown.get("red_flags_penalty", 0)
            + breakdown.get("unknowns_penalty", 0)
        )
        clamped = max(0, min(100, computed))
        # Override the stated score if it deviates by more than 5 from the rubric sum.
        if abs(result.get("score", clamped) - clamped) > 5:
            result["score"] = clamped

    return result


def evaluate_company(job: dict, job_preferences: str, target_companies: dict,
                     model: str, ollama_url: str, timeout: int | None = None) -> dict:
    """Score the hiring company separately from role fit."""
    company_name = (job.get("company") or "").strip()
    description_snippet = (job.get("description") or "")[:1500]

    # Quick authoritative lookup from target-companies.csv
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
    result = call_ollama(model, ollama_url, prompt, timeout=company_timeout)
    if not result:
        # Fallback: derive directly from CSV priority without a model call
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

    # If we have authoritative CSV data, override model's tier and floor the score
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

def get_shortlist_path(run_date: date) -> Path:
    return SHORTLIST_DIR / f"shortlist_{run_date.isoformat()}.csv"


def ensure_shortlist_header(path: Path) -> None:
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES).writeheader()


def append_shortlist_row(path: Path, row: dict) -> None:
    """Write one scored job to disk immediately (one open+close per row).
    Every completed job is guaranteed on disk before we move to the next one."""
    flat = dict(row)
    for k in ("top_reasons", "red_flags", "missing_information"):
        if isinstance(flat.get(k), list):
            flat[k] = " | ".join(flat[k])
    # Serialise score_breakdown dict -> compact pipe-separated string for CSV readability
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
    """Return job_url values already in this shortlist file.
    Crash-safe dedup: even if seen_jobs.csv missed a write, we won't
    re-score or double-write a row that's already on disk."""
    return {r["job_url"] for r in read_shortlist_rows(path) if r.get("job_url")}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CareerAI pipeline -- scrape, score, and track job postings.")
    parser.add_argument("--dry-run", action="store_true",
                        help="scrape + filter + stale-check only, skip AI scoring")
    parser.add_argument("--limit", type=int, default=None,
                        help="max number of new jobs to score this run")
    parser.add_argument("--date", type=str, default=None,
                        help="YYYY-MM-DD -- force a specific shortlist date (reruns next morning)")
    parser.add_argument("--no-stale-check", action="store_true",
                        help="skip closed-position detection from previous shortlists")
    args = parser.parse_args()

    cfg = load_config()
    SHORTLIST_DIR.mkdir(exist_ok=True)

    # Determine the shortlist date once at startup (midnight-safe)
    if args.date:
        try:
            run_date = date.fromisoformat(args.date)
        except ValueError:
            sys.exit(f"--date must be YYYY-MM-DD, got: {args.date!r}")
    else:
        run_date = date.today()

    out_path = get_shortlist_path(run_date)

    print("Loading career profile and preferences...")
    career_profile = load_text(cfg["career_profile_path"])
    job_preferences = load_text(cfg["job_preferences_path"])

    print("Loading target companies list...")
    companies_path = cfg.get("target_companies_path", "../profile/target-companies.csv")
    target_companies = load_target_companies(companies_path)
    print(f"  {len(target_companies)} companies loaded from priority list")

    print("Loading history (already-seen and already-applied jobs)...")
    seen = load_seen_job_urls()
    applied = load_applied_urls(cfg.get("tracker_path", "../tracker/applications.csv"))

    # CRASH-SAFE DEDUP: also read URLs already in today's shortlist.
    # Prevents re-scoring + double-writing if seen_jobs.csv missed a write on abrupt kill.
    already_shortlisted = load_shortlist_job_urls(out_path)
    if already_shortlisted:
        print(f"  {len(already_shortlisted)} job(s) already in today's shortlist "
              f"(resume layer -- will not re-score)")

    skip_urls = seen | applied | already_shortlisted

    print("Scraping job boards (this can take a few minutes)...")
    max_retries = cfg.get("max_retries", 2)
    jobs = scrape_all(cfg["searches"], max_retries=max_retries)
    print(f"  {len(jobs)} unique postings scraped across all boards")

    # Build the set of all scraped URLs for stale-position detection
    scraped_urls: set = {j["job_url"] for j in jobs if j.get("job_url")}

    new_jobs = [j for j in jobs if j.get("job_url") and j["job_url"] not in skip_urls]
    print(f"  {len(new_jobs)} are new (not previously seen or applied to)")

    if args.limit:
        new_jobs = new_jobs[: args.limit]

    # -- STALE / CLOSED POSITION DETECTION ----------------------------------
    closed_rows: list[dict] = []
    if not args.no_stale_check:
        print("\nChecking previous shortlists for closed/delisted positions...")
        watchlist = load_all_previous_shortlists(out_path)
        if watchlist:
            closed_rows = find_closed_positions(watchlist, scraped_urls, already_shortlisted)
            if closed_rows:
                print(f"  {len(closed_rows)} previously shortlisted job(s) no longer "
                      f"appear in today's scrape -> will mark CLOSED")
            else:
                print(f"  all {len(watchlist)} previously shortlisted job(s) still active")
        else:
            print("  no previous shortlists found")
    # -----------------------------------------------------------------------

    if args.dry_run:
        print(f"\n-- New jobs (dry-run, not scored) --")
        for j in new_jobs:
            print(f"  [NOT SCORED] {j.get('title')} @ {j.get('company')} -- {j.get('job_url')}")
        if closed_rows:
            print(f"\n-- Closed positions --")
            for r in closed_rows:
                print(f"  [CLOSED] {r.get('title')} @ {r.get('company')} -- {r.get('job_url')}")
    else:
        ensure_shortlist_header(out_path)
        model = cfg.get("ollama_model", "qwen2.5:14b")
        ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")
        # Timeouts: None means wait forever (correct for slow local models).
        # Set ollama_timeout_role / ollama_timeout_company in config.json to an
        # integer (seconds) only if you need a hard cap. 0 or null = no timeout.
        def _parse_timeout(val) -> int | None:
            """Return None for falsy values (0, null/None) meaning no timeout."""
            if val is None or val == 0:
                return None
            return int(val)
        role_timeout    = _parse_timeout(cfg.get("ollama_timeout_role"))
        company_timeout = _parse_timeout(cfg.get("ollama_timeout_company"))

        # -- Write CLOSED rows first (no Ollama needed) ----------------------
        if closed_rows:
            print(f"\nWriting {len(closed_rows)} CLOSED position(s) to today's shortlist...")
            for r in closed_rows:
                closed_row = {
                    "score": r.get("score", ""),
                    "company_score": r.get("company_score", ""),
                    "company_tier": r.get("company_tier", ""),
                    "decision": "CLOSED",
                    "title": r.get("title", ""),
                    "company": r.get("company", ""),
                    "location": r.get("location", ""),
                    "job_url": r.get("job_url", ""),
                    "min_amount": r.get("min_amount", ""),
                    "max_amount": r.get("max_amount", ""),
                    "currency": r.get("currency", ""),
                    "site": r.get("site", ""),
                    "top_reasons": "Position no longer appears in current scrape -- likely filled or delisted.",
                    "red_flags": r.get("red_flags", ""),
                    "missing_information": r.get("missing_information", ""),
                    "company_notes": r.get("company_notes", ""),
                    "score_breakdown": r.get("score_breakdown", ""),
                }
                append_shortlist_row(out_path, closed_row)
                # Mark as seen so we don't keep re-checking this URL
                mark_job_seen(r["job_url"])
        # --------------------------------------------------------------------

        # -- Score new jobs ---------------------------------------------------
        scored_this_run = 0
        if new_jobs:
            print(f"\nScoring {len(new_jobs)} new job(s)...")
        try:
            for i, j in enumerate(new_jobs, 1):
                print(f"  [{i}/{len(new_jobs)}] {j.get('title')} @ {j.get('company')}")
                try:
                    verdict = evaluate_job(
                        j, career_profile, job_preferences, model, ollama_url,
                        timeout=role_timeout)
                except Exception as e:
                    # Don't mark as seen -- leave it to be retried on the next run.
                    print(f"    ! role scoring failed, will retry next run ({e})")
                    continue

                # Company score -- non-fatal; defaults gracefully
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
        # --------------------------------------------------------------------

    # -- Summary digest -------------------------------------------------------
    rows = read_shortlist_rows(out_path)
    min_score = cfg.get("min_score_to_show", 60)

    def score_of(r):
        try:
            return int(r.get("score") or 0)
        except ValueError:
            return 0

    def sort_key(r):
        # CLOSED rows go at the bottom; everything else sorts by score descending
        decision = (r.get("decision") or "").upper()
        return (0 if decision == "CLOSED" else 1, score_of(r))

    rows.sort(key=sort_key, reverse=True)

    print(f"\n{'-'*60}")
    print(f"Today's shortlist: {out_path}")
    print(f"{len(rows)} row(s) total | showing score >= {min_score} and CLOSED\n")

    apply_rows = [r for r in rows if (r.get("decision") or "").upper() == "APPLY"
                  and score_of(r) >= min_score]
    hold_rows  = [r for r in rows if (r.get("decision") or "").upper() == "HOLD"
                  and score_of(r) >= min_score]
    skip_rows  = [r for r in rows if (r.get("decision") or "").upper() == "SKIP"
                  and score_of(r) >= min_score]
    closed_display = [r for r in rows if (r.get("decision") or "").upper() == "CLOSED"]

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
    print_rows("CLOSED -- no longer in today's scrape", closed_display)

    if not any([apply_rows, hold_rows, skip_rows, closed_display]) and not args.dry_run:
        print("  (nothing to show yet)")


if __name__ == "__main__":
    main()
