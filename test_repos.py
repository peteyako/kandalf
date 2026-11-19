#!/usr/bin/env python3
"""Repos in the store (store.py): the main app plus named repos from board.json.

    python3 -m unittest test_repos
"""

import contextlib
import http.client
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import test_pickup
from kandalf import claude_code as agent
from kandalf import pickup
from kandalf import serve
from kandalf import store as stores


class StoreRepos(unittest.TestCase):
    def store(self, cfg):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name).resolve()
        folder = self.top / "plans"
        folder.mkdir()
        (folder / "board.json").write_text(json.dumps(cfg))
        return stores.Store(folder)

    def test_a_store_with_only_an_app_has_one_main_repo(self):
        st = self.store({"app": "../myapp", "trunk": "dev"})
        self.assertEqual(st.repos, [stores.Repo("myapp", self.top / "myapp", "dev", True)])
        self.assertEqual(st.main_repo, st.repos[0])
        self.assertIsNone(st.repo_problem)

    def test_repos_follow_the_main_one_in_file_order_with_their_trunks(self):
        st = self.store({"app": "../myapp", "trunk": "dev",
                         "repos": {"api": "../api", "web": {"path": "../web", "trunk": "next"}}})
        self.assertEqual([r.name for r in st.repos], ["myapp", "api", "web"])
        self.assertEqual([r.trunk for r in st.repos], ["dev", "dev", "next"])
        self.assertEqual([r.path for r in st.repos], [self.top / "myapp", self.top / "api", self.top / "web"])
        self.assertEqual([r.main for r in st.repos], [True, False, False])

    def test_a_repo_is_found_by_name_in_any_case(self):
        st = self.store({"app": "../myapp", "repos": {"api": "../api"}})
        self.assertEqual(st.repo("API").name, "api")
        self.assertIsNone(st.repo("nope"))

    def test_a_name_clashing_with_the_main_repo_is_a_problem(self):
        st = self.store({"app": "../myapp", "repos": {"MyApp": "../other"}})
        self.assertIn("MyApp", st.repo_problem)
        self.assertEqual([r.name for r in st.repos], ["myapp"])

    def test_names_differing_only_in_case_or_badly_formed_are_problems(self):
        st = self.store({"app": "../myapp", "repos": {"api": "../a", "API": "../b"}})
        self.assertIn("API", st.repo_problem)
        st = self.store({"app": "../myapp", "repos": {"a pi": "../a", "web": "../w"}})
        self.assertIn("a pi", st.repo_problem)
        self.assertEqual([r.name for r in st.repos], ["myapp", "web"])

    def test_an_entry_with_no_path_is_a_problem(self):
        st = self.store({"app": "../myapp", "repos": {"api": {"trunk": "x"}}})
        self.assertIn("api", st.repo_problem)
        self.assertEqual([r.name for r in st.repos], ["myapp"])

    def test_plans_app_repo_moves_only_the_main_repo(self):
        with mock.patch.dict(os.environ, {"PLANS_APP_REPO": "../elsewhere"}):
            st = self.store({"app": "../myapp", "repos": {"api": "../api"}})
        self.assertEqual(st.repos[0].path, self.top / "elsewhere")
        self.assertEqual(st.repo("api").path, self.top / "api")

    def test_plans_app_trunk_moves_only_the_main_repo(self):
        with mock.patch.dict(os.environ, {"PLANS_APP_TRUNK": "hotfix"}):
            st = self.store({"app": "../myapp", "trunk": "dev", "repos": {"api": "../api"}})
        self.assertEqual([r.trunk for r in st.repos], ["hotfix", "dev"])

    def test_api_files_lists_the_store_s_repos_main_first(self):
        st = self.store({"app": "../myapp", "repos": {"api": "../api", "web": "../web"}})
        srv = serve.ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (serve.Handler,), {"store": st}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", "/api/files")
        reply = json.loads(c.getresponse().read())
        c.close()
        self.assertEqual(reply["repos"], [{"name": r.name, "main": r.main} for r in st.repos])
        self.assertEqual(reply["repos"][0], {"name": "myapp", "main": True})


