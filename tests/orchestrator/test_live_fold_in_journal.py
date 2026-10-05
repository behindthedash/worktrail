"""worker-fold-in-policy journal half.

Declared fold-ins are recorded on the run-journal entry's report dict, and
only when present -- a report with no fold-ins gains no new keys, so its
journal shape stays byte-identical to the pre-change one. A report doomed to
a fold-in violation counts as terminal for the missing-context pre-recovery
check, exactly like any other terminal report.
"""

import unittest

from worktrail.orchestrator import dispatch, live

_REPORT_FIELDS = ("status", "head_sha", "notes")


def _task(files=None):
    return {"id": "1.1", "files": files or ["src/a.py"], "retry_count": 0}


def _projection(rep):
    return {k: rep.get(k) for k in _REPORT_FIELDS}


class InjectFoldInFieldsTests(unittest.TestCase):
    def test_absent_fold_ins_adds_no_keys(self):
        rep = {"status": "success", "notes": "n"}
        fields = _projection(rep)
        live._inject_fold_in_fields(fields, _task(), rep)
        self.assertEqual(_projection(rep), fields)
        self.assertNotIn("fold_ins", fields)
        self.assertNotIn("fold_in_violations", fields)

    def test_empty_fold_ins_adds_no_keys(self):
        rep = {"status": "success", "notes": "n", "fold_ins": []}
        fields = _projection(rep)
        live._inject_fold_in_fields(fields, _task(), rep)
        self.assertEqual(_projection(rep), fields)
        self.assertNotIn("fold_ins", fields)

    def test_declared_fold_ins_are_recorded(self):
        rep = {
            "status": "success",
            "fold_ins": [
                {"file": "src/a.py", "commit": "abc123", "summary": "tighten x"}
            ],
        }
        fields = _projection(rep)
        live._inject_fold_in_fields(fields, _task(), rep)
        self.assertEqual(
            [{"file": "src/a.py", "commit": "abc123", "summary": "tighten x"}],
            fields["fold_ins"],
        )
        self.assertNotIn("fold_in_violations", fields)

    def test_violating_fold_ins_are_recorded(self):
        rep = {"status": "success", "fold_ins": [{"file": "src/b.py"}]}
        fields = _projection(rep)
        live._inject_fold_in_fields(fields, _task(), rep)
        self.assertIn("fold_in_violations", fields)
        self.assertNotIn("fold_ins", fields)


class WouldLandTerminalFoldInTests(unittest.TestCase):
    def test_violating_report_is_terminal(self):
        rep = {
            "task": "1.1",
            "step": dispatch.ROLE_IMPLEMENT,
            "status": "success",
            "fold_ins": [{"file": "src/b.py"}],
        }
        self.assertTrue(
            live._would_land_terminal(_task(), dispatch.ROLE_IMPLEMENT, rep)
        )

    def test_valid_report_is_not_terminal(self):
        rep = {
            "task": "1.1",
            "step": dispatch.ROLE_IMPLEMENT,
            "status": "success",
            "fold_ins": [{"file": "src/a.py"}],
        }
        self.assertFalse(
            live._would_land_terminal(_task(), dispatch.ROLE_IMPLEMENT, rep)
        )


if __name__ == "__main__":
    unittest.main()
