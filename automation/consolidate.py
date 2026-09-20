import csv
from pathlib import Path
import os

def main():
    shortlist_dir = Path(r"C:\CareerAI\automation\shortlist")
    files = sorted(shortlist_dir.glob("shortlist_2*.csv"))
    
    if not files:
        print("No dated shortlist files found to consolidate.")
        return

    jobs = {}
    fieldnames = []
    
    print(f"Found {len(files)} files to consolidate:")
    for fpath in files:
        print(f"  - {fpath.name}")
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not fieldnames:
                fieldnames = reader.fieldnames
            
            for row in reader:
                url = row.get("job_url")
                if url:
                    jobs[url] = row # Later files overwrite earlier files due to sorting

    master_path = shortlist_dir / "shortlist.csv"
    print(f"\nWriting {len(jobs)} unique jobs to {master_path.name}...")
    with open(master_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in jobs.values():
            writer.writerow(row)
            
    print("Deleting old dated csv files...")
    for fpath in files:
        fpath.unlink()
        
    print("Consolidation complete.")

if __name__ == "__main__":
    main()
