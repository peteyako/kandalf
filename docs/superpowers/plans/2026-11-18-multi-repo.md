# Several Repos per Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a store name repos besides its main `app`, and let a tile's `Repos:` line send its story to one or several of them. Each repo gets its own worktree on one shared branch.

**Architecture:** `store.py` gains a `Repo` record and the store's list of repos (main first). `pickup.py` reads the tile's `Repos:` line and cuts a worktree per repo. It starts the session in the first worktree and passes the rest with `--add-dir`, through `claude_code.start`. The job remembers every worktree it ever cut (`job["repos"]`), and the tile's session line names them, so tidy, the Done-tile merge check and Status can cover every repo. `board.html` gets a Repos picker, and `kandalf repo add/list/rm` wires repos up.

**Tech Stack:** Python 3.10+ (stdlib only), git, vanilla JS in `board.html`, `unittest`, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-18-multi-repo-design.md`. Read it before any task; this plan argues from it.

## Global Constraints

- Python 3.10+, standard library only. No new dependencies.
- Existing single-repo stores, `board.json` files, tiles and session lines behave exactly as before. A session line for a main-repo-only story is byte-identical to today's.
- A job with no `repos` and no `want` field (from before this change) means main repo only.
- Sessions never edit a repo's own checkout. Every repo a story works in gets a worktree.
- Tidy is all or nothing across a story's repos.
- Repo names are matched case-insensitively and must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- Tests follow the existing style: `test_*.py` (unittest, real temp git repos where git is involved, a fake `claude`), and `test_page.js` (functions lifted out of `board.html`).
- Before any test run: `sh ~/.claude/bin/memwait.sh`.
- Full gate after each task: `python3 -m unittest discover -p "test_*.py"` (baseline: 152 tests OK) and `node --test test_page.js` (baseline: 22 pass).
- Commit subjects are conventional (`feat(pickup): …`). Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Each task updates the README sections its behaviour touches, in the README's existing voice (plain, short sentences, tables for settings and conventions).

## Review Focus

1. **`--add-dir` is variadic** (`--add-dir <directories...>`). If it comes right before the prompt, it swallows the prompt. The directories must come before `--permission-mode`. Task 3 pins the argv order.
2. **Tiles and jobs from before this change** (old session line, a job with no `repos`/`want`): Restart, Status, tidy and the red outline all work as today. Tasks 4 and 6 pin this.
3. **A repo whose worktree can't be cut** (a mistyped trunk, say): the story is not started, the error names that repo, and the worktrees already cut for it stay for Restart. Task 3 pins this.
4. **`Repos: API, myapp`** (different case, main repo not first): names match case-insensitively and the session starts in `api`. Task 2 pins this.
5. **A repo taken out of `board.json` while a tile's session line still names it:** tidy keeps everything and says which repo it couldn't find, and the Done-tile check skips it without crashing. Task 6 pins this.

---

### Task 1: Repos in the store

**Files:**
- Modify: `kandalf/store.py` (the `Store` class and the module docstring's `board.json` listing)
- Create: `test_repos.py`
- Modify: `README.md` (Settings table: a `repos` row)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Repo: name: str; path: Path; trunk: str; main: bool = False`
  - `Store.repos: list[Repo]`: the main repo first (name `app`'s folder name, trunk `store.trunk`, `main=True`) when `app` is set, then the `repos` entries of `board.json` in file order. A `repos` value is either a path string relative to the store, or `{"path": str, "trunk": str}`. The trunk defaults to `store.trunk`.
  - `Store.main_repo -> Repo | None` (property)
  - `Store.repo(name: str) -> Repo | None`: case-insensitive lookup.
  - `Store.repo_problem: str | None`: one sentence naming the first configuration error, else None. The errors are: a name that clashes with the main repo's name, two names that differ only in case, a name not matching the pattern in Global Constraints, and an entry with no path. A store with problems still builds `repos` from the valid entries.
- `store.app`, `store.trunk`, `PLANS_APP_REPO` and `PLANS_APP_TRUNK` keep their meaning. The env vars affect only the main repo.

- [ ] **Step 1: Write the failing tests** in `test_repos.py`, class `StoreRepos`. Each builds a temp store folder with a `board.json`:
  - `test_a_store_with_only_an_app_has_one_main_repo`: `{"app": "../myapp", "trunk": "dev"}` gives `[Repo("myapp", top/"myapp", "dev", True)]`.
  - `test_repos_follow_the_main_one_in_file_order_with_their_trunks`: `"repos": {"api": "../api", "web": {"path": "../web", "trunk": "next"}}` gives names `["myapp", "api", "web"]`, with trunks `["dev", "dev", "next"]` and paths resolved against the store.
  - `test_a_repo_is_found_by_name_in_any_case`: `st.repo("API").name == "api"`, and `st.repo("nope") is None`.
  - `test_a_name_clashing_with_the_main_repo_is_a_problem`: `"repos": {"MyApp": "../other"}` makes `repo_problem` mention `MyApp`.
  - `test_names_differing_only_in_case_or_badly_formed_are_problems`: covers `{"api": …, "API": …}` and `{"a pi": …}`.
  - `test_plans_app_repo_moves_only_the_main_repo`: with `PLANS_APP_REPO` set (`mock.patch.dict(os.environ, …)`), main's path changes and `api`'s doesn't.
- [ ] **Step 2: Run** `python3 -m unittest test_repos -v`. Expected: FAIL (`Repo` / `repos` missing).
- [ ] **Step 3: Implement** `Repo` and the `Store` attributes above in `kandalf/store.py`, and add `repos` to the module docstring's `board.json` listing.
- [ ] **Step 4: Run** `python3 -m unittest test_repos -v`. Expected: PASS. Then run the full gate.
- [ ] **Step 5: README:** add a `repos` row to Settings (default none: "Other repos stories can work in, by name: a path relative to the store, or `{"path", "trunk"}`. A tile's `Repos:` line picks them.").
- [ ] **Step 6: Commit** `feat(store): name repos besides the main app`.

### Task 2: The tile's `Repos:` line

**Files:**
- Modify: `kandalf/pickup.py` (beside `MODEL_RE`/`model_for`, and `tick` where jobs are queued)
- Test: `test_repos.py` (class `TileRepos`)
- Modify: `README.md` (Tile conventions: a `Repos:` row)

**Interfaces:**
- Consumes: `Store.repos`, `Store.repo`, `Store.main_repo` (Task 1).
- Produces:
  - `REPOS_RE = re.compile(r"^\s+repos?:\s*(.+?)\.?\s*$", re.I | re.M)`: an indented line of its own. It is the same regex in `board.html` (Task 7).
  - `repo_names(text: str) -> list[str] | None`: the names on the first `Repos:` line in `text`, in order, split on commas and/or whitespace, with backticks stripped and duplicates dropped (case-insensitive, first kept). None when there is no line. Because `family_text` puts the tile first and then its masters, the first match is the nearest, which gives sub-story inheritance for free.
  - `resolve_repos(store, names: list[str] | None) -> tuple[list[Repo], str | None]`: None gives `([main], None)`. An unknown name gives `([], "no repo named `apix` in board.json")`. A folder that is missing or isn't a git repo gives `([], "repo `api` isn't there at /path")` or `"… isn't a git repo"`.
  - The job field `"want"`: `repo_names(family_text)` set when `tick` queues a job. It is absent when the tile has no line.

- [ ] **Step 1: Write the failing tests:**
  - `test_no_line_means_the_main_repo`: `repo_names("- [ ] PLN-1 **x**") is None`, and `resolve_repos(st, None)` gives `[main]`.
  - `test_the_line_s_order_is_kept_and_names_match_in_any_case`: `"  Repos: API, myapp"` → `resolve_repos` names `["api", "myapp"]` (Review Focus 4).
  - `test_repo_singular_backticks_and_spaces_are_read`: `"  Repo: `web`"` → `["web"]`, and `"  Repos: api web."` → `["api", "web"]`.
  - `test_a_sub_story_takes_its_master_s_line_unless_it_has_its_own`: uses `family_text` on a two-tile `by_key`.
  - `test_an_unknown_or_missing_repo_is_an_error_not_the_main_repo`: an unknown name, a missing folder, and a folder that isn't a git repo each give `([], <sentence naming it>)`.
  - `test_a_queued_job_carries_what_its_tile_wants`: in the `Queue` style of `test_pickup.py`, a tile with `  Repos: api` lands in flight, and the queued job has `want == ["api"]`.
- [ ] **Step 2: Run** `python3 -m unittest test_repos -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `REPOS_RE`, `repo_names` and `resolve_repos`, and set `want` in `tick`'s queueing loop next to `model_for`.
- [ ] **Step 4: Run** the test file, then the full gate. Expected: PASS.
- [ ] **Step 5: README:** add a Tile conventions row: `Repos: api, web` (own line). "The repos this story works in. The session starts in the first one. No line: the main repo. A sub-story takes its master's."
- [ ] **Step 6: Commit** `feat(pickup): a tile's Repos line picks the repos its story works in`.

### Task 3: Worktrees in each repo, and the session given them all

**Files:**
- Modify: `kandalf/claude_code.py` (`start`, the module docstring)
- Modify: `kandalf/pickup.py` (`names`, `branch`, `worktree`, `prepare`, `start`, `resume`, `switch`, `prompt`, `why_off`)
- Test: `test_repos.py` (classes `Cutting`, `Launch`); `test_pickup.py` stays green unchanged
- Modify: `README.md` ("What a session does with a story": step 1 and 2 for several repos; mention the trust prompt per repo in "Let agents pick up stories")

**Interfaces:**
- Consumes: Task 1 and Task 2 (`resolve_repos`, `job["want"]`).
- Produces:
  - `agent.start(args, cwd, permission: str, text: str, dirs=()) -> dict`: when `dirs` is non-empty, its argv is `[CLI, "--bg", *args, "--add-dir", *dirs, "--permission-mode", permission, text]`. With no dirs, the argv is exactly as today.
  - `names(store, job, repo: Repo = None) -> dict`, `branch(store, job, repo: Repo = None) -> str` and `worktree(store, job, repo: Repo = None) -> Path`. `repo` defaults to the main repo. `{app}` is `repo.path.name`, and the worktree sits under `repo.path.parent`.
  - `prepare(store, job, repo: Repo = None, name: str = None) -> tuple[Path, str]`: today's behaviour for one repo. `name` is the branch to use, defaulting to `branch(store, job, repo)`. The `docs/<link>` link is made only when that repo's checkout has one. Existing callers (`prepare(store, job)`) are unchanged.
  - `prepare_all(store, job, repos: list[Repo]) -> tuple[list[dict], str]`: cuts `repos[0]` first, and its branch (the existing one, when reused) is used in all the others. It returns `([{"name", "worktree": str, "trunk"}], branch)` in `repos` order. On a failure it raises `RuntimeError(f"{repo.name}: {git's error}")` and leaves the already-cut worktrees in place.
  - `job["repos"]`: every cut ever made for the job, as dicts like the above. The current `want` order comes first, then any dropped ones. `job["worktree"]` stays the starting worktree, and `job["branch"]` the shared branch.
  - `extra_dirs(store, job) -> list[str]`: the worktrees of the job's wanted repos other than the starting one, in order.
  - `prompt(store, job, branch_name, path, again=False)`: when the job works in more than the main repo, or in a repo that isn't main, it adds a "Repos" paragraph. That paragraph lists each wanted repo's name, worktree path and trunk, says which repo the session is in, and says the branch is shared. It says to commit in each repo's own worktree and that "don't merge, don't squash, don't push" covers every repo. For a main-only job the prompt is unchanged.
  - `why_off` also returns `store.repo_problem` (prefixed `Pickup is off: `, ending `, so fix "repos" in board.json.`). A missing or broken *extra* repo does not turn pickup off. It fails only the stories that name it, via `resolve_repos`. *(This is a plan decision that narrows the spec's "why_off checks each configured repo": one broken extra repo shouldn't stop every story. `kandalf doctor` reports it, in Task 8.)*

- [ ] **Step 1: Write the failing tests.** Use a temp tree with `myapp`, `api` and `web` git repos (trunk `dev`; `api` links the store at `docs/plans`, `web` doesn't). Use the store pattern `{app}/.worktrees/{key}`.
  - `Cutting.test_each_repo_gets_a_worktree_on_one_branch`: `prepare_all(st, job, [api, web])` gives worktrees `api/.worktrees/pln-07` and `web/.worktrees/pln-07`, both on `pln-07-small-thing`. Only `api`'s worktree has `docs/plans`.
  - `Cutting.test_the_branch_pattern_s_app_is_the_starting_repo`: with `"branch": "{app}/{key}"`, the branch is `api/pln-07` in both repos.
  - `Cutting.test_a_repo_that_cannot_be_cut_is_named_and_the_others_stay`: `web` has trunk `nope`. A `RuntimeError` starting `web:` is raised, and `api`'s worktree still exists (Review Focus 3).
  - `Cutting.test_cutting_again_reuses_every_worktree_and_the_branch`.
  - `Launch.test_the_other_worktrees_go_in_add_dir_before_the_permission_mode`: patch `subprocess.run` in `claude_code`, and assert the argv is `[..., "--add-dir", p1, p2, "--permission-mode", "auto", text]` (Review Focus 1).
  - `Launch.test_start_runs_in_the_first_wanted_repo_and_records_every_cut`: with `pickup.start(st, {"key": …, "want": ["web", "api"]})` and `agent.start` faked, the cwd is the `web` worktree, `dirs == [api worktree]`, and `job["repos"]` names `web` and `api`.
  - `Launch.test_an_unknown_repo_fails_the_story_with_the_reason`: `want: ["apix"]` → `{"state": "failed", "error": …"apix"…}` and no worktree.
  - `Launch.test_resume_passes_the_same_extra_dirs`.
  - `Launch.test_the_prompt_lists_each_repo_only_when_there_is_more_than_main`: the main-only prompt is equal to `prompt` before this change (compare against a job with no `want`); the multi-repo prompt contains each worktree path and "every repo".
  - `Launch.test_a_repo_problem_turns_pickup_off`.
- [ ] **Step 2: Run** `python3 -m unittest test_repos -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above. `start` resolves `job.get("want")`, calls `prepare_all`, and passes `extra_dirs` to `_launch`, which takes `dirs` and hands them to `agent.start`. `resume`/`switch` pass `extra_dirs(store, job)`. Update `claude_code.py`'s docstring, which lists the arguments `start` gets, to include `dirs`.
- [ ] **Step 4: Run** the test file, then the full gate. Expected: PASS, with `test_pickup.py` unchanged and green.
- [ ] **Step 5: README:** update "What a session does with a story" (a worktree per repo on one branch; the other repos are given to the session) and the trust note (accept the prompt once in each repo).
- [ ] **Step 6: Commit** `feat(pickup): cut a worktree in each of a story's repos`.

### Task 4: The session line names every repo

**Files:**
- Modify: `kandalf/serve.py` (`with_session`)
- Modify: `kandalf/pickup.py` (`worktree_label`, `tile_worktree`, new `TILE_REPOS_RE`, `tile_cuts`)
- Test: `test_repos.py` (class `SessionLine`); the existing `Note` tests in `test_pickup.py` stay green unchanged

**Interfaces:**
- Consumes: `job["repos"]` (Task 3).
- Produces:
  - The line format. A job whose `repos` is absent or is just the main repo writes today's line, unchanged. Otherwise the line reads: ``Claude session `id`: `claude attach id`. Branch `b`, worktrees api `api/.worktrees/pln-07`, web `web/.worktrees/pln-07`. Model `opus`. Started 2026-11-18.`` It names every repo in `job["repos"]`, dropped ones too.
  - `worktree_label(store, path, repo: Repo = None) -> str`: relative to `repo.path.parent` (default main), with the same fallback as today.
  - `TILE_REPOS_RE`: it matches the `worktrees` form and captures the branch and the `name `path`` list. `TILE_BRANCH_RE` is unchanged; it doesn't match the new form.
  - `tile_cuts(store, tile_text) -> tuple[str, list[tuple[str, Repo | None, Path | None]]] | None`: (branch, [(repo name, Repo or None if board.json no longer has it, worktree path or None)]). An old-form line gives `(branch, [(main.name, main, path)])`. No session line gives None.
  - `tile_worktree` keeps its signature and returns the first cut's path.
- `board.html`'s `SESSION_RE` already accepts the new line; no page change is needed here.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_main_only_job_writes_today_s_line`: compare against the literal string from the existing `Note` test.
  - `test_a_multi_repo_job_names_each_worktree_by_repo`: the exact line above.
  - `test_tile_cuts_reads_both_forms`: an old line → main; a new line → api and web, with paths under each repo's parent.
  - `test_a_repo_gone_from_board_json_is_read_as_unknown`: the new line names `old` → `("old", None, None)` (Review Focus 5, the reading half).
  - `test_the_page_still_reads_the_new_line_as_a_session`: in `test_page.js`, `sessionLine(newLine)` gives the id and the `rest`.
- [ ] **Step 2: Run** `python3 -m unittest test_repos -v` and `node --test test_page.js`. Expected: the new Python tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full gate. Expected: PASS.
- [ ] **Step 5: Commit** `feat(serve): the session line names a story's worktree in each repo`.

### Task 5: Changing `Repos:` while a story is in flight

**Files:**
- Modify: `kandalf/pickup.py` (`tick`, a new `_rerepo`, a new `switch_repos`, `restart`)
- Test: `test_repos.py` (class `InFlight`, using `test_pickup.Fake`-style fakes)
- Modify: `README.md` ("Model." paragraph's sibling: one paragraph on changing repos in flight)

**Interfaces:**
- Consumes: Tasks 2-4 (`repo_names`, `resolve_repos`, `prepare_all`, `extra_dirs`, `start`, `resume`, `TILE_SESSION_RE`).
- Produces:
  - `tick(..., reroute=switch_repos)`: a new injectable, like `switch`. In the same loop as `_remodel`, it calls `_rerepo(store, job, repo_names(family_text(...)), reroute, now(), on_start)`.
  - `_rerepo(store, job, names, reroute, at, on_start=None)`: it compares the case-folded resolved names (None and `[main.name]` are the same). If they're unchanged, it does nothing. For a queued, lost or failed job it just sets `want`, and the job starts with it next time. For a started or idle job it calls `reroute`, and on success it updates the job and `_report`s with how `"repos changed to api, web"`, so `on_start` rewrites the session line.
  - `switch_repos(store, job, names) -> dict`: it stops a session that is at work. If the starting repo stays the same, it cuts any new worktree (`prepare_all`) and resumes with the new `extra_dirs` and a `REPOS_CHANGED` message ("The plans board changed this story's repos to …; your session was restarted with them. …"). If the starting repo changes, it calls `start(store, {**job, "want": names}, again=True)`, a new session in the new starting worktree. Either way `job["repos"]` stays the union of every cut. It returns fields as `start` does.

- [ ] **Step 1: Write the failing tests:**
  - `test_adding_a_repo_resumes_the_session_with_it`: the job runs in `myapp`, the tile's line becomes `Repos: myapp, api`, and `reroute` resumes with `api` in the dirs.
  - `test_moving_the_starting_repo_starts_a_new_session_there`: `Repos: api, myapp` → `start` is called `again=True` with the cwd in `api`'s worktree.
  - `test_a_dropped_repo_stays_on_the_job`: `myapp, api` → `myapp` gives `job["repos"]` that still names `api`, while `extra_dirs` is empty.
  - `test_a_queued_or_lost_job_just_takes_the_new_repos`.
  - `test_no_line_and_a_line_naming_only_main_are_the_same`: no reroute.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above. `restart` already calls `start` or `resume`, which read `want`, so it needs nothing beyond that.
- [ ] **Step 4: Run** the full gate. Expected: PASS.
- [ ] **Step 5: README:** a "Repos." paragraph next to "Model." (change the line while the story is in flight and its session is restarted with the new set; a new first repo means a new session that takes over from the tile).
- [ ] **Step 6: Commit** `feat(pickup): follow a tile's Repos line while its story is in flight`.

### Task 6: Tidy, the Done-tile check and Status cover every repo

**Files:**
- Modify: `kandalf/pickup.py` (`tidy`, `status`, a new `repo_status_text`)
- Modify: `kandalf/serve.py` (`_app_git`, `_branch_merged`, `tidy_archived`, `unmerged_done`)
- Test: `test_repos.py` (classes `TidyRepos`, `DoneRepos`, `StatusRepos`); the existing `Tidy` and `Status` tests in `test_pickup.py` are updated only where `merged`'s signature changes
- Modify: `README.md` ("Archiving tidies." and the Tile conventions red-outline row)

**Interfaces:**
- Consumes: `tile_cuts` (Task 4), `job["repos"]` (Task 3), `repo_names`/`resolve_repos` (Task 2).
- Produces:
  - `serve._app_git(store, *args, repo: Repo = None)` and `serve._branch_merged(store, branch, repo: Repo = None) -> bool | None`: `repo` defaults to main, and the trunk is `repo.trunk`.
  - `pickup.tidy(store, tile_text, *, merged, job=None, sessions=…, retire=…) -> list`: `merged(repo: Repo, branch: str) -> bool | None`. The cuts come from `tile_cuts`, else `job["repos"]`, else the job's single worktree on main. It's all or nothing. If any repo has the branch unmerged, or any worktree is dirty, or the session is busy, or a cut names a repo that isn't in `board.json`, nothing goes, and one line names the repo that holds it (e.g. ``kept branch b in api and web, worktrees …, session …: the branch isn't in api's dev yet``). Otherwise the session is retired first, then each worktree is removed and the branch is deleted in each repo, one console line each, as today. For a main-only story the console lines are unchanged.
  - `serve.tidy_archived` passes `merged=lambda repo, b: _branch_merged(store, b, repo)`.
  - `serve.unmerged_done(store, text) -> dict`: for each Done tile, the repos to check are its session line's cuts (known repos only), else its `Repos:` line (known names only), else main. A branch counts as unmerged if `_branch_merged` is False in any of them. It still returns `{}` when the main repo isn't there.
  - `pickup.status(...)`: a main-only story's line is byte-identical to today's. With several repos, after `session `x` at work.` comes one part per repo from `repo_status_text(name, facts, trunk) -> str`, such as `api: 2 commits ahead of dev, last 5 min ago: "subject"; nothing uncommitted.`, `web: no commits yet; 1 file not committed.` or `old: worktree isn't there.`. The ETA follows, as today.

- [ ] **Step 1: Write the failing tests** (real temp repos, as in `test_pickup.Tidy.setUp`):
  - `TidyRepos.test_a_story_landed_in_every_repo_loses_all_its_worktrees_and_branches`.
  - `TidyRepos.test_one_repo_unmerged_keeps_everything_and_says_which`: `merged` is True for `myapp` and False for `api` → nothing removed, and the line names `api`.
  - `TidyRepos.test_one_dirty_worktree_keeps_everything`.
  - `TidyRepos.test_a_repo_gone_from_board_json_keeps_everything_and_says_so` (Review Focus 5).
  - `TidyRepos.test_a_tile_without_its_line_is_tidied_from_the_job_s_repos`.
  - `TidyRepos.test_an_old_single_repo_tile_is_tidied_as_before` (Review Focus 2): the existing fixture with the new `merged` signature, and the lines equal today's.
  - `DoneRepos.test_a_done_tile_is_red_when_its_branch_is_unmerged_in_any_repo` and `DoneRepos.test_an_unknown_repo_on_a_done_tile_is_skipped`.
  - `StatusRepos.test_status_gives_each_repo_its_part` and `StatusRepos.test_a_main_only_status_is_unchanged`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above. Restructure `unmerged_done` to gather each Done tile's full text before checking it, because a `Repos:` line can follow the branch token.
- [ ] **Step 4: Run** the full gate. Expected: PASS.
- [ ] **Step 5: README:** "Archiving tidies." (every repo the story worked in; nothing goes unless it's safe in all of them), and the red-outline row (checked in each repo the tile names).
- [ ] **Step 6: Commit** `feat(pickup): tidy, merge check and status across a story's repos`.

### Task 7: The board page's Repos picker

**Files:**
- Modify: `kandalf/serve.py` (`/api/files` reply)
- Modify: `kandalf/board.html` (globals near `STORE_MODEL`; `REPOS_RE`, `reposOf` and `withRepos` beside `withModel`; the dialog markup and handler next to `#cardModel`; the body filters at the `MODEL_RE` sites, around lines 749, 848 and 1434; a tile-face tag like `modelTag`)
- Test: `test_page.js` (add `reposOf` and `withRepos` to the lifted exports); `test_repos.py` (`/api/files` carries repos)

**Interfaces:**
- Consumes: `Store.repos` (Task 1); `REPOS_RE` (Task 2, same pattern).
- Produces:
  - `/api/files` adds `"repos": [{"name": str, "main": bool}]` in `Store.repos` order.
  - Page globals: `STORE_REPOS = []`, set where `STORE_MODEL` is set.
  - `reposOf(md: string) -> string[]`: the names on the tile's `Repos:` line (body lines only, not the first line), or `[]`.
  - `withRepos(md: string, names: string[]) -> string`: `[]`, or just the main repo, removes the line. Otherwise it writes `  Repos: a, b` in `STORE_REPOS` order, replacing an existing line in place, or else putting it where `withModel` puts a new Model line.
  - The dialog has a `#cardRepos` group with one checkbox per `STORE_REPOS` entry, hidden when there are fewer than two. Opening a tile ticks its current repos (or main, for none). Saving runs `withRepos`. A hand-written order on the line is kept when the ticked set hasn't changed. *(plan decision: the picker must not reorder a line the user wrote by hand.)*
  - The `Repos:` line is left out of the rendered description, as the `Model:` line is. The tile face shows a small `⎇ api, web` tag when the line is present.

- [ ] **Step 1: Write the failing tests** in `test_page.js`:
  - `reposOf reads the line in order and ignores the first line`.
  - `withRepos writes, replaces in place and removes the line`, including that `withRepos(md, ["myapp"])` with main `myapp` removes it.
  - `withRepos puts a new line before the first sub-task, as withModel does`.
  - And in `test_repos.py`: `test_api_files_lists_the_store_s_repos_main_first`, using the handler the same way the existing serve tests call it.
- [ ] **Step 2: Run** `node --test test_page.js` and `python3 -m unittest test_repos -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full gate, then `kandalf check plans.md` on a sample board with a `Repos:` line to confirm it still round-trips. Expected: PASS, with no view-only error.
- [ ] **Step 5: Manual look.** Run `kandalf serve --dir <temp store with two repos> --no-open`. Open a tile and confirm the picker shows, ticks and saves the line, and is hidden for a one-repo store. Use the `run` skill or a browser.
- [ ] **Step 6: Commit** `feat(page): a Repos picker in the tile dialog`.

### Task 8: `kandalf repo`, `where` and `doctor`

**Files:**
- Modify: `kandalf/init.py` (new `add_repo`, `remove_repo`, `repo_lines`, `repo_checks`; `checks`)
- Modify: `kandalf/cli.py` (the `repo` command, the usage docstring, `where`)
- Test: `test_init.py` (class `RepoCommands`)
- Modify: `README.md` (Commands, Safety, Development file list if needed), `CHANGELOG.md` (an Unreleased entry for the whole feature)

**Interfaces:**
- Consumes: `Store.repos`, `Store.repo_problem` (Task 1); existing `link_app`, `link_hooks`, `has_hooks`, `default_trunk`, `TRUST` and `git_root` in `init.py`/`store.py`.
- Produces:
  - `add_repo(st, path: Path, name: str = None, trunk: str = None) -> list[str]`: `path` must be in a git repo (its `git_root` is used). The name defaults to the folder name, and the trunk to `default_trunk(repo)`. It refuses (`SystemExit` with a sentence) a name the store already has or one that clashes with main. It writes `board.json`'s `repos[name]` as the path relative to the store, or as `{"path", "trunk"}` when the trunk differs from the store's `trunk`. It keeps the other keys and their order, writing `json.dumps(cfg, indent=2) + "\n"`, and queues the board.json change in the commit queue as other store edits are. It runs `link_app(repo, st.dir, st.link)`, and `link_hooks(repo, st.hooks_file)` when `has_hooks(st.hooks_file)`. It returns what it did, ending with `TRUST` filled in for that repo.
  - `remove_repo(st, name) -> list[str]`: it deletes the entry (case-insensitive) and leaves the repo's links alone. It refuses the main repo or an unknown name.
  - `repo_lines(st) -> list[str]`: `myapp (main) ../myapp @ dev`, `api ../api @ dev`, …
  - `repo_checks(st, repo) -> list[(mark, text)]`: what `app_checks` does today for one non-main repo (there, git repo, trunk exists, store linked at `docs/<link>`). A missing hooks link is INFO. `checks` adds `(BAD, st.repo_problem)` when there is one, and `repo_checks` for each non-main repo.
  - CLI: `kandalf repo add PATH [--name NAME] [--trunk BRANCH]`, `kandalf repo list` and `kandalf repo rm NAME`, all taking `--dir`. The usage docstring gets three lines. `where` prints one `repo …` line per `repo_lines` entry, after the `app` line.

- [ ] **Step 1: Write the failing tests** in `test_init.py`, using temp store and repos as the existing init tests do:
  - `test_repo_add_names_it_links_the_store_and_ignores_the_links`: `board.json` gains `"api": "../api"`; `api/docs/plans` leads to the store; `.gitignore` covers `docs/plans` and `.worktrees/`; the output mentions the trust prompt.
  - `test_repo_add_records_a_trunk_that_differs_from_the_store_s`.
  - `test_repo_add_links_the_hooks_when_the_store_has_them`.
  - `test_repo_add_refuses_a_clash_or_a_folder_that_is_not_a_repo`.
  - `test_repo_add_keeps_board_json_s_other_keys_in_order`.
  - `test_repo_rm_takes_it_out_and_leaves_its_links`, and `test_repo_rm_refuses_the_main_repo`.
  - `test_doctor_checks_each_repo_and_reports_a_repo_problem`.
  - `test_where_lists_the_repos`.
- [ ] **Step 2: Run** `python3 -m unittest test_init -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full gate. Expected: PASS.
- [ ] **Step 5: README:** add Commands lines for `repo add/list/rm`. Update Safety so sessions may change every repo a tile names, in worktrees, and are told not to merge or push in any. **CHANGELOG:** add an Unreleased "Several repos per store" entry summarising Tasks 1-8.
- [ ] **Step 6: Commit** `feat(cli): kandalf repo add, list and rm`.

### Finish

- [ ] Full gate: both test commands, each tier run separately after `memwait.sh`. Report the counts.
- [ ] A final whole-branch review by a fresh Opus reviewer, against the spec and this plan's Review Focus.