class TileRepos(unittest.TestCase):
    def store(self, cfg=None, make=("myapp", "api", "web")):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name).resolve()
        for name in make:
            (self.top / name / ".git").mkdir(parents=True)
        folder = self.top / "plans"
        folder.mkdir()
        cfg = cfg or {"app": "../myapp", "repos": {"api": "../api", "web": "../web"}}
        (folder / "board.json").write_text(json.dumps(cfg))
        return stores.Store(folder)

    def names(self, text):
        repos, problem = pickup.resolve_repos(self.store(), pickup.repo_names(text))
        self.assertIsNone(problem)
        return [r.name for r in repos]

    def test_no_line_means_the_main_repo(self):
        st = self.store()
        self.assertIsNone(pickup.repo_names("- [ ] PLN-1 **x**"))
        self.assertEqual(pickup.resolve_repos(st, None), ([st.main_repo], None))

    def test_the_line_s_order_is_kept_and_names_match_in_any_case(self):
        self.assertEqual(self.names("- [ ] PLN-1 **x**\n  Repos: API, myapp"), ["api", "myapp"])

    def test_repo_singular_backticks_and_spaces_are_read(self):
        self.assertEqual(self.names("  Repo: `web`"), ["web"])
        self.assertEqual(self.names("  Repos: api web."), ["api", "web"])
        self.assertEqual(pickup.repo_names("  Repos: `api`, API, web"), ["api", "web"])

    def test_a_sub_story_takes_its_master_s_line_unless_it_has_its_own(self):
        by_key = {
            "PLN-1": {"key": "PLN-1", "parent": None, "text": "- [ ] PLN-1 **m**\n  Repos: api"},
            "PLN-2": {"key": "PLN-2", "parent": "PLN-1", "text": "- [ ] PLN-2 **a** Part of PLN-1"},
            "PLN-3": {"key": "PLN-3", "parent": "PLN-1", "text": "- [ ] PLN-3 **b** Part of PLN-1\n  Repos: web"},
        }
        self.assertEqual(pickup.repo_names(pickup.family_text(by_key, "PLN-2")), ["api"])
        self.assertEqual(pickup.repo_names(pickup.family_text(by_key, "PLN-3")), ["web"])

    def test_an_unknown_or_missing_repo_is_an_error_not_the_main_repo(self):
        st = self.store(make=("myapp", "api"))
        self.assertEqual(pickup.resolve_repos(st, ["apix"]), ([], "no repo named `apix` in board.json"))
        self.assertEqual(pickup.resolve_repos(st, ["web"]), ([], f"repo `web` isn't there at {self.top / 'web'}"))
        (self.top / "web").mkdir()
        self.assertEqual(pickup.resolve_repos(st, ["web"]), ([], "repo `web` isn't a git repo"))
        repos, problem = pickup.resolve_repos(st, ["api"])
        self.assertEqual(([r.name for r in repos], problem), (["api"], None))

    def test_an_empty_line_names_no_repo_and_never_reads_the_next_line(self):
        st = self.store()
        self.assertEqual(pickup.resolve_repos(st, pickup.repo_names("  Repos: ,")),
                         ([], "the Repos line names no repo"))
        self.assertEqual(pickup.resolve_repos(st, pickup.repo_names("  Repos: ``"))[1], "the Repos line names no repo")
        self.assertIsNone(pickup.repo_names("  Repos:\n- [ ] PLN-1 **m**"))
        self.assertIsNone(pickup.repo_names("  Repos:   \n  api"))

    def test_a_queued_job_carries_what_its_tile_wants(self):
        st = self.store()
        fake = test_pickup.Fake()
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

        def tick(tiles):
            return pickup.tick(st, tiles, start=fake.start, sessions=fake.sessions, switch=fake.switch,
                               why=fake.why, now=lambda: 1000)

        tile = lambda key, extra="": {"key": key, "title": "t", "parent": None, "text": f"- [ ] {key} **t**{extra}"}
        tick([])
        tick([tile("PLN-1", "\n  Repos: api"), tile("PLN-2")])
        jobs = {j["key"]: j for j in pickup.load(st)["jobs"]}
        self.assertEqual(jobs["PLN-1"]["want"], ["api"])
        self.assertNotIn("want", jobs["PLN-2"])


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


class Tree(unittest.TestCase):
    """myapp, api and web git repos (trunk dev) beside a plans store; api links the store at docs/plans."""

    def tree(self, **extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name).resolve()
        for name in ("myapp", "api", "web"):
            repo = self.top / name
            repo.mkdir()
            git(repo, "init", "-q", "-b", "dev")
            git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        folder = self.top / "plans"
        folder.mkdir()
        (self.top / "api" / "docs").mkdir()
        (self.top / "api" / "docs" / "plans").symlink_to("../../plans")
        cfg = {"app": "../myapp", "trunk": "dev", "worktree": "{app}/.worktrees/{key}",
               "repos": {"api": "../api", "web": "../web"}, **extra}
        (folder / "board.json").write_text(json.dumps(cfg))
        self.st = stores.Store(folder)
        self.job = {"key": "PLN-07", "title": "Small thing"}
        return self.st

    def head(self, path):
        return pickup._git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


class Cutting(Tree):
    def test_each_repo_gets_a_worktree_on_one_branch(self):
        st = self.tree()
        api, web = st.repo("api"), st.repo("web")
        cuts, name = pickup.prepare_all(st, self.job, [api, web])
        self.assertEqual(name, "pln-07-small-thing")
        self.assertEqual(cuts, [{"name": "api", "worktree": str(self.top / "api/.worktrees/pln-07"), "trunk": "dev"},
                                {"name": "web", "worktree": str(self.top / "web/.worktrees/pln-07"), "trunk": "dev"}])
        for cut in cuts:
            self.assertEqual(self.head(cut["worktree"]), "pln-07-small-thing")
        self.assertTrue((Path(cuts[0]["worktree"]) / "docs/plans").is_symlink())
        self.assertFalse((Path(cuts[1]["worktree"]) / "docs").exists())

    def test_the_branch_pattern_s_app_is_the_starting_repo(self):
        st = self.tree(branch="{app}/{key}")
        cuts, name = pickup.prepare_all(st, self.job, [st.repo("api"), st.repo("web")])
        self.assertEqual(name, "api/pln-07")
        self.assertEqual([self.head(c["worktree"]) for c in cuts], ["api/pln-07", "api/pln-07"])

    def test_a_repo_that_cannot_be_cut_is_named_and_the_others_stay(self):
        st = self.tree(repos={"api": "../api", "web": {"path": "../web", "trunk": "nope"}})
        with self.assertRaisesRegex(RuntimeError, "^web:"):
            pickup.prepare_all(st, self.job, [st.repo("api"), st.repo("web")])
        self.assertTrue((self.top / "api/.worktrees/pln-07").is_dir())

    def test_cutting_again_reuses_every_worktree_and_the_branch(self):
        st = self.tree()
        repos = [st.repo("api"), st.repo("web")]
        cuts, name = pickup.prepare_all(st, self.job, repos)
        again, name2 = pickup.prepare_all(st, {**self.job, "repos": cuts, "worktree": cuts[0]["worktree"]}, repos)
        self.assertEqual((again, name2), (cuts, name))

    def test_a_reordered_line_keeps_the_story_on_one_branch(self):
        st = self.tree(branch="{app}/{key}")
        api, web = st.repo("api"), st.repo("web")
        cuts, name = pickup.prepare_all(st, self.job, [api])
        job = {**self.job, "branch": name, "repos": cuts, "worktree": cuts[0]["worktree"]}
        again, name2 = pickup.prepare_all(st, job, [web, api])
        self.assertEqual((name, name2), ("api/pln-07", "api/pln-07"))
        self.assertEqual([self.head(c["worktree"]) for c in again], ["api/pln-07", "api/pln-07"])
        # without the job's branch noted, the cuts it has still say which one it is
        again, name3 = pickup.prepare_all(st, {**job, "branch": None}, [web, api])
        self.assertEqual(name3, "api/pln-07")

    def test_a_new_first_repo_and_a_reused_later_one_end_on_the_jobs_branch(self):
        st = self.tree(branch="{app}/{key}")
        api, web, app = st.repo("api"), st.repo("web"), st.main_repo
        cuts, name = pickup.prepare_all(st, self.job, [web])
        job = {**self.job, "branch": name, "repos": cuts, "worktree": cuts[0]["worktree"]}
        again, name2 = pickup.prepare_all(st, job, [api, app, web])
        self.assertEqual(name2, "web/pln-07")
        self.assertEqual([self.head(c["worktree"]) for c in again], ["web/pln-07"] * 3)

    def test_the_main_repo_alone_is_cut_as_before(self):
        st = self.tree()
        path, name = pickup.prepare(st, self.job)
        self.assertEqual((path, name), (self.top / "myapp/.worktrees/pln-07", "pln-07-small-thing"))


