import sys
import json
import csv
import time
from pathlib import Path
import requests

import json_repair
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from fpdf import FPDF

# ---------------------------------------------------------------------------
# Configuration & Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(r"C:\CareerAI")
PROFILE_DIR = BASE_DIR / "profile"
AUTO_DIR = BASE_DIR / "automation"
SHORTLIST_PATH = AUTO_DIR / "shortlist" / "shortlist.csv"
CACHE_PATH = AUTO_DIR / "data" / "jobs_cache.jsonl"
TRACKER_PATH = BASE_DIR / "tracker" / "applications.csv"
APPS_DIR = BASE_DIR / "applications"

# ---------------------------------------------------------------------------
# Ollama Helper
# ---------------------------------------------------------------------------
def call_ollama(model: str, url: str, prompt: str, schema=None, stream=False) -> dict|str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {"temperature": 0.2, "num_predict": 3000}
    }
    if schema:
        payload["format"] = schema
        
    if stream:
        try:
            resp = requests.post(url, json=payload, stream=True)
            resp.raise_for_status()
            full = ""
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    chunk = data.get("response", "")
                    print(chunk, end="", flush=True)
                    full += chunk
                    if data.get("done"):
                        break
            print()
            return full
        except requests.RequestException as e:
            print(f"Ollama streaming error: {e}")
            return ""
    else:
        try:
            resp = requests.post(url, json=payload, timeout=300)
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            if schema:
                # Robust JSON repair in case the model outputs markdown backticks
                start_idx = raw.find('{')
                end_idx = raw.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                    raw = raw[start_idx:end_idx+1]
                return json_repair.loads(raw)
            return raw
        except requests.RequestException as e:
            print(f"Ollama request error: {e}")
            return {}

# ---------------------------------------------------------------------------
# Document Generation
# ---------------------------------------------------------------------------
def generate_docx(resume_data: dict, out_path: Path):
    doc = Document()
    
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)
    
    # Name
    head = doc.add_heading(resume_data.get("name", "Name"), 0)
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Contact
    contact = doc.add_paragraph()
    contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
    contact.add_run(resume_data.get("contact_info", "")).italic = True
    
    # Summary
    doc.add_heading("Professional Summary", level=1)
    doc.add_paragraph(resume_data.get("summary", ""))
    
    # Experience
    doc.add_heading("Professional Experience", level=1)
    for exp in resume_data.get("experience", []):
        p = doc.add_paragraph()
        p.add_run(f"{exp.get('title', '')}").bold = True
        p.add_run(f" | {exp.get('company', '')}")
        p.add_run(f" ({exp.get('dates', '')})").italic = True
        
        for bullet in exp.get("bullets", []):
            doc.add_paragraph(bullet, style='List Bullet')
            
    # Education
    doc.add_heading("Education", level=1)
    for edu in resume_data.get("education", []):
        doc.add_paragraph(f"{edu.get('degree', '')} - {edu.get('institution', '')} ({edu.get('dates', '')})")
        
    # Skills
    doc.add_heading("Skills", level=1)
    doc.add_paragraph(", ".join(resume_data.get("skills", [])))
    
    doc.save(out_path)

def generate_pdf(resume_data: dict, out_path: Path):
    class PDF(FPDF):
        def header(self):
            pass
        def footer(self):
            pass

    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Handle Unicode properly
    pdf.set_font("Helvetica", "B", 16)
    
    # Name
    pdf.cell(0, 10, resume_data.get("name", "Name"), new_x="LMARGIN", new_y="NEXT", align="C")
    
    # Contact
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 8, resume_data.get("contact_info", ""), new_x="LMARGIN", new_y="NEXT", align="C")
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Professional Summary", border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(2)
    pdf.multi_cell(0, 5, resume_data.get("summary", ""))
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Professional Experience", border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    
    for exp in resume_data.get("experience", []):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, f"{exp.get('title', '')} | {exp.get('company', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, f"{exp.get('dates', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for bullet in exp.get("bullets", []):
            pdf.multi_cell(0, 5, f"• {bullet}".encode('latin-1', 'replace').decode('latin-1'))
        pdf.ln(3)
        
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Education", border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(2)
    for edu in resume_data.get("education", []):
        pdf.cell(0, 6, f"{edu.get('degree', '')} - {edu.get('institution', '')} ({edu.get('dates', '')})", new_x="LMARGIN", new_y="NEXT")
        
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Skills", border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(2)
    skills = ", ".join(resume_data.get("skills", []))
    pdf.multi_cell(0, 5, skills.encode('latin-1', 'replace').decode('latin-1'))
    
    pdf.output(out_path)

def build_resume_schema():
    return {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "contact_info": {"type": "string", "description": "Email, Phone, LinkedIn, Location"},
        "summary": {"type": "string", "description": "A tailored 3-4 sentence professional summary"},
        "experience": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "company": {"type": "string"},
              "title": {"type": "string"},
              "dates": {"type": "string"},
              "bullets": {
                "type": "array",
                "items": {"type": "string", "description": "A tailored bullet point"}
              }
            },
            "required": ["company", "title", "dates", "bullets"]
          }
        },
        "education": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "degree": {"type": "string"},
              "institution": {"type": "string"},
              "dates": {"type": "string"}
            }
          }
        },
        "skills": {
          "type": "array",
          "items": {"type": "string"}
        }
      },
      "required": ["name", "contact_info", "summary", "experience", "education", "skills"]
    }

