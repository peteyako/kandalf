# Several repos per store

2026-11-18. Status: draft, for approval.

## Intent

Today a store has one `app` repo. Every picked-up story gets a worktree and branch there, and
that is the only place its session works. The user wants a main repo plus other repos that
sessions can go into and change, for two cases:

1. **One board, several repos.** A story belongs to one repo, not necessarily the main one
   (a story for the API repo, another for the web client).
2. **A story that spans repos.** One session changes several repos together (an API change
   and the client that calls it).

## Goals

- `board.json` names a main repo (as `app` does today) and any number of other repos, by name.
- A tile says which repos its story works in. Without one, it works in the main repo, exactly as now.
- Each repo a story names gets its own worktree, on the same branch, so parallel stories never
  share a checkout. The session starts in the first one and is given the rest with `--add-dir`.
- Tidy on archive, the red "not merged" outline on Done tiles, and the Status button cover
  every repo a story names.
- Adding a repo to a store is one command, and it wires that repo up the way `kandalf init` wires the main one.

## Non-goals

- A session picking up repos by itself partway through (the "on demand" approach below). It may come later, on top.
- Different branch names per repo for one story.
- Coordinated merging across repos. Review and merge stay the user's, repo by repo.
- Agents other than Claude Code.

## Anti-goals (failure even if it works)

- Breaking an existing single-repo store, its `board.json`, its tiles, or the session lines already written on them.
- A session editing a repo's own checkout instead of a worktree.
- Tidy deleting a branch or worktree that holds unmerged or uncommitted work in **any** repo.

## Approaches considered

- **A. The tile names its repos (chosen).** A `Repos: api, web` line, like `Model:`. The board
  cuts worktrees in each repo up front. This is explicit, fits the existing tile conventions, and
  keeps worktrees and cleanup in the board's hands. Trade-off: the repos are chosen before the
  story starts, and adding one partway through means editing the tile.
- **B. Every story gets every repo.** No syntax, but branches and worktrees pile up in repos a
  story never touches, and "this story belongs to api" isn't expressible, because the session
  always starts in the main repo.
- **C. The session takes repos on demand** through a `kandalf` command. This is the most
  flexible, but it relies on the session following instructions, and the tile doesn't say where
  the work will happen.

The user picked A.

## Design

### Configuration (`board.json`, `store.py`)

```json
{
  "app": "../myapp",
  "trunk": "main",
  "repos": {
    "api": "../api",
    "web": { "path": "../web", "trunk": "dev" }
  }
}
```

- `app` stays the **main repo**. Its name is its folder name (`myapp`). `trunk` is its trunk.
  `PLANS_APP_REPO` and `PLANS_APP_TRUNK` keep overriding only the main repo.
- `repos` is optional and maps a name to a path relative to the store, or to `{path, trunk}`.
  A repo's trunk defaults to the store's `trunk`. *(my call)*
- Names are matched case-insensitively. A name that clashes with the main repo's folder name is a
  configuration error, which `kandalf doctor` reports and pickup reports in `why_off`. *(my call)*
- `Store` gains a list of repos (name, path, trunk) with the main repo first, and a lookup by
  name. `store.app` and `store.trunk` keep meaning the main repo, so existing callers keep working.

### The tile line

- `Repos: api, web` (or `Repo: api`) is an indented line of its own, matched like `MODEL_RE`, in
  both `pickup.py` and `board.html`.
- The order matters: the **first** repo is where the session starts (its cwd). The rest are
  passed with `--add-dir`.
- No line means the main repo only. `Repos: api` alone sends the story to the api repo only.
- A sub-story without its own line inherits the nearest master's, through `family_text`, the way
  `Model:` already does.
- An unknown name, or a repo whose folder is missing or isn't a git repo, fails the job with a
  reason ("not started: no repo named `apix` in board.json"). It never falls back to the main repo.

### Pickup (`pickup.py`, `claude_code.py`)

- `prepare` runs once per repo the job names. The worktree path comes from the store's `worktree`
  pattern with `{app}` filled with **that** repo's folder name, under **that** repo's parent
  folder. The branch name is the same in every repo, cut from that repo's trunk. The rules
  `prepare` follows today (reuse an existing worktree or branch) apply per repo.
- The store link `docs/<link>` is made in a worktree only when that repo's own checkout links
  the store, which is what `prepare` already checks.
- `{app}` in the `branch` pattern means the starting repo's folder name. *(my call)*
- If any repo's worktree can't be made, the job fails and says which repo. Worktrees already made
  for it are left in place for Restart. *(my call; rollback would risk deleting a reused worktree)*
- `agent.start` takes the extra directories and passes `--add-dir <path>` for each. `resume` and
  `switch` pass them too. The job records `repos: [{name, worktree, trunk}]`, in tile order.
- The prompt lists every repo the story works in: its name, worktree path, trunk, and the shared
  branch. It names which one the session started in, and says to commit in each repo's worktree
  and to keep work for one repo in that repo's worktree. The "don't merge, don't push" rule
  covers every repo.
