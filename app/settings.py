import os
import yaml
from pydantic_settings import BaseSettings


def load_yaml_settings() -> dict:
    yaml_path = "Job_Automation/config.yaml"
    if not os.path.exists(yaml_path):
        # Try relative to setting's parent
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        yaml_path = os.path.join(base_dir, "Job_Automation", "config.yaml")

    if not os.path.exists(yaml_path):
        return {}

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        settings_dict = {}
        # Map search parameters
        search = data.get("search", {})
        if "platform" in search:
            settings_dict["platform"] = search["platform"]
        if "titles" in search:
            settings_dict["job_titles"] = search["titles"]
        if "locations" in search:
            settings_dict["locations"] = search["locations"]
        if "max_jobs" in search:
            settings_dict["max_jobs"] = search["max_jobs"]
        if "past_24_hours" in search:
            settings_dict["past_24_hours"] = search["past_24_hours"]
        if "headless" in search:
            settings_dict["headless"] = bool(search["headless"])
        if "page_size" in search:
            settings_dict["page_size"] = int(search["page_size"])

        # Map evaluation parameters
        evaluation = data.get("evaluation", {})
        if "llm_provider" in evaluation:
            settings_dict["llm_provider"] = evaluation["llm_provider"]
        if "threshold" in evaluation:
            settings_dict["threshold"] = evaluation["threshold"]
        if "base_resume_path" in evaluation:
            settings_dict["base_resume_path"] = evaluation["base_resume_path"]
        if "output_path" in evaluation:
            settings_dict["output_path"] = evaluation["output_path"]

        # Map profile parameters
        profile = data.get("profile", {})
        if "linkedin" in profile:
            settings_dict["linkedin_url"] = profile["linkedin"]
        if "github" in profile:
            settings_dict["github_url"] = profile["github"]
        if "portfolio" in profile:
            settings_dict["portfolio_url"] = profile["portfolio"]
        if "profile_text" in profile:
            settings_dict["profile_text"] = profile["profile_text"]

        return settings_dict
    except Exception as e:
        print(f"Error loading config.yaml: {e}")
        return {}


class Settings(BaseSettings):
    database_url: str = "sqlite:///./jobs.db"
    page_size: int = 20

    # Search Settings
    platform: str = "linkedin"
    job_titles: list[str] = [
        "Software Engineer", "Backend Engineer", "Full Stack Engineer",
        "Python Developer", "AI Engineer", "ML Engineer",
        "Senior Software Engineer", "Lead Engineer", "Tech Lead",
    ]
    locations: list[str] = ["Remote", "Jaipur", "India"]
    max_jobs: int = 300
    past_24_hours: bool = True
    headless: bool = True

    # Evaluation Settings
    llm_provider: str = "groq"
    threshold: int = 50
    base_resume_path: str = "./resume.pdf"
    output_path: str = "./output"
    
    # Candidate Profile
    linkedin_url: str = "https://www.linkedin.com/in/kartik-lohar"
    github_url: str = "https://github.com/Kartik-Lohar"
    portfolio_url: str = "https://codebasics.io/portfolio/Kartik-Lohar"
    profile_text: str = """
    3 years of professional experience transitioning from software testing
    and QA into Data Science and Machine Learning. Proficient in Python,
    pandas, NumPy, scikit-learn, SQL, and automated testing with Selenium.
    Completed certifications in Data Science and Machine Learning.
    Familiar with ML model evaluation, feature engineering, and basic NLP.
    Seeking Data Scientist, Machine Learning Engineer, or ML Analyst roles.
    Open to remote and hybrid positions in Jaipur, Gurugram, or Hyderabad.
    """

    # Legacy/Dashboard specific
    skills: list[str] = [
        "Python", "FastAPI", "Django", "Flask",
        "JavaScript", "TypeScript", "React", "Next.js",
        "Node.js", "PostgreSQL", "SQLAlchemy", "LangChain",
        "LangGraph", "OpenAI", "LLM", "AI", "Machine Learning",
        "Docker", "Kubernetes", "AWS", "GCP", "REST API",
        "GraphQL", "Redis", "Celery", "Go", "PHP",
    ]
    min_salary: int = 0
    max_experience_years: int = 10
    name: str = "Puneet Sharma"
    remote_preferred: bool = True

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def __init__(self, **values):
        yaml_data = load_yaml_settings()
        # Merge: passed values override YAML, which overrides defaults/env
        for k, v in yaml_data.items():
            if k not in values:
                values[k] = v
        super().__init__(**values)


settings = Settings()
