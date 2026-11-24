"""Bounded, real BM25 retrieval load. Requires a short-lived, authorized token."""

import os

from locust import HttpUser, between, task


class RetrievalUser(HttpUser):
    wait_time = between(0.1, 0.3)

    @task
    def search(self):
        self.client.post(
            "/api/knowledge-bases/" + os.environ["LOAD_KB"] + "/search",
            headers={"Authorization": "Bearer " + os.environ["LOAD_TOKEN"]},
            json={"query": "住宿费用按什么标准报销？"},
            name="authorized BM25",
            timeout=15,
        )
