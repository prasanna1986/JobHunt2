#!/usr/bin/env python3
"""
CareerAI Pipeline — local job discovery + AI scoring
=====================================================

What this does, end to end, with no manual portal-browsing:

 1. FIND    — uses the open-source `python-jobspy` library to pull fresh
              job postings from Naukri, LinkedIn and Indeed in one run.
 2. FILTER  — drops jobs you've already seen or already applied to.
 3. SCORE   — sends each new job, together with your career-profile.md
              and job-preferences.md, to your local Ollama model and asks
              for a structured APPLY / HOLD / SKIP verdict + score.
 4. OUTPUT  — writes a ranked shortlist CSV + prints a digest, so your
              daily routine becomes "open one CSV" instead of "browse
              three portals".

Nothing here submits an application or messages a recruiter. That step
stays yours, on purpose (see the SOP for why).

Requirements
------------
    pip install -U python-jobspy requests

Ollama must already be running locally (`ollama serve`) with a model
pulled (`ollama pull qwen2.5:14b`).

Usage
-----
    python pipeline.py                 # full run
    python pipeline.py --dry-run       # scrape + filter only, skip AI scoring
    python pipeline.py --limit 15      # only score the first 15 new jobs
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


def append_seen_job_urls(urls) -> None:
    is_new = not SEEN_JOBS_PATH.exists()
    with SEEN_JOBS_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["job_url", "date_processed"])
        for u in urls:
            writer.writerow([u, date.today().isoformat()])


def load_applied_urls(tracker_path: str) -> set:
    p = Path(tracker_path)
    if not p.is_absolute():
        p = (HERE / tracker_path).resolve()
    if not p.exists():
        return set()
    with p.open(newline="", encoding="utf-8") as f:
        return {row.get("JobURL", "") for row in csv.DictReader(f)}


def scrape_all(search_configs) -> "list":
    import pandas as pd

    frames = []
    for cfg in search_configs:
        print(f"  scraping: {cfg['search_term']!r} @ {cfg['location']!r} "
              f"on {cfg.get('site_name')}")
        try:
            df = scrape_jobs(
                site_name=cfg.get("site_name", ["naukri", "linkedin", "indeed"]),
                search_term=cfg["search_term"],
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
        return {"decision": "HOLD", "score": 0, "top_reasons": ["Model returned unparseable output — review manually."],
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="scrape + filter only, skip AI scoring")
    parser.add_argument("--limit", type=int, default=None, help="max number of new jobs to score")
    args = parser.parse_args()

    cfg = load_config()
    SHORTLIST_DIR.mkdir(exist_ok=True)

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
    print(f"  {len(new_jobs)} are new")

    if args.limit:
        new_jobs = new_jobs[: args.limit]

    results = []
    processed_urls = []

    if args.dry_run:
        for j in new_jobs:
            results.append({**j, "decision": "NOT_SCORED", "score": None})
    else:
        model = cfg.get("ollama_model", "qwen2.5:14b")
        ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")
        for i, j in enumerate(new_jobs, 1):
            print(f"  scoring {i}/{len(new_jobs)}: {j.get('title')} @ {j.get('company')}")
            verdict = evaluate_job(j, career_profile, job_preferences, model, ollama_url)
            results.append({**j, **verdict})
            processed_urls.append(j["job_url"])

    if not args.dry_run:
        append_seen_job_urls(processed_urls)

    results.sort(key=lambda r: (r.get("score") or 0), reverse=True)

    out_path = SHORTLIST_DIR / f"shortlist_{date.today().isoformat()}.csv"
    fieldnames = ["score", "decision", "title", "company", "location", "job_url",
                  "min_amount", "max_amount", "currency", "site", "top_reasons", "red_flags"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = dict(r)
            for k in ("top_reasons", "red_flags", "missing_information"):
                if isinstance(row.get(k), list):
                    row[k] = " | ".join(row[k])
            writer.writerow(row)

    min_score = cfg.get("min_score_to_show", 60)
    print(f"\nSaved: {out_path}\n")
    print(f"Top matches (score >= {min_score}):\n")
    shown = 0
    for r in results:
        if args.dry_run or (r.get("score") or 0) >= min_score:
            print(f"  [{r.get('score', '-')}] {r.get('decision', '')}: "
                  f"{r.get('title')} @ {r.get('company')} ({r.get('location')})")
            print(f"       {r.get('job_url')}")
            shown += 1
    if shown == 0:
        print("  (nothing cleared the bar today)")


if __name__ == "__main__":
    main()
