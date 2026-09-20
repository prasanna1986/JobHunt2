import csv
from pathlib import Path
import sys

sys.path.append(r"c:\CareerAI\automation")
from pipeline import SHORTLIST_FIELDNAMES

def main():
    backup_dir = Path(r"C:\CareerAI\backup")
    files = sorted(backup_dir.glob("shortlist_*.csv"))
    
    if not files:
        print("No backup files found.")
        return

    jobs = {}
    
    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("job_url")
                if url:
                    # Overwrite with the latest evaluation from the backups
                    jobs[url] = row

    needs_reeval = 0
    for url, row in jobs.items():
        score = str(row.get("company_score", "")).strip()
        
        # If it doesn't have a valid company_score, mark it for reevaluation
        if not score:
            row["decision"] = "REEVALUATE"
            needs_reeval += 1

    master_path = Path(r"c:\CareerAI\automation\shortlist\shortlist.csv")
    master_path.parent.mkdir(exist_ok=True)
    
    with open(master_path, "w", newline="", encoding="utf-8") as f:
        # We explicitly use the full enhanced fieldnames list
        writer = csv.DictWriter(f, fieldnames=SHORTLIST_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in jobs.values():
            writer.writerow(row)
            
    print(f"Restored {len(jobs)} unique jobs to {master_path.name}.")
    print(f"Marked {needs_reeval} jobs for REEVALUATE.")

if __name__ == "__main__":
    main()
