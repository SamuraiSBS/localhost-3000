import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("probe", ROOT / "infra/probe/server.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), probe.Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def test_page(self):
        with urllib.request.urlopen(self.url, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("localhost-3000 — Infrastructure Ready", response.read().decode())

    def test_health_and_revision(self):
        with urllib.request.urlopen(self.url + "/healthz?probe=1", timeout=3) as response:
            self.assertEqual(json.load(response), {"status": "ok", "revision": probe.REVISION})

    def test_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url + "/missing", timeout=3)
        self.assertEqual(ctx.exception.code, 404)

    def test_head(self):
        request = urllib.request.Request(self.url, method="HEAD")
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.read(), b"")
            self.assertGreater(int(response.headers["Content-Length"]), 0)


if __name__ == "__main__":
    unittest.main()
