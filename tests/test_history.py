import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.helpers import BridgeFixture
from zebra_print_bridge.jobs import JobLog

PRINTER = SimpleNamespace(uid="p1", name="Zebra", connection="network")
LOGO = "~DGR:LOGO.GRF,4,2,FF0000FF\n"


class HistoryFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "history.jsonl"

    def log(self, **kwargs):
        return JobLog(path=self.path, **kwargs)

    def test_jobs_and_responses_survive_a_restart(self):
        jobs = self.log()
        first = jobs.record(PRINTER, "^XA^CI28^FDÁé^FS^XZ")
        jobs.attach_response("p1", "\x02PRINTER STATUS\x03")
        jobs.record(PRINTER, "^XA^FDfail^FS^XZ", error="Connection refused")

        again = self.log()
        summaries = again.summaries()
        self.assertEqual([j["id"] for j in summaries], [first["id"], first["id"] + 1])
        self.assertEqual(again.get(first["id"])["data"], "^XA^CI28^FDÁé^FS^XZ")
        self.assertEqual(summaries[0]["response"], "\x02PRINTER STATUS\x03")
        self.assertEqual(summaries[1]["error"], "Connection refused")
        self.assertEqual(again.printers.charset("p1"), 28)
        # Ids keep growing after a restart (the viewer links jobs by id).
        self.assertEqual(again.record(PRINTER, "^XA^XZ")["id"], first["id"] + 2)

    def test_file_is_private(self):
        self.log().record(PRINTER, "^XA^FDJane Doe, 1 Main St^FS^XZ")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_printer_state_outlives_the_jobs(self):
        jobs = self.log(max_jobs=2)
        jobs.record(PRINTER, LOGO)
        jobs.record(PRINTER, "^XA^CI28^XZ")
        jobs.record(PRINTER, "^XA^FDa^FS^XZ")
        jobs.record(PRINTER, "^XA^FDb^FS^XZ")  # the ~DG and ^CI jobs are gone from the history
        jobs.clear()

        again = self.log(max_jobs=2)
        self.assertEqual(again.summaries(), [])
        self.assertEqual(again.printers.graphics_for("p1", "^XA^XGR:LOGO.GRF^XZ"), LOGO)
        self.assertEqual(again.printers.charset("p1"), 28)

    def test_compaction_keeps_the_file_bounded(self):
        jobs = self.log(max_jobs=5)
        for i in range(40):
            jobs.record(PRINTER, f"^XA^FD{i}^FS^XZ")
        lines = self.path.read_text().splitlines()
        self.assertLessEqual(len(lines), 2 * 5 + 1)
        again = self.log(max_jobs=5)
        self.assertEqual(
            [again.get(job["id"])["data"] for job in again.summaries()], [f"^XA^FD{i}^FS^XZ" for i in range(35, 40)]
        )

    def test_size_limit_drops_the_oldest(self):
        jobs = self.log(max_bytes=3000)
        for i in range(10):
            jobs.record(PRINTER, f"^XA^FD{i}{'x' * 500}^FS^XZ")
        jobs.clear()  # compacting with nothing to keep
        for i in range(10):
            jobs.record(PRINTER, f"^XA^FD{i}{'x' * 500}^FS^XZ")
        again = self.log(max_bytes=3000)
        kept = [again.get(s["id"])["data"][6] for s in again.summaries()]
        self.assertTrue(0 < len(kept) < 10)
        self.assertEqual(kept[-1], "9")
        self.assertLessEqual(self.path.stat().st_size, 3000)

    def test_unreadable_lines_are_skipped(self):
        jobs = self.log()
        jobs.record(PRINTER, "^XA^FDok^FS^XZ")
        with open(self.path, "a") as fh:
            fh.write("{not json\n")
            fh.write(json.dumps({"response": {"id": 999, "text": "orphan"}}) + "\n")
        again = self.log()
        self.assertEqual(len(again.summaries()), 1)

    def test_without_a_file(self):
        jobs = JobLog()
        jobs.record(PRINTER, "^XA^XZ")
        jobs.clear()
        self.assertIsNone(jobs.path)


class HistoryServiceTest(unittest.TestCase):
    def test_history_after_restarting_the_service(self):
        device = {"name": "Mock ZD421", "uid": "mock-1"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            first = BridgeFixture(history_file=str(path))
            first.request("POST", "write", {"device": device, "data": "^XA^FDkeep me^FS^XZ"})
            _, settings = first.ui("ui/api/settings")
            self.assertEqual(settings["service"]["history_file"], str(path))
            first.close()
            second = BridgeFixture(history_file=str(path))
            try:
                _, state = second.ui("ui/api/state")
                self.assertEqual([j["uid"] for j in state["jobs"]], ["mock-1"])
                _, job = second.ui(f"ui/api/jobs/{state['jobs'][0]['id']}")
                self.assertEqual(job["data"], "^XA^FDkeep me^FS^XZ")
            finally:
                second.close()

    def test_history_can_be_turned_off(self):
        bridge = BridgeFixture(persist_history=False)
        try:
            self.assertIsNone(bridge.server.jobs.path)
            _, settings = bridge.ui("ui/api/settings")
            self.assertIsNone(settings["service"]["history_file"])
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
