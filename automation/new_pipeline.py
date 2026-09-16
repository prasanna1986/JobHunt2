#!/usr/bin/env python3
"""
CareerAI Pipeline — local job discovery + AI scoring
=====================================================

What this does, end to end, with no manual portal-browsing:

 1. FIND    — uses the open-source `python-jobspy` library to pull fresh
              job postings from LinkedIn and Indeed in one run.
 2. FILTER  — drops jobs you've already seen or already applied to.
 3. SCORE   — sends each new job, together with your career-profile.md
              and job-preferences.md, to your local Ollama model and asks
              for a structured APPLY / HOLD / SKIP verdict + score.
 4. OUTPUT  — writes a shortlist CSV + prints a digest, so your daily
              routine becomes "open one CSV" instead of "browse job boards".

Nothing here submits an application or messages a recruiter. That step
stays yours, on purpose (see the SOP for why).

RESUMABLE BY DESIGN
--------------------
Every job's row is written to today's shortlist CSV, and its URL is marked
"seen", IMMEDIATELY after it is scored — not in one batch at the end. If the
script crashes, loses network, or you Ctrl+C it partway through, nothing
already-scored is lost. Just run the exact same command again: already-seen
jobs are skipped instantly and it picks up with whatever's left.

Requirements
------------
    pip install -U python-jobspy requests

Ollama must already be running locally (`ollama serve`) with a model
pulled (`ollama pull qwen2.5:14b`).

Usage
-----
    python pipeline.py                 # full run (resumes automatically)
    python pipeline.py --dry-run       # scrape + filter only, skip AI scoring
    python pipeline.py --limit 15      # only score the next 15 new jobs
"""

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

try:
    from jobspy import scrape_jobs
except ImportError:
    sys.exit("python-jobspy is not installed. Run: pip install -U python-jobspy")

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_JOBS_PATH = HERE / "seen_jobs.csv"
SHORTLIST_DIR = HERE / "shortlist"

SHORTLIST_FIELDNAMES = ["score", "decision", "title", "company", "location", "job_url",
                         "min_amount", "max_amount", "currency", "site",
                         "top_reasons", "red_flags", "missing_information"]

EVAL_INSTRUCTIONS = """You are evaluating a job for a candidate. Use ONLY the
CAREER PROFILE and JOB PREFERENCES provided below as ground truth about the
candidate. Use ONLY the JOB POSTING text for facts about the job. Never invent
salary, culture information or requirements — if something is not stated,
mark it UNKNOWN.

Evaluate in this order: (1) location/remote fit against the stated priority,
(2) seniority fit, (3) required skills fit, (4) leadership/architecture fit,
(5) compensation attractiveness if stated, (6) any visible company-quality
signal, (7) career growth signal, (8) risks or missing information.

Respond with ONLY a single JSON object, no prose, no markdown fences, in
exactly this shape:

{
  "decision": "APPLY" | "HOLD" | "SKIP",
  "score": <integer 0-100>,
  "location_fit": "PASS" | "FAIL" | "UNKNOWN",
  "compensation": "GOOD" | "ACCEPTABLE" | "POOR" | "UNKNOWN",
  "role_fit": "STRONG" | "MODERATE" | "WEAK",
  "top_reasons": ["...", "...", "..."],
  "red_flags": ["..."],
  "missing_information": ["..."]
}
"""


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


def load_seen_job_urls() -> set:
    if not SEEN_JOBS_PATH.exists():
        return set()
    with SEEN_JOBS_PATH.open(newline="", encoding="utf-8") as f:
        return {row["job_url"] for row in csv.DictReader(f) if row.get("job_url")}