class Launch(Tree):
    def fake(self):
        calls = []
        def start(args, cwd, permission, text, dirs=()):
            calls.append({"cwd": cwd, "dirs": list(dirs), "text": text})
            return {"id": "ab12cd34"}
        patches = [mock.patch.object(agent, "start", start), mock.patch.object(pickup, "full_id", lambda s: "full")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return calls

    def test_the_other_worktrees_go_in_add_dir_before_the_permission_mode(self):
        said = subprocess.CompletedProcess([], 0, "backgrounded x ab12cd34\n", "")
        with mock.patch("kandalf.claude_code.subprocess.run", return_value=said) as run:
            agent.start(["-n", "x"], ".", "auto", "go", ["/a", "/b"])
            self.assertEqual(run.call_args[0][0],
                             ["claude", "--bg", "-n", "x", "--add-dir", "/a", "/b", "--permission-mode", "auto", "go"])
            agent.start(["-n", "x"], ".", "auto", "go")
            self.assertEqual(run.call_args[0][0], ["claude", "--bg", "-n", "x", "--permission-mode", "auto", "go"])

    def test_start_runs_in_the_first_wanted_repo_and_records_every_cut(self):
        st, calls = self.tree(), self.fake()
        done = pickup.start(st, {**self.job, "want": ["web", "api"]})
        web, api = str(self.top / "web/.worktrees/pln-07"), str(self.top / "api/.worktrees/pln-07")
        self.assertEqual(done["state"], "started")
        self.assertEqual((str(calls[0]["cwd"]), calls[0]["dirs"]), (web, [api]))
        self.assertEqual([c["name"] for c in done["repos"]], ["web", "api"])
        self.assertEqual((done["worktree"], done["branch"]), (web, "pln-07-small-thing"))

    def test_a_main_only_story_starts_exactly_as_before(self):
        st, calls = self.tree(), self.fake()
        done = pickup.start(st, self.job)
        self.assertEqual((calls[0]["dirs"], done["worktree"]), ([], str(self.top / "myapp/.worktrees/pln-07")))

    def test_an_unknown_repo_fails_the_story_with_the_reason(self):
        st, calls = self.tree(), self.fake()
        done = pickup.start(st, {**self.job, "want": ["apix"]})
        self.assertEqual(done["state"], "failed")
        self.assertIn("apix", done["error"])
        self.assertEqual(calls, [])
        self.assertFalse((self.top / "myapp/.worktrees").exists())

    def test_a_repo_that_cannot_be_cut_fails_the_story_naming_it(self):
        st, calls = self.tree(repos={"api": "../api", "web": {"path": "../web", "trunk": "nope"}}), self.fake()
        done = pickup.start(st, {**self.job, "want": ["api", "web"]})
        self.assertEqual(done["state"], "failed")
        self.assertTrue(done["error"].startswith("web:"))
        self.assertEqual(calls, [])

    def test_a_repo_dropped_from_the_line_stays_on_the_job(self):
        st, _ = self.tree(), self.fake()
        first = pickup.start(st, {**self.job, "want": ["api", "web"]})
        second = pickup.start(st, {**self.job, **first, "want": ["web"]})
        self.assertEqual([c["name"] for c in second["repos"]], ["web", "api"])
        self.assertEqual(second["worktree"], str(self.top / "web/.worktrees/pln-07"))

    def test_resume_passes_the_same_extra_dirs(self):
        st, calls = self.tree(), self.fake()
        job = {**self.job, "want": ["web", "api"], **pickup.start(st, {**self.job, "want": ["web", "api"]})}
        calls.clear()
        done = pickup.resume(st, {**job, "session": "full"})
        self.assertEqual(done["state"], "started")
        self.assertEqual(calls[0]["dirs"], [str(self.top / "api/.worktrees/pln-07")])
        self.assertEqual(pickup.extra_dirs(st, job), calls[0]["dirs"])
        self.assertEqual(pickup.extra_dirs(st, {**job, "want": ["nope"]}), [])
        self.assertEqual(pickup.extra_dirs(st, self.job), [])

    def test_the_prompt_lists_each_repo_only_when_there_is_more_than_main(self):
        st, calls = self.tree(), self.fake()
        path = self.top / "myapp/.worktrees/pln-07"
        plain = pickup.prompt(st, self.job, "pln-07-small-thing", path)
        self.assertEqual(pickup.prompt(st, {**self.job, "want": ["myapp"]}, "pln-07-small-thing", path), plain)
        self.assertNotIn("every repo", plain)
        pickup.start(st, {**self.job, "want": ["web", "api"]})
        text = calls[0]["text"]
        for part in ("every repo", str(self.top / "web/.worktrees/pln-07"), str(self.top / "api/.worktrees/pln-07"), "Repos"):
            self.assertIn(part, text)

    def test_a_session_outside_the_main_repo_is_told_its_own_repo_and_trunk(self):
        st = self.tree(repos={"api": "../api", "web": {"path": "../web", "trunk": "next"}})
        path = self.top / "web/.worktrees/pln-07"
        job = {**self.job, "want": ["web", "api"], "repos": [{"name": "web", "worktree": str(path), "trunk": "next"}]}
        for again in (False, True):
            text = pickup.prompt(st, job, "pln-07-small-thing", path, again)
            self.assertIn(f"worktree of the web repo at {path}", text)
            self.assertIn("cut from next", text)
            self.assertNotIn("of the app", text)
            self.assertNotIn("docs/plans", text)
            self.assertNotIn("small-thing, cut from dev", text)
        self.assertIn("git log next..HEAD", text)
        path.mkdir(parents=True)
        (path / "docs").mkdir()
        (path / "docs" / "plans").symlink_to("../../plans")
        self.assertIn("linked in at docs/plans", pickup.prompt(st, job, "b", path))
        main = self.top / "myapp/.worktrees/pln-07"
        text = pickup.prompt(st, {**self.job, "want": ["myapp", "api"]}, "b", main)
        self.assertIn(f"worktree of the app at {main}", text)
        self.assertIn("linked in at docs/plans", text)

    def test_a_repo_problem_turns_pickup_off(self):
        st = self.tree(pickup=True, repos={"MyApp": "../x"})
        why = pickup.why_off(st, installed=lambda: True, lacks=lambda: [])
        self.assertTrue(why.startswith("Pickup is off: "))
        self.assertTrue(why.endswith('so fix "repos" in board.json.'))
        ok = self.tree(pickup=True)
        self.assertIsNone(pickup.why_off(ok, installed=lambda: True, lacks=lambda: []))

    def test_a_broken_extra_repo_does_not_turn_pickup_off(self):
        st = self.tree(pickup=True, repos={"api": "../gone"})
        self.assertIsNone(pickup.why_off(st, installed=lambda: True, lacks=lambda: []))


class SessionLine(Tree):
    BOARD = "## In flight\n\n- [ ] PLN-07 **Small thing.** The master.\n  - [ ] **Step.**\n"

    def job_in(self, st, *names):
        cuts = [{"name": n, "worktree": str(st.repo(n).path / ".worktrees/pln-07"), "trunk": st.repo(n).trunk} for n in names]
        return {"key": "PLN-07", "id": "ab12cd34", "branch": "pln-07-small-thing", "worktree": cuts[0]["worktree"],
                "repos": cuts, "model": "opus", "started": 1795000000}

    def line(self, st, job):
        text = serve.with_session(st, self.BOARD, job)
        return next(x for x in text.split("\n") if "Claude session" in x)

    def test_a_main_only_job_writes_today_s_line(self):
        job = {"key": "PLN-07", "id": "ab12cd34", "branch": "pln-70-dark-mode", "worktree": "/x/app-pln-70", "model": "opus", "started": 1795000000}
        want = "  Claude session `ab12cd34`: `claude attach ab12cd34`. Branch `pln-70-dark-mode`, worktree `app-pln-70`. Model `opus`. Started " + test_pickup.DAY + "."
        bare = stores.Store(tempfile.mkdtemp())
        self.assertEqual(self.line(bare, job), want)
        self.assertEqual(self.line(bare, {**job, "repos": [{"name": "app", "worktree": "/x/app-pln-70", "trunk": "dev"}]}), want)
        st = self.tree()
        one = self.job_in(st, "myapp")
        self.assertIn("worktree `myapp/.worktrees/pln-07`. Model", self.line(st, one))

    def test_a_multi_repo_job_names_each_worktree_by_repo(self):
        st = self.tree()
        self.assertEqual(self.line(st, self.job_in(st, "api", "web")),
                         "  Claude session `ab12cd34`: `claude attach ab12cd34`. Branch `pln-07-small-thing`, worktrees "
                         "api `api/.worktrees/pln-07`, web `web/.worktrees/pln-07`. Model `opus`. Started " + test_pickup.DAY + ".")

    def test_tile_cuts_reads_both_forms(self):
        st = self.tree()
        old = "  Claude session `ab12cd34`: `claude attach ab12cd34`. Branch `b`, worktree `myapp/.worktrees/pln-07`. Started 2026-11-18."
        self.assertEqual(pickup.tile_cuts(st, old), ("b", [("myapp", st.main_repo, self.top / "myapp/.worktrees/pln-07")]))
        self.assertEqual(pickup.tile_worktree(st, old), self.top / "myapp/.worktrees/pln-07")
        new = self.line(st, self.job_in(st, "api", "web"))
        branch, cuts = pickup.tile_cuts(st, new)
        self.assertEqual(branch, "pln-07-small-thing")
        self.assertEqual(cuts, [("api", st.repo("api"), self.top / "api/.worktrees/pln-07"),
                                ("web", st.repo("web"), self.top / "web/.worktrees/pln-07")])
        self.assertEqual(pickup.tile_worktree(st, new), self.top / "api/.worktrees/pln-07")
        self.assertIsNone(pickup.tile_cuts(st, "- [ ] PLN-07 **Small thing.**"))

    def test_a_repo_gone_from_board_json_is_read_as_unknown(self):
        st = self.tree()
        new = ("  Claude session `ab12cd34`: `claude attach ab12cd34`. Branch `b`, worktrees "
               "api `api/.worktrees/pln-07`, old `old/.worktrees/pln-07`. Started 2026-11-18.")
        self.assertEqual(pickup.tile_cuts(st, new), ("b", [("api", st.repo("api"), self.top / "api/.worktrees/pln-07"), ("old", None, None)]))

    def test_a_main_repo_with_a_space_in_its_name_round_trips(self):
        st = self.tree()
        spaced = self.top / "My App"
        spaced.mkdir()
        cfg = json.loads((st.dir / "board.json").read_text())
        cfg["app"] = "../My App"
        (st.dir / "board.json").write_text(json.dumps(cfg))
        st = stores.Store(st.dir)
        job = self.job_in(st, "My App", "api")
        line = self.line(st, job)
        self.assertIn("worktrees My App `My App/.worktrees/pln-07`, api `api/.worktrees/pln-07`", line)
        self.assertEqual(pickup.tile_cuts(st, line), ("pln-07-small-thing", [
            ("My App", st.main_repo, spaced / ".worktrees/pln-07"), ("api", st.repo("api"), self.top / "api/.worktrees/pln-07")]))

    def test_the_old_form_pattern_does_not_match_the_new_line(self):
        st = self.tree()
        self.assertIsNone(pickup.TILE_BRANCH_RE.search(self.line(st, self.job_in(st, "api", "web"))))

    def test_a_dropped_repo_gone_from_board_json_is_written_by_folder_name(self):
        st = self.tree()
        job = self.job_in(st, "api")
        job["repos"].append({"name": "old", "worktree": str(self.top / "old/.worktrees/pln-07"), "trunk": "dev"})
        self.assertIn("old `pln-07`", self.line(st, job))


class InFlight(Tree):
    """A tile's Repos line changes while its story's session is at work."""

    def setUp(self):
        self.sent = []  # what each session the board launched was given: cwd, dirs, text
        self.stopped = []
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

        def start(args, cwd, permission, text, dirs=()):
            self.sent.append({"args": list(args), "cwd": str(cwd), "dirs": list(dirs), "text": text})
            return {"id": f"new{len(self.sent)}"}

        for p in (mock.patch.object(agent, "start", start), mock.patch.object(agent, "stop", self.stopped.append),
                  mock.patch.object(pickup, "full_id", lambda s: s + "-full")):
            p.start()
            self.addCleanup(p.stop)
        self.noted = []

    def cut(self, st, name):
        repo = st.repo(name)
        return {"name": repo.name, "worktree": str(repo.path / ".worktrees/pln-07"), "trunk": repo.trunk}

    def running(self, st, names, want="same", state="started"):
        """A job in flight in the repos names (cut for real), its state saved on the store."""
        job = {**self.job, "text": "- [ ] PLN-07 **Small thing.**", "state": state, "id": "old1", "session": "old1-full",
               "model": st.model, "started": 1000, "branch": "pln-07-small-thing"}
        cuts, _ = pickup.prepare_all(st, job, [st.repo(n) for n in names])
        job.update(repos=cuts, worktree=cuts[0]["worktree"])
        if names != ["myapp"] or want != "same":
            job["want"] = names if want == "same" else want
        pickup.save(st, {"inflight": ["PLN-07"], "jobs": [job]})
        return job

    def tick(self, st, line, reroute=pickup.switch_repos, sessions=None):
        tile = {"key": "PLN-07", "title": "Small thing", "parent": None,
                "text": "- [ ] PLN-07 **Small thing.**" + (f"\n  Repos: {line}" if line else "")}
        return pickup.tick(st, [tile], sessions=sessions or (lambda: {"old1": "busy"}), now=lambda: 5000,
                           start=lambda store, job, again=False: {"state": "waiting"},  # a queued job stays queued
                           switch=lambda *a: self.fail("model switch"), reroute=reroute,
                           on_start=lambda store, job: self.noted.append(dict(job)))

    def job_after(self, st):
        return pickup.load(st)["jobs"][0]

    def test_adding_a_repo_resumes_the_session_with_it(self):
        st = self.tree()
        self.running(st, ["myapp"])
        self.tick(st, "myapp, api")
        self.assertEqual(self.stopped, ["old1"])
        api = str(self.top / "api/.worktrees/pln-07")
        self.assertEqual(self.sent[0]["dirs"], [api])
        self.assertEqual(self.sent[0]["cwd"], str(self.top / "myapp/.worktrees/pln-07"))
        self.assertIn("--resume", self.sent[0]["args"])
        self.assertIn("repos", self.sent[0]["text"])
        job = self.job_after(st)
        self.assertEqual([c["name"] for c in job["repos"]], ["myapp", "api"])
        self.assertEqual((job["want"], job["state"]), (["myapp", "api"], "started"))
        self.assertEqual(self.head(api), "pln-07-small-thing")
        self.assertEqual(len(self.noted), 1)

    def test_moving_the_starting_repo_starts_a_new_session_there(self):
        st = self.tree()
        self.running(st, ["myapp", "api"])
        self.tick(st, "api, myapp")
        api, app = str(self.top / "api/.worktrees/pln-07"), str(self.top / "myapp/.worktrees/pln-07")
        self.assertEqual(self.stopped, ["old1"])
        self.assertEqual((self.sent[0]["cwd"], self.sent[0]["dirs"]), (api, [app]))
        self.assertNotIn("--resume", self.sent[0]["args"])
        self.assertIn("taking over", self.sent[0]["text"])
        job = self.job_after(st)
        self.assertEqual((job["id"], job["worktree"], job["branch"]), ("new1", api, "pln-07-small-thing"))
        self.assertEqual([c["name"] for c in job["repos"]], ["api", "myapp"])

    def test_a_dropped_repo_stays_on_the_job(self):
        st = self.tree()
        self.running(st, ["myapp", "api"])
        self.tick(st, "myapp")
        job = self.job_after(st)
        self.assertEqual([c["name"] for c in job["repos"]], ["myapp", "api"])
        self.assertEqual(pickup.extra_dirs(st, job), [])
        self.assertEqual(self.sent[0]["dirs"], [])
        self.assertTrue((self.top / "api/.worktrees/pln-07").is_dir())

    def test_a_queued_or_lost_job_just_takes_the_new_repos(self):
        for state in ("waiting", "lost"):
            st = self.tree()
            self.running(st, ["myapp"], state=state)
            self.tick(st, "api", sessions=lambda: {})
            job = self.job_after(st)
            self.assertEqual((job["want"], job["state"]), (["api"], state), state)
        self.assertEqual((self.sent, self.stopped), ([], []))

    def test_no_line_and_a_line_naming_only_main_are_the_same(self):
        st = self.tree()
        self.running(st, ["myapp"])
        self.tick(st, "")
        self.tick(st, "MyApp")
        self.assertEqual((self.sent, self.stopped), ([], []))
        self.running(st, ["myapp"], want=["myapp"])
        self.tick(st, "")
        self.assertEqual((self.sent, self.stopped), ([], []))

    def test_the_same_repos_in_another_case_do_nothing(self):
        st = self.tree()
        self.running(st, ["api", "web"])
        self.tick(st, "WEB, Api")  # a different order is a change; the same order in another case is not
        self.assertEqual(len(self.sent), 1)
        self.sent.clear()
        self.tick(st, "WEB, API")
        self.assertEqual(self.sent, [])

    def test_a_line_that_does_not_resolve_leaves_the_session_alone_and_says_so_once(self):
        st = self.tree()
        self.running(st, ["myapp"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tick(st, "nope")
            self.tick(st, "nope")
        self.assertEqual((self.sent, self.stopped), ([], []))
        self.assertEqual(out.getvalue().count("nope"), 1)
        self.assertEqual(self.job_after(st).get("want"), None)
        self.tick(st, "api")  # fixed: it moves
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("repos_error", self.job_after(st))

    def test_a_queued_job_with_a_bad_line_takes_it_so_the_start_shows_the_error(self):
        st = self.tree()
        self.running(st, ["myapp"], state="waiting")
        self.tick(st, "nope", sessions=lambda: {})
        self.assertEqual(self.job_after(st)["want"], ["nope"])

    def restart(self, st):
        calls = []
        def start(store, job, again=False):
            calls.append(("start", again, job["want"]))
            return pickup.start(store, job, again)
        def resume(store, job):
            calls.append(("resume",))
            return pickup.resume(store, job)
        pickup.restart(st, "PLN-07", start=start, resume=resume)
        return calls

    def test_a_lost_job_whose_first_repo_moved_is_restarted_by_a_new_session_there(self):
        st = self.tree()
        self.running(st, ["myapp", "api"], want=["api", "myapp"], state="lost")
        self.assertEqual(self.restart(st), [("start", True, ["api", "myapp"])])
        self.assertEqual(self.sent[0]["cwd"], str(self.top / "api/.worktrees/pln-07"))
        self.assertEqual(self.job_after(st)["worktree"], str(self.top / "api/.worktrees/pln-07"))

    def test_a_lost_job_whose_first_repo_is_unchanged_still_resumes(self):
        st = self.tree()
        self.running(st, ["myapp", "api"], want=["myapp"], state="lost")
        self.assertEqual(self.restart(st), [("resume",)])
        self.assertIn("--resume", self.sent[0]["args"])

    def test_a_failed_job_takes_the_fixed_line_so_retry_starts_in_the_right_repo(self):
        st = self.tree()
        self.running(st, ["myapp"], want=["nope"], state="failed")
        pickup.save(st, {**pickup.load(st), "jobs": [{k: v for k, v in pickup.load(st)["jobs"][0].items() if k not in ("repos", "worktree", "id", "session")} | {"error": "x", "want": ["nope"]}]})
        self.tick(st, "api", sessions=lambda: {})
        self.assertEqual(self.job_after(st)["want"], ["api"])
        pickup.restart(st, "PLN-07")
        self.assertEqual(self.sent[0]["cwd"], str(self.top / "api/.worktrees/pln-07"))

    def test_a_model_and_repos_change_in_one_tick_restart_the_session_once(self):
        st = self.tree()
        self.running(st, ["myapp"])
        tile = {"key": "PLN-07", "title": "Small thing", "parent": None,
                "text": "- [ ] PLN-07 **Small thing.**\n  Model: sonnet\n  Repos: myapp, api"}
        pickup.tick(st, [tile], sessions=lambda: {"old1": "busy"}, now=lambda: 5000, reroute=pickup.switch_repos,
                    switch=lambda *a: self.fail("switched twice"))
        self.assertEqual(self.stopped, ["old1"])
        self.assertEqual(len(self.sent), 1)
        args = self.sent[0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertEqual(self.sent[0]["dirs"], [str(self.top / "api/.worktrees/pln-07")])
        self.assertEqual(self.job_after(st)["model"], "sonnet")

    def test_restart_after_adding_a_repo_resumes_with_it(self):
        st = self.tree()
        self.running(st, ["myapp"], state="lost")
        self.tick(st, "myapp, api", sessions=lambda: {})
        self.assertEqual(self.sent, [])
        self.restart(st)
        api = self.top / "api/.worktrees/pln-07"
        self.assertEqual(self.sent[0]["dirs"], [str(api)])
        self.assertTrue(api.is_dir())
        self.assertIn("--resume", self.sent[0]["args"])
        self.assertEqual([c["name"] for c in self.job_after(st)["repos"]], ["myapp", "api"])
    def test_a_job_from_before_several_repos_keeps_its_main_cut_when_restarted_elsewhere(self):
        st = self.tree()
        job = self.running(st, ["myapp"], want=["api"], state="failed")
        old = {k: v for k, v in job.items() if k not in ("repos", "id", "session")}
        pickup.save(st, {"inflight": ["PLN-07"], "jobs": [old]})
        self.restart(st)
        after = self.job_after(st)
        self.assertEqual([c["name"] for c in after["repos"]], ["api", "myapp"])
        self.assertEqual(after["repos"][1], {"name": "myapp", "worktree": job["worktree"], "trunk": "dev"})

    def test_a_job_from_before_several_repos_restarted_with_a_new_repo_gets_it(self):
        st = self.tree()
        job = self.running(st, ["myapp"], want=["myapp", "api"], state="lost")
        old = {k: v for k, v in job.items() if k != "repos"}
        pickup.save(st, {"inflight": ["PLN-07"], "jobs": [old]})
        self.restart(st)
        api = self.top / "api/.worktrees/pln-07"
        self.assertEqual(self.sent[0]["dirs"], [str(api)])
        self.assertIn("--resume", self.sent[0]["args"])
        self.assertEqual([c["name"] for c in self.job_after(st)["repos"]], ["myapp", "api"])


def commit(path, message="work"):
    (Path(path) / f"{message}.txt").write_text(message + "\n")
    git(path, "add", f"{message}.txt")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)


class Cut(Tree):
    """A story cut and committed in myapp, api and web, its session line written into a tile."""
    BOARD = "## Done\n\n- [x] PLN-07 **Small thing.**\n"

    def story(self, *names, **extra):
        st = self.tree(**extra)
        git(self.top / "api", "branch", "next")  # a trunk some test gives api
        with open(self.top / "api/.git/info/exclude", "a") as f:  # the store's link is gitignored, as kandalf init leaves it
            f.write("docs/plans\n")
        job = {**self.job, "id": "ab12cd34", "model": "opus", "started": 1795000000}
        cuts, branch = pickup.prepare_all(st, job, [st.repo(n) for n in names])
        for cut in cuts:
            commit(cut["worktree"])
        self.cuts, self.branch = cuts, branch
        self.job = {**job, "branch": branch, "worktree": cuts[0]["worktree"], "repos": cuts}
        self.tile = serve.with_session(st, self.BOARD, self.job).split("\n\n", 1)[1]
        return st

    def has_branch(self, st, name):
        return pickup._git(st.repo(name).path, "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}").returncode == 0

    def land(self, st, name):
        git(st.repo(name).path, "merge", "-q", "--ff-only", self.branch)


class TidyRepos(Cut):
    def tidy(self, st, merged=None, tile=None, job=None, sessions=lambda: {}):
        merged = merged or {}
        self.retired = []
        return pickup.tidy(st, self.tile if tile is None else tile, merged=lambda repo, b: merged.get(repo.name, True),
                           job=job, sessions=sessions, retire=lambda sid: self.retired.append(sid) or True)

    def gone(self, st):
        return [not Path(c["worktree"]).exists() and not self.has_branch(st, c["name"]) for c in self.cuts]

    def test_a_story_landed_in_every_repo_loses_all_its_worktrees_and_branches(self):
        st = self.story("myapp", "api", "web")
        said = self.tidy(st)
        self.assertEqual(said[0], "closed session ab12cd34")
        self.assertEqual(len([s for s in said if s.startswith("removed worktree")]), 3)
        self.assertEqual(len([s for s in said if s.startswith("deleted branch")]), 3)
        self.assertEqual(self.gone(st), [True] * 3)

    def test_one_repo_unmerged_keeps_everything_and_says_which(self):
        st = self.story("myapp", "api", "web")
        said = self.tidy(st, merged={"api": False})
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0], r"^kept branch pln-07-small-thing in myapp and api and web.*session ab12cd34: the branch isn't in api's dev yet$")
        self.assertEqual(self.gone(st), [False] * 3)
        self.assertEqual(self.retired, [])

    def test_one_dirty_worktree_keeps_everything(self):
        st = self.story("myapp", "api", "web")
        (Path(self.cuts[2]["worktree"]) / "scratch").write_text("x\n")
        said = self.tidy(st)
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("kept ") and said[0].endswith("1 uncommitted file in web's worktree"), said)
        self.assertEqual(self.gone(st), [False] * 3)
        self.assertEqual(self.retired, [])

    def test_a_busy_session_keeps_everything(self):
        st = self.story("api", "web")
        said = self.tidy(st, sessions=lambda: {"ab12cd34": "busy"})
        self.assertTrue(said[0].endswith("the session is still at work"), said)
        self.assertEqual(self.gone(st), [False] * 2)

    def test_a_repo_gone_from_board_json_keeps_everything_and_says_so(self):
        st = self.story("api", "web")
        cfg = json.loads((st.dir / "board.json").read_text())
        cfg["repos"] = {"api": "../api"}
        (st.dir / "board.json").write_text(json.dumps(cfg))
        st = stores.Store(st.dir)
        said = self.tidy(st)
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("kept ") and said[0].endswith(": repo `web` isn't in board.json any more"), said)
        self.assertEqual([Path(c["worktree"]).exists() for c in self.cuts], [True, True])
        self.assertEqual(self.retired, [])
        # the same when the board's record of the pickup has to stand in for the tile's line
        said = self.tidy(st, tile="- [x] PLN-07 **Small thing.**\n  Merged.", job=self.job)
        self.assertTrue(said[0].endswith(": repo `web` isn't in board.json any more"), said)
        self.assertEqual([Path(c["worktree"]).exists() for c in self.cuts], [True, True])

    def test_a_tile_without_its_line_is_tidied_from_the_job_s_repos(self):
        st = self.story("myapp", "api")
        said = self.tidy(st, tile="- [x] PLN-07 **Small thing.**\n  Merged.", job=self.job)
        self.assertEqual(said[0], "closed session ab12cd34")
        self.assertEqual(self.gone(st), [True, True])

    def test_an_old_single_repo_tile_is_tidied_as_before(self):
        st = self.story("myapp")
        path = Path(self.cuts[0]["worktree"])
        said = self.tidy(st)
        self.assertEqual(said[:2], ["closed session ab12cd34", "removed worktree pln-07"])
        self.assertRegex(said[2], r"^deleted branch pln-07-small-thing \(was [0-9a-f]{7,}\)$")
        self.assertEqual(len(said), 3)
        self.assertFalse(path.exists())

    def test_a_story_in_one_other_repo_names_it_and_its_trunk(self):
        st = self.story("api", repos={"api": {"path": "../api", "trunk": "next"}})
        said = self.tidy(st, merged={"api": False})
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0], r"^kept branch pln-07-small-thing in api, worktrees api/pln-07, session ab12cd34: the branch isn't in api's next yet$")
        self.assertEqual(self.gone(st), [False])

    def test_an_old_single_repo_tile_keeps_its_old_line(self):
        st = self.story("myapp")
        said = self.tidy(st, merged={"myapp": False})
        self.assertEqual(said, ["kept branch pln-07-small-thing and worktree pln-07 and session ab12cd34: the branch isn't in dev yet"])

    def test_a_job_from_before_several_repos_is_tidied_in_the_main_repo(self):
        st = self.story("myapp")
        old = {k: v for k, v in self.job.items() if k != "repos"}
        said = self.tidy(st, tile="- [x] PLN-07 **Small thing.**\n  Merged.", job=old)
        self.assertEqual(said[:2], ["closed session ab12cd34", "removed worktree pln-07"])
        self.assertEqual(self.gone(st), [True])

    def test_a_branch_a_repo_never_had_is_not_asked_about(self):
        st = self.story("api", "web")
        git(st.repo("web").path, "worktree", "remove", "--force", self.cuts[1]["worktree"])
        git(st.repo("web").path, "branch", "-D", self.branch)
        asked = []
        said = pickup.tidy(st, self.tile, merged=lambda repo, b: asked.append(repo.name) or True, sessions=lambda: {}, retire=lambda s: True)
        self.assertEqual(asked, ["api"])
        self.assertFalse(Path(self.cuts[0]["worktree"]).exists())


