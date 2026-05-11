import re
import requests
import json
from typing import List, Dict, Any, Optional

class NaukriApplyService:
    def __init__(self, headers: Dict[str, str]):
        self.headers = headers

    def extract_job_id(self, url: str) -> Optional[str]:
        """Extracts Naukri jobId from URL."""
        # Pattern: ...-digits? or ...-digits
        match = re.search(r'-(\d{10,15})(?:\?|$)', url)
        if match:
            return match.group(1)
        return None

    def trigger_apply(self, job_url: str) -> Dict[str, Any]:
        """Calls the apply-workflow API to get screening questions."""
        job_id = self.extract_job_id(job_url)
        if not job_id:
            return {"error": "Could not extract Job ID from URL"}

        url = "https://www.naukri.com/cloudgateway-workflow/workflow-services/apply-workflow/v1/apply"
        
        # Extracted from user's curl
        payload = {
            "strJobsarr": [job_id],
            "logstr": f"--drecomm_profile-3-F-0-1--", # Simplified
            "flowtype": "show",
            "crossdomain": True,
            "jquery": 1,
            "chatBotSDK": True,
            "applyTypeId": "107",
            "closebtn": "y"
        }

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"Apply trigger failed with status {response.status_code}", "detail": response.text}
        except Exception as e:
            return {"error": f"Request failed: {str(e)}"}

    def submit_answer(self, job_id: str, answer_text: str, conversation_id: str) -> Dict[str, Any]:
        """Submits an answer to a screening question via the chatbot respond API."""
        url = "https://www.naukri.com/cloudgateway-chatbot/chatbot-services/botapi/v5/respond"
        
        payload = {
            "input": {
                "text": [answer_text],
                "id": ["-1"]
            },
            "appName": f"{job_id}_apply",
            "domain": "Naukri",
            "conversation": f"{job_id}_apply", # Often same as appName or conversation_session_id
            "channel": "web",
            "status": "Returning",
            "deviceType": "WEB"
        }

        # Some endpoints might need the conversation_session_id specifically
        # if conversation_id:
        #     payload["conversation"] = conversation_id

        try:
            # Note: For respond API, we usually need the 'Bearer' token in Authorization
            resp_headers = self.headers.copy()
            if "Authorization" in resp_headers and "ACCESSTOKEN =" in resp_headers["Authorization"]:
                # Swap to Bearer for chatbot API if needed
                token = resp_headers["Authorization"].replace("ACCESSTOKEN = ", "")
                resp_headers["Authorization"] = f"Bearer {token}"

            response = requests.post(url, headers=resp_headers, json=payload, timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"Submit failed with status {response.status_code}", "detail": response.text}
        except Exception as e:
            return {"error": f"Submit failed: {str(e)}"}
