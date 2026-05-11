import re
import urllib.parse
from app.models import SessionLocal, Job

def run_repair():
    print("🚀 Starting Database LinkedIn Job ID repair and normalization...")
    db = SessionLocal()
    try:
        # Fetch all LinkedIn jobs
        jobs = db.query(Job).filter(Job.platform == "linkedin").all()
        print(f"📊 Found {len(jobs)} total LinkedIn jobs to analyze.")

        cleaned_count = 0
        deleted_duplicates = 0
        no_id_found = 0

        for job in jobs:
            # Standardise regional subdomains inside URL
            if job.url and "linkedin.com" in job.url:
                normalized_url = re.sub(r"https?://[a-z]{2,3}\.linkedin\.com", "https://www.linkedin.com", job.url)
                if job.url != normalized_url:
                    print(f"   🌐 Normalizing URL subdomain for ID {job.id}: {job.url} ➔ {normalized_url}")
                    job.url = normalized_url
                    db.flush()

            # Unquote URL encoding first (e.g. convert %E2%80%93 to actual en-dash characters)
            decoded_url = urllib.parse.unquote(job.url or "")

            # Extract the correct clean numeric ID from URL
            job_id = None
            
            # Pattern 1: Look for trailing hyphen-digits at the end of the URL path before any query string
            # e.g., view/senior-engineer-full-stack-with-ai-ml-at-birdeye-4363914370
            path_part = decoded_url.split("?")[0]
            match = re.search(r'-(\d+)/?$', path_part)
            if match:
                job_id = match.group(1)
            else:
                # Pattern 2: Look for view/ followed directly by digits
                match = re.search(r'view/(\d+)', decoded_url)
                if match:
                    job_id = match.group(1)
                else:
                    # Pattern 3: Look for currentJobId= followed by digits
                    match = re.search(r'currentJobId=(\d+)', decoded_url)
                    if match:
                        job_id = match.group(1)
                    else:
                        # Pattern 4: Fallback to any 9-11 digit sequence
                        match = re.search(r'\b\d{9,11}\b', decoded_url)
                        if match:
                            job_id = match.group(0)

            if not job_id:
                no_id_found += 1
                print(f"   ⚠️ Could not extract Job ID for URL: {job.url}")
                continue

            # Check if another job already exists with this platform and clean job_id
            existing_duplicate = (
                db.query(Job)
                .filter(
                    Job.platform == "linkedin",
                    Job.job_id == job_id,
                    Job.id != job.id
                )
                .first()
            )

            if existing_duplicate:
                # Merge or keep the one with better evaluation score / more description
                print(f"   ⚠️  Duplicate found for clean Job ID: {job_id}")
                score_current = job.match_score or 0
                score_dup = existing_duplicate.match_score or 0
                
                if score_current >= score_dup:
                    print(f"      Keeping Job ID {job.id} (Score: {score_current}%) and removing older duplicate Job ID {existing_duplicate.id} (Score: {score_dup}%)")
                    db.delete(existing_duplicate)
                    # Use db.flush() to make sure the deletion is executed in the database session
                    db.flush()
                    job.job_id = job_id
                    db.flush()
                    cleaned_count += 1
                else:
                    print(f"      Keeping duplicate Job ID {existing_duplicate.id} (Score: {score_dup}%) and removing messy Job ID {job.id} (Score: {score_current}%)")
                    db.delete(job)
                    db.flush()
                    deleted_duplicates += 1
            else:
                # No duplicate, simply update the job_id to the clean version
                old_id = job.job_id
                if old_id != job_id:
                    print(f"   ✨ Updating ID {job.id}: {old_id} ➔ {job_id}")
                    job.job_id = job_id
                    db.flush()
                    cleaned_count += 1

        db.commit()
        print("\n✅ Database Repair Completed successfully!")
        print(f"   🔹 Cleaned/Updated Job IDs: {cleaned_count}")
        print(f"   🔹 Merged/Deleted duplicate entries: {deleted_duplicates}")
        print(f"   🔹 Unresolved (no numeric ID in URL): {no_id_found}")

    except Exception as exc:
        db.rollback()
        print(f"❌ Error during repair: {exc}")
    finally:
        db.close()

if __name__ == "__main__":
    run_repair()