class DoneRepos(Cut):
    def test_a_done_tile_is_red_when_its_branch_is_unmerged_in_any_repo(self):
        st = self.story("api", "web")
        board = serve.with_session(st, self.BOARD, self.job)
        self.assertEqual(serve.unmerged_done(st, board), {"PLN-07": [self.branch]})
        self.land(st, "api")
        self.assertEqual(serve.unmerged_done(st, board), {"PLN-07": [self.branch]})  # web still holds it
        self.land(st, "web")
        self.assertEqual(serve.unmerged_done(st, board), {})

    def test_an_unknown_repo_on_a_done_tile_is_skipped(self):
        st = self.story("api", "web")
        board = serve.with_session(st, self.BOARD, self.job)
        cfg = json.loads((st.dir / "board.json").read_text())
        cfg["repos"] = {"api": "../api"}
        (st.dir / "board.json").write_text(json.dumps(cfg))
        st = stores.Store(st.dir)
        self.assertEqual(serve.unmerged_done(st, board), {"PLN-07": [self.branch]})
        self.land(st, "api")
        self.assertEqual(serve.unmerged_done(st, board), {})

    def test_a_branch_token_is_checked_in_the_repos_the_tile_names_even_when_the_line_follows(self):
        st = self.tree()
        git(st.repo("web").path, "branch", "feat/x")
        commit(st.repo("web").path, "ahead")  # on dev: feat/x stays behind, so it is merged
        git(st.repo("web").path, "checkout", "-q", "-b", "feat/y")
        commit(st.repo("web").path, "unmerged")
        git(st.repo("web").path, "checkout", "-q", "dev")
        board = "## Done\n\n- [x] PLN-07 **Small thing.** Branch `feat/y`.\n  Repos: web, nope.\n"
        self.assertEqual(serve.unmerged_done(st, board), {"PLN-07": ["feat/y"]})
        board = "## Done\n\n- [x] PLN-07 **Small thing.** Branch `feat/y`.\n  Repos: api.\n"
        self.assertEqual(serve.unmerged_done(st, board), {})  # api has no such branch
        board = "## Done\n\n- [x] PLN-07 **Small thing.** Branch `feat/y`.\n"
        self.assertEqual(serve.unmerged_done(st, board), {})  # main has none either

    def test_without_the_main_repo_nothing_is_checked(self):
        bare = stores.Store(tempfile.mkdtemp())
        self.assertEqual(serve.unmerged_done(bare, "## Done\n\n- [x] PLN-07 **A.** `feat/y`\n"), {})


