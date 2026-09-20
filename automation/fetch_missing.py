import csv
import json
import time
import random
from pathlib import Path
import requests
from bs4 import BeautifulSoup

def main():
    shortlist_path = Path(r"C:\CareerAI\automation\shortlist\shortlist.csv")
    cache_path = Path(r"C:\CareerAI\automation\data\jobs_cache.jsonl")
    
    # 1. Load existing cache
    cache = set()
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        job = json.loads(line)
                        if job.get("job_url"):
                            cache.add(job["job_url"])
                    except json.JSONDecodeError:
                        continue
                        
    # 2. Read shortlist and find missing
    rows = []
    missing_urls = []
    with open(shortlist_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            url = row.get("job_url")
            score = str(row.get("company_score", "")).strip()
            # If the job lacks a company score AND isn't in cache, we try to fetch it
            if not score and url and url not in cache:
                missing_urls.append(row)
            rows.append(row)
            
    if not missing_urls:
        print("No missing jobs found to scrape.")
        return
        
    print(f"Attempting to fetch descriptions for {len(missing_urls)} jobs...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    }
    
    success_count = 0
    with open(cache_path, "a", encoding="utf-8") as cache_file:
        for i, row in enumerate(missing_urls):
            url = row["job_url"]
            print(f"[{i+1}/{len(missing_urls)}] Fetching: {url}")
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    text = soup.get_text(separator="\n", strip=True)
                    
                    if "no longer accepting applications" in text.lower() or len(text) < 200:
                        print("  -> Job appears closed or blocked by anti-bot.")
                    else:
                        job_dict = {
                            "job_url": url,
                            "title": row.get("title", ""),
                            "company": row.get("company", ""),
                            "location": row.get("location", ""),
                            "description": text[:3500]
                        }
                        cache_file.write(json.dumps(job_dict) + "\n")
                        cache.add(url)
                        
                        # Mark it back to REEVALUATE so pipeline picks it up
                        row["decision"] = "REEVALUATE"
                        success_count += 1
                        print("  -> Success!")
                elif resp.status_code == 429:
                    print("  -> Rate limited by job board! Stopping here to avoid IP ban.")
                    break
                else:
                    print(f"  -> Failed (HTTP {resp.status_code})")
            except Exception as e:
                print(f"  -> Error: {e}")
                
            # Random delay to behave like a human
            time.sleep(random.uniform(2.0, 4.0))
            
    # Rewrite the shortlist to update the decision flags for the ones we successfully fetched
    with open(shortlist_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
            
    print(f"\nDone! Successfully fetched and cached {success_count} job descriptions.")
    if success_count > 0:
        print("You can now run `python pipeline.py --sync-csv` to reevaluate the recovered ones!")

if __name__ == "__main__":
    main()