# ---------------------------------------------------------------------------
# Main Routine
# ---------------------------------------------------------------------------
def main():
    if not SHORTLIST_PATH.exists():
        print("Shortlist not found.")
        return
        
    cfg_path = AUTO_DIR / "config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    model = cfg.get("ollama_model", "qwen2.5:14b")
    ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/generate")
    
    profile_text = (PROFILE_DIR / "career-profile.md").read_text("utf-8")
    resume_source = (PROFILE_DIR / "resume-source.txt").read_text("utf-8")
    
    cache = {}
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        job = json.loads(line)
                        cache[job["job_url"]] = job
                    except:
                        pass

    rows = []
    with open(SHORTLIST_PATH, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        
    apply_jobs = [r for r in rows if r.get("decision", "").strip().upper() == "APPLY"]
    if not apply_jobs:
        print("No jobs marked 'APPLY' found in shortlist.")
        return
        
    print(f"Found {len(apply_jobs)} job(s) marked for APPLY.")
    
    for job in apply_jobs:
        print("\n" + "="*80)
        print(f"Title:       {job.get('title')}")
        print(f"Company:     {job.get('company')} (Tier: {job.get('company_tier')})")
        print(f"Score:       Role={job.get('score')} | Company={job.get('company_score')}")
        print(f"Link:        {job.get('job_url')}")
        print(f"Breakdown:   {job.get('score_breakdown')}")
        print(f"Top Reasons: {job.get('top_reasons')}")
        if job.get('red_flags'):
            print(f"Red Flags:   {job.get('red_flags')}")
            
        while True:
            cmd = input("\n[a]pply / [c]hat / [s]kip / [q]uit: ").strip().lower()
            if cmd == 'q':
                print("Exiting.")
                return
            elif cmd == 's':
                print("Skipping to next job.")
                break
            elif cmd == 'c':
                question = input("Ask a question about this job: ")
                jd = cache.get(job.get("job_url"), {}).get("description", "Not found.")
                prompt = f"JOB DESCRIPTION:\n{jd}\n\nUSER PROFILE:\n{profile_text}\n\nQUESTION: {question}\nAnswer the question directly based on the job and profile."
                print("\nAI:")
                call_ollama(model, ollama_url, prompt, stream=True)
                print()
            elif cmd == 'a':
                custom = input("Any extra skills or custom details to emphasize? (Leave blank for default): ")
                job_desc = cache.get(job.get("job_url"), {}).get("description", "")
                
                feedback = ""
                resume_data = None
                
                comp_clean = "".join(c if c.isalnum() else "_" for c in job.get('company', 'Company')).strip('_')
                title_clean = "".join(c if c.isalnum() else "_" for c in job.get('title', 'Role')).strip('_')
                today_str = time.strftime("%Y-%m-%d")
                out_folder = APPS_DIR / f"{today_str}_{comp_clean}_{title_clean}"
                out_folder.mkdir(parents=True, exist_ok=True)
                
                while True:
                    print("\nGenerating tailored resume. Please wait...")
                    schema = build_resume_schema()
                    
                    sys_prompt = "You are an expert executive resume writer formatting an ATS-friendly resume."
                    if feedback:
                        prompt = f"{sys_prompt}\n\nMake the following adjustments to the previous resume generation:\n{feedback}\n\nORIGINAL RESUME:\n{resume_source}\n\nJOB DESCRIPTION:\n{job_desc}"
                    else:
                        prompt = f"{sys_prompt}\n\nRewrite the following resume to strictly align with the job description. Keep facts accurate, but tailor the Summary and Bullet Points to highlight matching skills.\n\nCUSTOM INSTRUCTIONS from user: {custom}\n\nORIGINAL RESUME:\n{resume_source}\n\nJOB DESCRIPTION:\n{job_desc}"
                    
                    resume_data = call_ollama(model, ollama_url, prompt, schema=schema)
                    
                    if not resume_data or not isinstance(resume_data, dict):
                        print("Failed to generate resume data.")
                        break
                        
                    docx_path = out_folder / f"Resume_{comp_clean}.docx"
                    pdf_path = out_folder / f"Resume_{comp_clean}.pdf"
                    
                    try:
                        generate_docx(resume_data, docx_path)
                        generate_pdf(resume_data, pdf_path)
                        print(f"\nGenerated!")
                        print(f"DOCX: {docx_path}")
                        print(f"PDF:  {pdf_path}")
                    except Exception as e:
                        print(f"Error rendering document: {e}")
                        break
                        
                    print("\nPlease open the generated files and review them.")
                    rev_cmd = input("Are you happy with this resume? [a]ccept / [e]dit / [c]ancel: ").strip().lower()
                    if rev_cmd == 'a':
                        did_apply = input("Did you submit the application on the company portal? (y/n): ").strip().lower()
                        if did_apply == 'y':
                            # Ensure applications.csv exists and has headers
                            tracker_exists = TRACKER_PATH.exists()
                            with open(TRACKER_PATH, "a", newline="", encoding="utf-8") as tf:
                                writer = csv.writer(tf)
                                if not tracker_exists:
                                    writer.writerow(["Date", "Company", "Title", "URL", "Status"])
                                writer.writerow([time.strftime("%Y-%m-%d"), job.get("company"), job.get("title"), job.get("job_url"), "APPLIED"])
                            
                            # Update shortlist
                            for r in rows:
                                if r.get("job_url") == job.get("job_url"):
                                    r["decision"] = "APPLIED"
                                    
                            with open(SHORTLIST_PATH, "w", newline="", encoding="utf-8") as sf:
                                swriter = csv.DictWriter(sf, fieldnames=rows[0].keys(), extrasaction="ignore")
                                swriter.writeheader()
                                swriter.writerows(rows)
                            print("Application logged successfully!")
                        break
                    elif rev_cmd == 'e':
                        feedback = input("What would you like to change? (e.g. 'make the summary shorter'): ")
                    else:
                        print("Cancelled generation.")
                        break
                break

if __name__ == "__main__":
    main()
