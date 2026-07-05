import os
import sys
import unittest

# Ensure the root of the project is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from fastapi.testclient import TestClient
from backend.app.main import app


class TestSalesIntelligenceAPI(unittest.TestCase):
    
    def test_health_check(self):
        """Test health check route."""
        with TestClient(app) as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("status", data)
            self.assertIn("database", data)
            self.assertIn("api_keys", data)
            print("[OK] Health check endpoint verified successfully.")

    def test_enrichment_flow_without_auth(self):
        """Test enrichment endpoints without login or signup."""
        with TestClient(app) as client:
            enrich_payload = {
                "companies": ["Siemens Energy", "Vestas Wind Systems"],
                "quick_mode": True
            }

            resp_enrich = client.post("/api/enrich", json=enrich_payload)
            self.assertEqual(resp_enrich.status_code, 202)
            job_data = resp_enrich.json()
            self.assertIn("job_id", job_data)
            self.assertEqual(job_data["status"], "pending")

            job_id = job_data["job_id"]
            print(f"[OK] Lead enrichment job dispatched successfully (Job ID: {job_id}).")

            resp_status = client.get(f"/api/enrich/{job_id}")
            self.assertEqual(resp_status.status_code, 200)
            status_data = resp_status.json()
            self.assertEqual(status_data["job_id"], job_id)
            self.assertIn(status_data["status"], ["pending", "running", "completed"])
            print("[OK] Job status polling verified successfully.")

            resp_cancel = client.get(f"/api/enrich/{job_id}/cancel")
            self.assertIn(resp_cancel.status_code, [200, 400])
            print("[OK] Job cancellation dispatch verified successfully.")

            resp_history = client.get("/api/enrich/history/list")
            self.assertEqual(resp_history.status_code, 200)
            history_data = resp_history.json()
            self.assertGreaterEqual(history_data["total_jobs"], 1)
            print("[OK] Historic jobs listing verified successfully.")

            resp_leads = client.get("/api/enrich/leads/all")
            self.assertEqual(resp_leads.status_code, 200)
            print("[OK] Active leads search listings verified successfully.")


if __name__ == "__main__":
    unittest.main()
