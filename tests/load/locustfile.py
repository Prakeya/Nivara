"""
Locust Load Test: Nivara API

Run: locust -f tests/load/locustfile.py --host=http://localhost:8000
"""

from locust import HttpUser, task, between


class NivaraUser(HttpUser):
    """Simulates a user interacting with the Nivara API."""
    wait_time = between(1, 3)

    @task(5)
    def health_check(self):
        self.client.get("/health")

    @task(3)
    def get_jobs(self):
        self.client.get("/v1/jobs")

    @task(3)
    def get_metrics(self):
        self.client.get("/metrics")

    @task(2)
    def get_pending_reviews(self):
        self.client.get("/api/review/pending")

    @task(1)
    def get_status_unknown(self):
        self.client.get("/status/00000000-0000-0000-0000-000000000000")
