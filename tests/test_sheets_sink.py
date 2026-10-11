"""Google Sheets row-splitting tests with a fake HTTP session."""
import unittest

from sheets_sink import GoogleSheetsSink, HEADERS


class FakeResponse:
    def __init__(self, body=None):
        self.body = body or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class FakeSession:
    def __init__(self):
        self.get_urls = []
        self.posts = []

    def get(self, url, **kwargs):
        self.get_urls.append(url)
        return FakeResponse({"values": []})

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return FakeResponse({"updates": {"updatedRows": len(kwargs["json"]["values"])}})


class SheetsSinkTests(unittest.TestCase):
    def test_mine_and_worker_rows_use_separate_worksheets(self):
        session = FakeSession()
        sink = GoogleSheetsSink("sheet-id", session)
        sink.append_records([
            {"node_type": "mine", "node_id": "m1", "boot_id": "b", "seq": 1,
             "payload": {"mq5_raw": 400, "methane_pct": None, "calibration": {}}},
            {"node_type": "worker", "node_id": "w1", "boot_id": "b", "seq": 2,
             "payload": {"heart_rate_bpm": 80, "work_status": "good_to_work"}},
        ])
        posted_urls = [post["url"] for post in session.posts]
        self.assertTrue(any("mine_data" in url for url in posted_urls))
        self.assertTrue(any("worker_private" in url for url in posted_urls))
        self.assertEqual(session.posts[0]["json"]["values"][0], HEADERS)
        row_json = " ".join(str(post["json"]["values"]) for post in session.posts)
        self.assertIn("mq5_raw", row_json)
        self.assertIn("heart_rate_bpm", row_json)


if __name__ == "__main__":
    unittest.main()