- **Changing `Repos:` while in flight** works like changing `Model:`. A session that is at work
  or idle is stopped, any new repo's worktree is cut, and the session is resumed with the new set
  of `--add-dir`. A queued job just starts with the new set. *(my call, for consistency with Model)*
  - **If the change moves the starting (first) repo,** the session's cwd changes, and `--resume`
    is tied to the cwd it started in. The board therefore starts a **new** session in the new
    starting worktree, taking over from the tile and the branches. This is the same path Restart
    takes when it can't resume (`start(..., again=True)`). *(my call)*
  - **A repo dropped from the line** keeps its worktree and branch. The job's `repos` holds every
    repo ever cut for the story, not just the ones on the line now. The session line (rewritten by
    the board on every start, switch and take-over, as it is today) names them all. That way tidy
    and the merge check still see the dropped repo's work. *(my call)*
- **Sub-stories of a master** each get their own branch and worktrees in every repo they work in,
  as today. Inheriting the master's `Repos:` line decides only which repos they use.
- `{app}` in the `worktree` pattern is each repo's own folder name, so the default init pattern
  gives `myapp/.worktrees/pln-74` and `api/.worktrees/pln-74`. That is intended.
- `app` (the main repo) stays required for pickup, even if every story names other repos.
  The user wants a main repo, and it is the default for tiles without a `Repos:` line.
- If `PLANS_APP_REPO` renames the main repo so that its name clashes with an entry in `repos`,
  that is the same configuration error as any other name clash.
- `why_off` and `naming_problem` check each configured repo (exists, is a git repo, worktree
  pattern stays within one folder above that repo's parent).

### The tile's session line

- A single-repo story's session line stays exactly as today.
- A multi-repo (or non-main) story's line also names its repos, so tidy and the merge check can
  work from the tile alone when the board's job record is gone. The exact wording is the
  builder's. Requirements:
  - lines already written still parse (`TILE_BRANCH_RE`, `TILE_SESSION_RE`, `sessionLine` in `board.html`);
  - every repo's worktree can be recovered from the line plus `board.json`. The line carries
    each repo's name and its worktree path relative to **that repo's own** parent folder (e.g.
    `api/.worktrees/pln-74`). Paths are not recomputed from the pattern, so changing the
    `worktree` pattern after pickup doesn't strand tiles already in flight. *(my call)*;
  - the board still shows it as one session block.

### Tidy, the merge check and Status (`pickup.tidy`, `serve.py`)

- **Tidy is all or nothing across a story's repos.** The branch must be in the trunk in every
  repo that has it, no worktree may hold uncommitted files, and the session must not be at work.
  Otherwise everything stays, and the console says which repo is holding it. Then the session
  is closed, every worktree is removed, and the branch is deleted in each repo, each with its last
  commit on the console as now.
- `_branch_merged` takes the repo (path and trunk) to check in.
- **Red outline on Done:** a tile's branches are checked in each repo it names: from the session
  line, else from its `Repos:` line, else the main repo. The outline is red if the branch is
  unmerged in any of them.
- **Status** reports commits ahead, last commit and uncommitted files per repo. For a single
  repo it reads as today.

### The board page (`board.html`)

- The tile dialog gets a **Repos** control beside Model, shown only when the store has more than
  one repo. It is a checkbox per repo, in `board.json` order with the main repo first, and it
  writes or removes the `Repos:` line. Writing it in that order means the first ticked repo is
  where the session starts. A different starting repo can be had by editing the line by hand. *(my call)*
- The server sends the repo names to the page with the rest of the store's settings.
- The session block shows the repo names when there is more than one, or when the story isn't
  in the main repo.

### CLI (`cli.py`, `init.py`)

- `kandalf repo add PATH [--name NAME] [--trunk BRANCH]` adds the repo to `repos` in
  `board.json`. It links the store in at `docs/<link>`, gitignores that link and `.worktrees/`,
  and links the hooks file as `.claude/settings.local.json` when the store has hooks, the same
  way `kandalf init` wires the main repo. It also prints the trust-prompt reminder for that
  repo. The name defaults to the folder name, and the trunk defaults to that repo's default branch.
- `kandalf repo list` lists the main repo and the others with their trunks. `kandalf repo rm
  NAME` takes a repo out of `board.json` and leaves its links alone. *(my call: rm is small and
  saves hand-editing)*
- `kandalf where` lists the repos. `kandalf doctor` adds a line per repo: folder present, git
  repo, trunk exists, store linked in, hooks linked.
- `kandalf init` is unchanged. More repos come afterwards with `repo add`.

### Docs

The README's Settings table (`repos`), Tile conventions (`Repos:`), What a session does,
Commands (`repo add/list/rm`), Safety (sessions may now change every configured repo a tile
names), the `store.py` and `pickup.py` docstrings, and CHANGELOG.

## Constraints

- Python 3.10+, standard library only. git. No new dependencies.
- Claude Code's trust prompt must be accepted once in each repo. kandalf can't check this, so
  `repo add` and `doctor` say so.
- Tests in the existing style (`test_*.py` with unittest, `test_page.js` with node --test). TDD.

## Left to the builder

- The exact wording of the multi-repo session line, within the requirements above.
- How the job state records repos, and the migration of old job records (treat a job with no
  `repos` as main-only).
- How the page gets the repo list (an existing settings response or a new field).
- The wording of console and banner messages.