class StatusRepos(Cut):
    def status(self, st, tile=None):
        return pickup.status(st, "PLN-07", tile or self.tile, sessions=lambda: {"ab12cd34": "busy"}, why=lambda s: None,
                             screen=lambda s: "", now=time.time)

    def test_status_gives_each_repo_its_part(self):
        st = self.story("api", "web")
        commit(self.cuts[0]["worktree"], "second")
        git(self.cuts[1]["worktree"], "reset", "-q", "--hard", "dev")
        (Path(self.cuts[1]["worktree"]) / "scratch").write_text("x\n")
        said = self.status(st)
        self.assertIn("session `ab12cd34` at work. "
                      'api: 2 commits ahead of dev, last 1 min ago: "second"; nothing uncommitted. '
                      "web: no commits yet; 1 file not committed. ", said)

    def test_a_repo_that_is_unknown_or_has_no_worktree_says_so(self):
        st = self.story("api", "web")
        tile = self.tile.replace("web `web/.worktrees/pln-07`", "old `old/.worktrees/pln-07`")
        self.assertIn(" old: worktree isn't there. ", self.status(st, tile))
        git(st.repo("web").path, "worktree", "remove", "--force", self.cuts[1]["worktree"])
        self.assertIn(" web: worktree isn't there. ", self.status(st))

    def test_a_main_only_status_is_unchanged(self):
        st = self.story("myapp")
        facts = pickup.git_facts(self.cuts[0]["worktree"], "dev")
        want = pickup.status_text("ab12cd34", "busy", facts, "dev", time.time())
        self.assertRegex(self.status(st), "^Status [0-9-]+ [0-9:]+: " + want.replace("(", r"\(").replace(")", r"\)").replace(".", r"\.") + " ")
        self.assertIn("Branch 1 commit ahead of dev; last commit 1 min ago", want)


if __name__ == "__main__":
    unittest.main()
