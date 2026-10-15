"""CLI help for every subcommand, download patterns, checksum verification and load_dataset."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for running without pip install
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import make_dataset
from mmenterprise_bench import HF_DATASET_REPO, RAW_PATTERNS, load_dataset
from mmenterprise_bench import hub
from mmenterprise_bench.cli import build_parser, main

SUBCOMMANDS = ["download", "validate", "export-contexts", "score", "run-baseline", "metrics", "serve"]
SPEC_PATTERNS = ["qa/*", "corpus/*", "retrieval/*", "media/*", "projects.json", "seed_documents.json", "manifest.json", "SHA256SUMS"]


class CliTests(unittest.TestCase):
    def test_help_for_every_subcommand(self):
        parser = build_parser()
        names = [action.dest for action in parser._actions if action.dest == "command"]
        self.assertEqual(names, ["command"])
        choices = parser._subparsers._group_actions[0].choices
        self.assertEqual(sorted(choices), sorted(SUBCOMMANDS))
        for name in SUBCOMMANDS:
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream), self.assertRaises(SystemExit) as caught:
                main([name, "--help"])
            self.assertEqual(caught.exception.code, 0, name)
            self.assertIn("usage: mmeb " + name, stream.getvalue())

    def test_score_help_lists_judge_options(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), self.assertRaises(SystemExit):
            main(["score", "--help"])
        text = stream.getvalue()
        for option in ("--judge-model", "--judge-base-url", "--judge-api-key-env", "--judge-concurrency", "--pre-judged", "--limit", "--retriever", "--k"):
            self.assertIn(option, text)

    def test_commands_on_fixture_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_dataset(Path(temp) / "ds")
            output = Path(temp) / "ctx.jsonl"
            self.assertEqual(main(["export-contexts", "--dataset", str(root), "--retriever", "2b", "--k", "10", "--limit", "1", "--output", str(output)]), 0)
            self.assertEqual(len(output.read_text().splitlines()), 1)
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(["run-baseline", "--dataset", str(root), "--dry-run", "--models", "luna", "--k", "10", "--output", str(Path(temp) / "o")]), 0)
            self.assertEqual(json.loads(stream.getvalue())["answer_cells"], 4)
            predictions = Path(temp) / "p.jsonl"
            predictions.write_text(json.dumps({"id": "p01::Q1", "answer": "x", "correct": True}) + "\n")
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(["score", str(predictions), "--dataset", str(root), "--pre-judged", "--output", str(Path(temp) / "s")]), 0)
            self.assertIn("**Accuracy: 25.0%**", stream.getvalue())
            self.assertEqual(json.loads((Path(temp) / "s/metrics.json").read_text())["correct"], 1)
            stream = io.StringIO()
            with contextlib.redirect_stderr(stream):
                self.assertEqual(main(["validate", "--dataset", str(Path(temp) / "nowhere")]), 1)
            self.assertIn("Validation failed", stream.getvalue())


class HubTests(unittest.TestCase):
    def test_raw_patterns_match_the_release_spec(self):
        self.assertEqual(RAW_PATTERNS, SPEC_PATTERNS)
        self.assertEqual(HF_DATASET_REPO, "MMEnterprise-Bench/MMEnterprise-Bench")

    def test_download_passes_token_and_patterns(self):
        calls = []

        def fake_snapshot_download(**kwargs):
            calls.append(kwargs)
            make_dataset(kwargs["local_dir"], with_checksums=True)
            return kwargs["local_dir"]

        with tempfile.TemporaryDirectory() as temp, patch("huggingface_hub.snapshot_download", fake_snapshot_download), \
                patch.dict(os.environ, {"HF_TOKEN": "test-token"}):
            hub.download_dataset(Path(temp) / "ds", revision="v1", quiet=True)
        [call] = calls
        self.assertEqual(call["repo_id"], HF_DATASET_REPO)
        self.assertEqual(call["repo_type"], "dataset")
        self.assertEqual(call["allow_patterns"], SPEC_PATTERNS)
        self.assertEqual((call["revision"], call["token"]), ("v1", "test-token"))

    def test_unavailable_repo_gives_clear_error(self):
        from huggingface_hub import errors

        def repository_not_found():
            try:
                return errors.RepositoryNotFoundError("404")          # huggingface_hub < 2
            except TypeError:                                          # >= 2 requires a response object
                class Response:
                    headers, request, status_code = {}, None, 404
                return errors.RepositoryNotFoundError("404", response=Response())

        def failing(**kwargs):
            raise repository_not_found()

        with tempfile.TemporaryDirectory() as temp, patch("huggingface_hub.snapshot_download", failing):
            with self.assertRaisesRegex(hub.DatasetUnavailable, "private or not published yet"):
                hub.download_dataset(Path(temp) / "ds", quiet=True)
            stream = io.StringIO()
            with contextlib.redirect_stderr(stream):
                self.assertEqual(main(["download", "--dataset", str(Path(temp) / "ds")]), 1)
            self.assertIn("HF_TOKEN", stream.getvalue())

    def test_verify_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_dataset(Path(temp) / "ds", with_checksums=True)
            (root / ".cache/huggingface").mkdir(parents=True)
            (root / ".cache/huggingface/meta").write_text("ignored hub metadata")
            self.assertGreater(hub.verify_checksums(root), 5)
            (root / "extra.txt").write_text("x")
            with self.assertRaisesRegex(ValueError, "not covered"):
                hub.verify_checksums(root)
            (root / "extra.txt").unlink()
            (root / "media/image.png").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                hub.verify_checksums(root)

    def test_load_dataset_downloads_when_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "ds"
            with self.assertRaises(FileNotFoundError):
                load_dataset(target, download=False)
            with patch("mmenterprise_bench.hub.download_dataset", lambda root, revision=None: make_dataset(root)):
                dataset = load_dataset(target)                       # auto-download path
            self.assertEqual(len(dataset.rows), 4)
            with patch.dict(os.environ, {"MMEB_DATA_DIR": str(target)}):
                self.assertEqual(load_dataset(download=False).root, target.resolve())


if __name__ == "__main__":
    unittest.main()
