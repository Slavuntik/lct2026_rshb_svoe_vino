"""Contract and failure tests using the actual organizer shell script + a local HTTP server."""
import argparse
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("eval_detector", Path(__file__).with_name("eval_detector.py"))
eval_detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_detector)


class EvalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "dataset with spaces"
        (self.data / "queries").mkdir(parents=True)
        (self.data / "queries" / "label one.jpg").write_bytes(b"synthetic fixture, no model")
        self.manifest = self.data / "queries.tsv"
        self.manifest.write_text("query_id\timage_path\nq-1\tlabel one.jpg\n", encoding="utf-8")
        self.status = 200
        self.response = {"slug": "fixture-wine"}
        self.health = {"status": "ok", "warm": True, "cv_index_version": "fixture-real-index"}
        self.posts = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(owner.health).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.posts.append(body)
                self.send_response(owner.status)
                self.end_headers()
                payload = owner.response if isinstance(owner.response, bytes) else json.dumps(owner.response).encode()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.args = argparse.Namespace(dataset=str(self.data), images_dir=None, output=str(self.root / "result.jsonl"), report=None, eval_script=str(eval_detector.SCRIPT),
                                       endpoint=f"http://127.0.0.1:{server.server_port}/v1/eval/predict")

    def test_folder_recursive_default_report_and_order(self):
        nested = self.data / "queries" / "nested"
        nested.mkdir()
        (nested / "LABEL.PNG").write_bytes(b"second image")
        (nested / "readme.txt").write_text("not an image")
        self.args.dataset = None
        self.args.images_dir = str(self.data / "queries")
        self.assertEqual(eval_detector.run(self.args), 0)
        report = json.loads((self.root / "result.report.json").read_text())
        self.assertEqual(report["input_mode"], "folder")
        self.assertEqual(report["summary"]["images"], 2)
        self.assertEqual([row["image_path"] for row in report["predictions"]], ["label one.jpg", "nested/LABEL.PNG"])
        self.assertEqual(len(self.posts), 2)
        self.assertFalse((self.data / "queries" / "queries.tsv").exists())

    def test_empty_folder_rejected(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.args.dataset = None
        self.args.images_dir = str(empty)
        with self.assertRaisesRegex(ValueError, "нет поддерживаемых"):
            eval_detector.run(self.args)

    def test_folder_symlink_escape_rejected(self):
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"outside")
        (self.data / "queries" / "escape.jpg").symlink_to(outside)
        self.args.dataset = None
        self.args.images_dir = str(self.data / "queries")
        with self.assertRaises(ValueError):
            eval_detector.run(self.args)

    def test_actual_script_multipart_hash_and_file_only(self):
        self.assertEqual(eval_detector.run(self.args), 0)
        rows = [json.loads(line) for line in Path(self.args.output).read_text().splitlines()]
        self.assertEqual(rows[0]["predicted_slug"], "fixture-wine")
        self.assertEqual(rows[0]["image_sha256"], eval_detector.sha256(self.data / "queries" / "label one.jpg"))
        self.assertIn(b'name="image"', self.posts[0])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["dataset with spaces", "result.jsonl"])

    def test_array_response_and_optional_report(self):
        self.response = [{"slug": "fixture-wine"}]
        self.args.report = str(self.root / "summary.json")
        self.assertEqual(eval_detector.run(self.args), 0)
        summary = json.loads(Path(self.args.report).read_text())
        self.assertIsNone(summary["summary"]["accuracy"])
        self.assertEqual(summary["summary"]["predicted"], 1)
        self.assertEqual(summary["predictions_sha256"], eval_detector.sha256(Path(self.args.output)))

    def test_http_error_is_not_silent_success_and_keeps_result(self):
        self.status = 502
        self.response = b"<html>bad gateway</html>"
        self.assertEqual(eval_detector.run(self.args), 2)
        self.assertIsNone(json.loads(Path(self.args.output).read_text())["predicted_slug"])

    def test_malformed_success_response_is_not_success(self):
        self.response = b"<html>not json</html>"
        self.assertEqual(eval_detector.run(self.args), 2)

    def test_existing_output_never_overwritten(self):
        Path(self.args.output).write_text("keep")
        with self.assertRaisesRegex(ValueError, "уже существует"):
            eval_detector.run(self.args)
        self.assertEqual(Path(self.args.output).read_text(), "keep")
        self.assertEqual(self.posts, [])

    def test_invalid_dataset_fails_before_health_or_inference(self):
        for content in ["wrong\theader\n", "query_id\timage_path\n", "query_id\timage_path\nq-1\tmissing.jpg\n",
                        "query_id\timage_path\nq-1\tlabel one.jpg\nq-1\tlabel one.jpg\n",
                        "query_id\timage_path\nq-1\t../../private.jpg\n"]:
            with self.subTest(content=content), patch.object(eval_detector, "service_health") as health:
                self.manifest.write_text(content)
                with self.assertRaises(ValueError):
                    eval_detector.run(self.args)
                health.assert_not_called()

    def test_symlink_escape_rejected(self):
        (self.root / "outside.jpg").write_bytes(b"outside")
        (self.data / "queries" / "escape.jpg").symlink_to(self.root / "outside.jpg")
        self.manifest.write_text("query_id\timage_path\nq-1\tescape.jpg\n")
        with self.assertRaises(ValueError):
            eval_detector.run(self.args)
        self.assertEqual(self.posts, [])

    def test_checksum_mismatch_rejected(self):
        (self.data / "checksums.sha256").write_text("0" * 64 + "  queries/label one.jpg\n")
        with self.assertRaisesRegex(ValueError, "контрольная сумма"):
            eval_detector.run(self.args)

    def test_mock_or_cold_cv_rejected(self):
        for health in [{"status": "ok", "warm": True, "cv_index_version": "mock-fixtures"},
                       {"status": "ok", "warm": False, "cv_index_version": "real-index"},
                       {"status": "ok", "warm": True}]:
            self.health = health
            with self.assertRaises(ValueError):
                eval_detector.run(self.args)
        self.assertEqual(self.posts, [])

    def test_endpoint_credentials_and_wrong_service_rejected(self):
        for endpoint in ["http://user:secret@host/v1/eval/predict", "http://host/v1/shelf/scan", "http://host/v1/eval/predict?key=secret"]:
            with self.assertRaises(ValueError):
                eval_detector.service_health(endpoint)

    def test_wrong_prediction_identity_rejected(self):
        path = self.root / "wrong.jsonl"
        path.write_text(json.dumps({"query_id": "wrong"}) + "\n")
        with self.assertRaises(ValueError):
            eval_detector.verify_predictions(path, [{"query_id": "q-1"}])

    def test_outputs_cannot_modify_dataset(self):
        self.args.output = str(self.data / "result.jsonl")
        with self.assertRaisesRegex(ValueError, "вне исходного датасета"):
            eval_detector.run(self.args)


if __name__ == "__main__":
    unittest.main()
