#!/usr/bin/env python3
"""Filing stories into a domain (serve.set_domains): each story's top master gets its Domain
line written, replaced or removed on whichever file holds it, in one commit.

    python3 -m unittest test_domains
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kandalf import serve
from kandalf import store as stores
from kandalf import tiles as reader

PLANS = """# Plans

## To do

- [ ] PLN-70 **Family.**
  The master.
- [ ] PLN-71 **First kid.** Part of PLN-70.
- [ ] PLN-72 **Second kid.** Part of PLN-70.
  Domain: Special
- [ ] PLN-73 **Loner.**
  Domain: Billing

## In flight

- [ ] PLN-74 **Running.**
  Some words.
"""

BACKLOG = """# Backlog

## Backlog

- [ ] PLN-80 **Later.**
  Domain: Reports
- [ ] PLN-81 **Admin: analytics.**
"""

ARCHIVE = """# Archive

## Archived

- [x] PLN-90 **Old thing.**
  It shipped.
  Archived 2026-11-01.
"""


class Domains(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "archive").mkdir()
        self.files = {"plans.md": PLANS, "backlog.md": BACKLOG, "archive/archived.md": ARCHIVE}
        for name, text in self.files.items():
            (self.dir / name).write_text(text)
        self.store = stores.Store(self.dir)
        patch = mock.patch.object(serve.gitlog, "record")
        self.record = patch.start()
        self.addCleanup(patch.stop)

    def text(self, name):
        return (self.dir / name).read_text()

    def domain(self, key):
        snap = reader.Snapshot.read(self.store)
        return reader.domain(snap.tile(key).text)

    def subject(self):
        return self.record.call_args[0][2]

    def assert_untouched(self):
        for name, text in self.files.items():
            self.assertEqual(self.text(name), text)

    def test_stories_on_three_files_are_filed_in_one_commit(self):
        status, reply = serve.set_domains(self.store, {"PLN-74": "Billing", "PLN-81": "Billing", "PLN-90": "Billing"})
        self.assertEqual(status, 200)
        self.assertEqual(list(reply["filed"]), ["PLN-74", "PLN-81", "PLN-90"])
        self.assertEqual(reply["filed"]["PLN-74"], {"from": None, "to": "Billing"})
        self.assertEqual([self.domain(k) for k in ("PLN-74", "PLN-81", "PLN-90")], ["Billing"] * 3)
        self.record.assert_called_once()
        self.assertEqual(self.subject(), "board: 3 stories → Billing")
        self.assertEqual({p.name for p in self.record.call_args[0][1]}, {"plans.md", "backlog.md", "archived.md"})

    def test_a_sub_story_files_its_master_and_the_family_counts_once(self):
        status, reply = serve.set_domains(self.store, {"PLN-71": "Billing", "PLN-72": "Billing"})
        self.assertEqual(status, 200)
        self.assertEqual(reply["filed"], {"PLN-70": {"from": None, "to": "Billing"}})
        self.assertEqual(self.subject(), "board: 1 story → Billing")
        self.assertIsNone(self.domain("PLN-71"))

    def test_a_master_and_its_own_sub_story_together_are_one_write(self):
        status, reply = serve.set_domains(self.store, {"PLN-70": "Billing", "PLN-71": "Billing"})
        self.assertEqual(list(reply["filed"]), ["PLN-70"])
        self.assertEqual(self.text("plans.md").count("Domain: Billing"), 2)  # PLN-70's and PLN-73's
        self.assertEqual(reply["unchanged"], [])

    def test_a_sub_story_with_its_own_domain_line_keeps_it(self):
        serve.set_domains(self.store, {"PLN-72": "Billing"})
        self.assertEqual(self.domain("PLN-72"), "Special")
        self.assertEqual(self.domain("PLN-70"), "Billing")

    def test_no_domain_takes_the_line_off_and_says_so_in_the_commit(self):
        status, reply = serve.set_domains(self.store, {"PLN-73": None, "PLN-80": None})
        self.assertEqual(reply["filed"], {"PLN-73": {"from": "Billing", "to": None},
                                           "PLN-80": {"from": "Reports", "to": None}})
        self.assertEqual(self.subject(), "board: 2 stories → no domain")
        self.assertNotIn("Domain:", self.text("backlog.md"))

    def test_a_story_with_no_line_dropped_on_no_domain_is_unchanged(self):
        status, reply = serve.set_domains(self.store, {"PLN-81": None})
        self.assertEqual((status, reply), (200, {"filed": {}, "unchanged": ["PLN-81"], "skipped": []}))
        self.record.assert_not_called()
        self.assert_untouched()

    def test_the_same_domain_again_is_unchanged_and_commits_nothing(self):
        status, reply = serve.set_domains(self.store, {"PLN-73": "Billing"})
        self.assertEqual(reply["unchanged"], ["PLN-73"])
        self.assertEqual(reply["filed"], {})
        self.record.assert_not_called()
        self.assert_untouched()

    def test_a_name_in_another_case_is_written_as_the_domain_is_already_spelt(self):
        status, reply = serve.set_domains(self.store, {"PLN-74": "  bILLING "})
        self.assertEqual(reply["filed"]["PLN-74"]["to"], "Billing")
        self.assertEqual(self.domain("PLN-74"), "Billing")

    def test_one_bad_name_refuses_the_whole_call_and_writes_nothing(self):
        status, reply = serve.set_domains(self.store, {"PLN-74": "Billing", "PLN-81": "`bad`"})
        self.assertEqual(status, 400)
        self.assertTrue(reply["error"])
        self.assert_untouched()
        self.record.assert_not_called()

    def test_a_name_that_is_not_text_is_a_bad_name(self):
        status, reply = serve.set_domains(self.store, {"PLN-74": 5})
        self.assertEqual((status, reply), (400, {"error": "A domain needs a name."}))
        self.assert_untouched()

    def test_nothing_to_file_is_refused(self):
        for changes in (None, {}, [], "x"):
            self.assertEqual(serve.set_domains(self.store, changes), (400, {"error": "nothing to file"}))

    def test_a_key_no_tile_has_is_skipped(self):
        status, reply = serve.set_domains(self.store, {"PLN-99": "Billing", "PLN-74": "Billing"})
        self.assertEqual(reply["skipped"], ["PLN-99"])
        self.assertEqual(list(reply["filed"]), ["PLN-74"])

    def test_mixed_targets_are_one_commit_named_re_filed(self):
        serve.set_domains(self.store, {"PLN-74": "Billing", "PLN-73": None})
        self.record.assert_called_once()
        self.assertEqual(self.subject(), "board: 2 stories re-filed")

    def test_undo_is_the_same_call_with_what_each_was_before(self):
        status, reply = serve.set_domains(self.store, {"PLN-73": "Reports", "PLN-74": "Reports", "PLN-90": "Reports", "PLN-80": None})
        self.assertNotEqual(self.text("plans.md"), PLANS)
        back = {k: v["from"] for k, v in reply["filed"].items()}
        serve.set_domains(self.store, back)
        self.assert_untouched()

    def test_a_name_some_tile_already_carries_keeps_that_spelling(self):
        (self.dir / "plans.md").write_text(PLANS.replace("Domain: Special", "Domain: billing"))
        status, reply = serve.set_domains(self.store, {"PLN-74": "Billing", "PLN-81": "BILLING"})
        self.assertEqual(reply["filed"]["PLN-74"]["to"], "Billing")
        self.assertEqual(reply["filed"]["PLN-81"]["to"], "billing")
        self.assertEqual(self.domain("PLN-74"), "Billing")

    def drop_then_undo(self, drop, expect):
        """Drop, then post the Undo with what each filed tile should say now: (status, reply)."""
        status, reply = serve.set_domains(self.store, drop)
        self.assertEqual(status, 200, reply)
        back = {k: v["from"] for k, v in reply["filed"].items()}
        return serve.set_domains(self.store, back, expect=expect(reply["filed"]))

    def test_an_undo_whose_tiles_say_what_the_drop_wrote_restores_the_files(self):
        status, reply = self.drop_then_undo({"PLN-73": "Reports", "PLN-80": None, "PLN-90": "Reports"},
                                            lambda filed: {k: v["to"] for k, v in filed.items()})
        self.assertEqual(status, 200, reply)
        self.assert_untouched()

    def test_an_undo_after_a_backlog_tile_was_re_filed_is_refused_and_writes_nothing(self):
        status, reply = serve.set_domains(self.store, {"PLN-74": "Billing", "PLN-80": "Billing"})
        edited = self.text("backlog.md").replace("Domain: Billing", "Domain: Payments")
        (self.dir / "backlog.md").write_text(edited)
        before = {name: self.text(name) for name in self.files}
        self.record.reset_mock()
        status, reply = serve.set_domains(self.store, {"PLN-74": None, "PLN-80": "Reports"},
                                          expect={"PLN-74": "Billing", "PLN-80": "Billing"})
        self.assertEqual((status, reply), (409, {"error": "changed since", "changed": ["PLN-80"]}))
        self.assertEqual({name: self.text(name) for name in self.files}, before)
        self.record.assert_not_called()

    def test_an_expected_name_in_another_case_still_matches(self):
        status, reply = self.drop_then_undo({"PLN-74": "Billing"}, lambda filed: {"PLN-74": "bILLING"})
        self.assertEqual(status, 200, reply)
        self.assert_untouched()

    def test_an_expected_null_matches_a_tile_with_no_line(self):
        status, reply = serve.set_domains(self.store, {"PLN-81": "Billing"}, expect={"PLN-81": None})
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.domain("PLN-81"), "Billing")
        status, reply = serve.set_domains(self.store, {"PLN-73": None}, expect={"PLN-73": None})
        self.assertEqual((status, reply["changed"]), (409, ["PLN-73"]))

    def test_one_mismatch_among_several_refuses_the_whole_call(self):
        status, reply = serve.set_domains(self.store, {"PLN-73": "Reports", "PLN-71": "Reports", "PLN-74": "Reports"},
                                          expect={"PLN-73": "Billing", "PLN-71": "Admin", "PLN-74": None})
        self.assertEqual((status, reply), (409, {"error": "changed since", "changed": ["PLN-70"]}))
        self.assert_untouched()
        self.record.assert_not_called()

    def test_an_expected_key_no_tile_has_is_changed(self):
        status, reply = serve.set_domains(self.store, {"PLN-74": "Billing"}, expect={"PLN-74": None, "PLN-99": "Billing"})
        self.assertEqual((status, reply), (409, {"error": "changed since", "changed": ["PLN-99"]}))
        self.assert_untouched()

    def post(self, path, body):
        """A POST to a board server on this store, as the page sends it: (status, reply)."""
        import http.client
        import threading
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": self.store}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            r = c.getresponse()
            out = (r.status, json.loads(r.read()))
            c.close()
            return out
        finally:
            srv.shutdown()
            srv.server_close()

    def test_the_route_files_stories_and_answers_with_the_board(self):
        status, reply = self.post("/api/domain?file=plans.md", {"set": {"PLN-74": "Billing"}})
        self.assertEqual(status, 200, reply)
        self.assertTrue({"mtime", "text", "tiles", "filed"} <= set(reply))
        self.assertEqual(reply["filed"], {"PLN-74": {"from": None, "to": "Billing"}})
        self.assertIn("Domain: Billing", reply["text"].split("PLN-74", 1)[1])

    def test_the_route_refuses_nothing_to_file_a_bad_name_and_an_unknown_board(self):
        self.assertEqual(self.post("/api/domain?file=plans.md", {"set": {}}), (400, {"error": "nothing to file"}))
        status, reply = self.post("/api/domain?file=plans.md", {"set": {"PLN-74": ".bad"}})
        self.assertEqual((status, reply), (400, {"error": "A domain's name starts with a letter or a digit."}))
        self.assertEqual(self.post("/api/domain?file=nope.md", {"set": {"PLN-74": "Billing"}})[0], 404)
        self.assert_untouched()

    def test_the_route_refuses_an_undo_whose_tiles_changed_since_and_ignores_an_expect_that_is_not_a_map(self):
        status, reply = self.post("/api/domain?file=plans.md", {"set": {"PLN-73": None}, "expect": {"PLN-73": "Payments"}})
        self.assertEqual((status, reply), (409, {"error": "changed since", "changed": ["PLN-73"]}))
        self.assert_untouched()
        status, reply = self.post("/api/domain?file=plans.md", {"set": {"PLN-74": "Billing"}, "expect": ["PLN-74"]})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["filed"], {"PLN-74": {"from": None, "to": "Billing"}})

    def test_an_archived_tile_keeps_its_archived_line_last(self):
        serve.set_domains(self.store, {"PLN-90": "Billing"})
        self.assertEqual(self.text("archive/archived.md").splitlines()[-3:],
                         ["  It shipped.", "  Domain: Billing", "  Archived 2026-11-01."])


if __name__ == "__main__":
    unittest.main()