def mark_job_seen(url: str) -> None:
    """Append one URL to seen_jobs.csv immediately — called right after that
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


def scrape_all(search_configs) -> list:
    import pandas as pd

    frames = []
    for cfg in search_configs:
        print(f"  scraping: {cfg['search_term']!r} @ {cfg['location']!r} "
              f"on {cfg.get('site_name')}")
        try:
            df = scrape_jobs(
                site_name=cfg.get("site_name", ["linkedin", "indeed"]),
                search_term=cfg["search_term"],
                google_search_term=cfg.get("google_search_term"),  # only used if "google" is in site_name
                location=cfg["location"],
                results_wanted=cfg.get("results_wanted", 20),
                hours_old=cfg.get("hours_old", 72),
                country_indeed=cfg.get("country_indeed", "India"),
                is_remote=cfg.get("is_remote", False),
                linkedin_fetch_description=True,
            )
            frames.append(df)
        except Exception as e:  # a single bad search shouldn't kill the run
            print(f"    ! skipped ({e})")
        time.sleep(2)  # be polite to the job boards between searches

    if not frames:
        return []
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["job_url"])
    return combined.to_dict(orient="records")


def call_ollama(model: str, url: str, prompt: str) -> dict:
    resp = requests.post(
        url,
        json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
        timeout=180,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"decision": "HOLD", "score": 0,
                "top_reasons": ["Model returned unparseable output — review manually."],
                "red_flags": [], "missing_information": [], "location_fit": "UNKNOWN",
                "compensation": "UNKNOWN", "role_fit": "MODERATE"}


def evaluate_job(job: dict, career_profile: str, job_preferences: str, model: str, ollama_url: str) -> dict:
    description = (job.get("description") or "")[:3000]
    prompt = (
        f"{EVAL_INSTRUCTIONS}\n\n"
        f"JOB POSTING:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nSalary info: min={job.get('min_amount')} "
        f"max={job.get('max_amount')} currency={job.get('currency')}\n"
        f"Description:\n{description}\n\n"
        f"CAREER PROFILE:\n{career_profile}\n\n"
        f"JOB PREFERENCES:\n{job_preferences}\n"
    )
    return call_ollama(model, ollama_url, prompt)


def get_shortlist_path() -> Path:
    return SHORTLIST_DIR / f"shortlist_{date.today().isoformat()}.csv"


def ensure_shortlist_header(path: Path) -> None:
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES).writeheader()


def append_shortlist_row(path: Path, row: dict) -> None:
    """Write one scored job to disk right away. Opening + closing the file
    per row is deliberately simple (not batched) so every completed job is
    guaranteed to be on disk before we ever move on to the next one."""
    flat = dict(row)
    for k in ("top_reasons", "red_flags", "missing_information"):
        if isinstance(flat.get(k), list):
            flat[k] = " | ".join(flat[k])
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES, extrasaction="ignore")
        writer.writerow(flat)


def read_shortlist_rows(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="scrape + filter only, skip AI scoring")
    parser.add_argument("--limit", type=int, default=None, help="max number of new jobs to score this run")
    args = parser.parse_args()

    cfg = load_config()
    SHORTLIST_DIR.mkdir(exist_ok=True)
    out_path = get_shortlist_path()

    print("Loading career profile and preferences...")
    career_profile = load_text(cfg["career_profile_path"])
    job_preferences = load_text(cfg["job_preferences_path"])

    print("Loading history (already-seen and already-applied jobs)...")
    seen = load_seen_job_urls()
    applied = load_applied_urls(cfg.get("tracker_path", "../tracker/applications.csv"))
    skip_urls = seen | applied

    print("Scraping job boards (this can take a minute or two)...")
    jobs = scrape_all(cfg["searches"])
    print(f"  found {len(jobs)} postings before dedup")

    new_jobs = [j for j in jobs if j.get("job_url") and j["job_url"] not in skip_urls]
    print(f"  {len(new_jobs)} are new (not previously seen or applied to)")

    if args.limit:
        new_jobs = new_jobs[: args.limit]

    if args.dry_run:
        for j in new_jobs:
            print(f"  [NOT SCORED] {j.get('title')} @ {j.get('company')} — {j.get('job_url')}")
    else:
        ensure_shortlist_header(out_path)
        model = cfg.get("ollama_model", "qwen2.5:14b")
        ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")
        scored_this_run = 0
        try:
            for i, j in enumerate(new_jobs, 1):
                print(f"  scoring {i}/{len(new_jobs)}: {j.get('title')} @ {j.get('company')}")
                try:
                    verdict = evaluate_job(j, career_profile, job_preferences, model, ollama_url)
                except Exception as e:
                    # Don't mark as seen — leave it to be retried on the next run.
                    print(f"    ! scoring failed, will retry next run ({e})")
                    continue
                append_shortlist_row(out_path, {**j, **verdict})
                mark_job_seen(j["job_url"])
                scored_this_run += 1
        except KeyboardInterrupt:
            print(f"\nStopped early. {scored_this_run} job(s) were scored and saved "
                  f"to {out_path.name} before you stopped.")
            print("Run the same command again to continue with the rest — nothing is lost.")
            sys.exit(0)

    # Whether this run scored 0, some, or all of today's new jobs, show
    # everything accumulated in today's file so far.
    rows = read_shortlist_rows(out_path)
    min_score = cfg.get("min_score_to_show", 60)

    def score_of(r):
        try:
            return int(r.get("score") or 0)
        except ValueError:
            return 0

    rows.sort(key=score_of, reverse=True)

    print(f"\nToday's shortlist so far: {out_path}")
    print(f"({len(rows)} job(s) scored today; showing score >= {min_score})\n")
    shown = 0
    for r in rows:
        if args.dry_run or score_of(r) >= min_score:
            print(f"  [{r.get('score', '-')}] {r.get('decision', '')}: "
                  f"{r.get('title')} @ {r.get('company')} ({r.get('location')})")
            print(f"       {r.get('job_url')}")
            shown += 1
    if shown == 0 and not args.dry_run:
        print("  (nothing cleared the bar yet today)")


if __name__ == "__main__":
    main()
