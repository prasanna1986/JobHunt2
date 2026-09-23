import sys
import json
from pathlib import Path

def main():
    if len(sys.argv) < 5:
        print("Usage: python inject_jd.py <path_to_jd_txt_file> <job_url> <title> <company> [location]")
        print("Example: python inject_jd.py jd.txt \"https://naukri.com/...\" \"AI Architect\" \"Zoho\" \"Chennai\"")
        sys.exit(1)
        
    jd_path = Path(sys.argv[1])
    job_url = sys.argv[2]
    title = sys.argv[3]
    company = sys.argv[4]
    location = sys.argv[5] if len(sys.argv) > 5 else "Chennai, Tamil Nadu"
    
    if not jd_path.exists():
        print(f"Error: File not found: {jd_path}")
        sys.exit(1)
        
    # Read the text file (assuming utf-8, falling back if needed)
    with open(jd_path, "r", encoding="utf-8", errors="ignore") as f:
        description = f.read().strip()
        
    if not description:
        print("Error: The JD file is empty.")
        sys.exit(1)
        
    job_obj = {
        "job_url": job_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description[:3500] # pipeline truncates at 3500 anyway
    }
    
    # Append to pipeline data files
    cache_path = Path(r"C:\CareerAI\automation\data\jobs_cache.jsonl")
    pending_path = Path(r"C:\CareerAI\automation\data\pending_eval.jsonl")
    
    for p in [cache_path, pending_path]:
        # Create directory if it doesn't exist
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(job_obj) + "\n")
            
    print(f"✅ Successfully injected '{title}' at '{company}' into the evaluation queue.")
    print("➡️ Run `python pipeline.py --step evaluate` to score it and see it in your shortlist!")

if __name__ == "__main__":
    main()
