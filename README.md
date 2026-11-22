# kandalf

A wizard for your backlog, straight outta middle management.

This tool is neither original nor clever. It's a stupider version of something a hundred vibe-coded GitHub repos have probably built already. But this one is my stupid, so I love it. It's also useful to me, doing just what I really need. And that is enough. Until it isn't. And that's how we get new features. 

It's a local kanban board for planning work for AI agents, served on your machine. Drag a story to In flight and a background Claude Code or GitHub Copilot CLI session starts on it, in its own git worktree. Everything is markdown in git: no database, and nothing to install beyond Python 3.10+ and git.

For the love of Sauron, keep it on your own machine: for now, kandalf is meant to be a local tool for managing your plans and the AI sessions that work on them. It isn't built to be hosted or shared over a network.

Feel free to contribute if you have nothing better to do, or tokens to burn.

![kandalf: a wizard at a kanban board](docs/kandalf.jpg)

## Quick start

Install it. Either clone it and put the `kandalf` command on your PATH:

```bash
git clone https://github.com/peteyako/kandalf.git
ln -s "$PWD/kandalf/bin/kandalf" ~/.local/bin/kandalf     # any folder on your PATH
```

Or use pipx, which puts `kandalf` on your PATH:

```bash
pipx install git+https://github.com/peteyako/kandalf.git
```

Then, from inside the repo you want agents to work on:

```bash
kandalf init --example      # a plans store beside the repo, with a sample story
kandalf serve               # the board, on http://127.0.0.1:7788 (or the port init picked), opened in your browser
```

`kandalf init` makes a folder beside your repo, named for the name you give it (`<repo>-plans` if you keep the default): a git repo of its own holding the board and its plan docs. It picks a free port and a key prefix, and links the store into your repo at `docs/plans` (a gitignored symlink). You get a board with the columns Bugs, To do, In flight and Done, a sample master story with two sub-stories, and a backlog board.

### Let agents pick up stories

Pickup is off until you turn it on. Turning it on lets the board server start Claude Code sessions in your repo, one per story that lands in In flight. By default a session edits files and runs commands without asking (Claude Code's auto mode decides what is allowed). It works in its own worktree and branch, and is told to stop before merging or pushing.

Turn it on with `"pickup": true` in the store's `board.json`, or answer yes when `kandalf init` asks. `kandalf doctor` lists what works and what is missing. A running `kandalf serve` reads `board.json` again within a few seconds of a change, so pickup comes on (picking up the tiles already in flight) or goes off without a restart; turning it off leaves the sessions at work alone.

Before the first story, run `claude` once in your repo and accept its trust prompt. Claude Code won't start a background session in a repo it doesn't trust, and the tile will say so. Trusting the repo covers the worktrees cut from it. A story that works in several repos (`Repos:` on its tile) needs the prompt accepted once in each of them. A store made by `kandalf init` keeps those worktrees inside the repo, under `.worktrees/` (gitignored).

Pickup needs an agent CLI: [Claude Code](https://claude.com/claude-code) (the default) or the GitHub Copilot CLI (see [Platforms and requirements](#platforms-and-requirements)). Pick the agent when `kandalf init` asks (or pass `--agent copilot`), switch a store later with `kandalf agent copilot`, or set `"agent": "copilot"` to use the [GitHub Copilot CLI](https://github.com/features/copilot/cli) (`copilot`) instead: each session is a detached `copilot -p --output-format json` process, tracked under `~/.kandalf/copilot/` (`KANDALF_COPILOT_DIR` overrides it). The tile's attach command is `kandalf attach ab12cd34`: it prints what the session has said and the tools it ran, follows it while it works (Ctrl-C leaves it working), and once it has finished opens the conversation in `copilot --resume` in its worktree, so you can read it and carry on, with the folders and permissions it ran with. Quit it (`/exit`) before Restart on the tile: while it is open, the board can't resume the session. A Copilot session can't stop to ask, so Status and the tile call it waiting for your input when it ended on an error or with a question. Copilot reads Claude Code hooks from a repo's `.claude/settings.local.json`, so a Copilot session gets the board's hooks through a link there in its worktree (kept out of git by the repo's `info/exclude`). A Copilot session reaches only its worktree and the folders it is given, so it is given the store too (`--add-dir`), for the board and plan docs behind `docs/plans`. `permission` `auto` runs with `--allow-all-tools --allow-all-paths` (any tool, any file path; URLs are still checked), `acceptEdits` with `--allow-all-tools`, `bypassPermissions` with `--allow-all`, `plan` with `--mode plan`, others ask for nothing and so deny what isn't pre-approved. Run the agent (`claude` or `copilot`) once in your repo and accept its trust prompt before the first pickup.

`kandalf agent NAME` switches an existing store: it sets `agent`, drops `model` (it was the old agent's), and leaves each story the old agent picked up lost, so Continue on its tile starts the new agent in the same worktree. It refuses while an old session is at work or idle; `--force` stops those first.

## What a session does with a story

While `kandalf serve` runs for a store with an `app` and `"pickup": true`, a keyed tile that lands in the In flight column of `plans.md` is picked up, however it got there: dragged on the board, edited in by hand, or moved by an agent. The server:

1. Cuts a git worktree on a branch from the trunk (or the branch the tile's `Base:` line names): worktree `myapp/.worktrees/pln-74` in a store made by `kandalf init` (beside the app, as `myapp-pln-74`, when `"worktree"` isn't set), branch `pln-74-colour-tokens`. The store is linked in at `docs/plans` there too. `"branch"` and `"worktree"` in `board.json` change the names. A tile with a `Repos: api, web` line gets a worktree in each of those repos, all on that one branch, each beside its own repo. The store is linked in only where the repo's checkout links it.
2. Starts a background Claude Code session in it (in the first repo's worktree, when there are several; the others are added to the session with `--add-dir`, and its prompt lists them), given the tile and told to follow the repo's rules, tick sub-tasks as they land with their commits, keep a `Handoff:` line on the tile, and stop before merging or pushing. The board writes the session, branch and worktree onto the tile itself.

When the server starts, a tile already in flight that never had a session (no session line on it, and no sub-stories) is picked up too. Unkeyed tiles and tiles that say `No pickup` never are (take the words out and the tile is picked up on the next look); the server's startup list says when a tile's `No pickup` is why it has no session.

**On the tile.** The session is written into the tile as one line: ``Claude session `ab12cd34`: `kandalf attach ab12cd34`. Branch `pln-74-colour-tokens`, worktree `myapp-pln-74`. Model `opus`. Started 2026-11-18.`` A Copilot store's line says ``Copilot session `ab12cd34`: `kandalf attach ab12cd34`.`` A story in several repos names each one's worktree instead: ``Branch `pln-74-colour-tokens`, worktrees api `api/.worktrees/pln-74`, web `web/.worktrees/pln-74`.`` The board shows it as a block with the session's state and a button that copies the attach command:

- **working**: the session is at work. The block gets **Pause** (below).
- **pausing**: it was paused and is writing its handoff before it stops. The block gets **Pause now**, which stops it there if it hangs, leaving the Handoff line the tile has.
- **paused**: it wrote its handoff and stopped. The block gets **Continue**.
- **idle**: it finished its turn. The block gets **Continue**.
- **waiting for you**: idle because it needs input (interrupted, or it asked a question). The tile gets an amber outline. This is a best guess from `claude logs`.
- **gone**: it was stopped, closed or removed. The tile flashes red until you continue it or move it out of In flight. The block gets **Continue**.

**Pause** puts a story aside. It stops the session (Claude or Copilot) and resumes it once, told to finish or undo the step it is on, commit what is worth keeping, rewrite the tile's `Handoff:` line in full (done, uncommitted, next, undecided) and stop. Once it stops the story is **paused**: it gives up its place, so the next queued tile starts. Moving a tile out of In flight while its session is at work, to To do or any other column, pauses it the same way.

**Continue** gets a stopped session going again. On a paused story a new session takes over from the tile's `Handoff:` line and the branch, in the same worktree; the old conversation isn't needed, so this works on another day, after a server restart, or with the store switched to another agent. **Queue**, beside it, puts the paused story at the back of the queue to be taken over in its turn instead of now. A paused tile out of In flight has no button: move it back to In flight and it queues, at the back, for that take-over (**queued**, with **Start now** to start it beside the session at work). The take-over runs on the model and repos the tile names then. On an idle or gone session, Continue resumes the same conversation, told to carry on from where it was, check `git status` and the tile, and take another way rather than wait again; if the conversation can't be resumed, a new session takes over in the same worktree.

**Status** in the block checks the story right now and writes the result under the session line: session state, commits ahead of the trunk, the last commit, uncommitted files, sub-tasks ticked and an ETA, and the last words on the session's screen. The board measures it from the CLI's session list, git in the worktree and the tile; it asks the session for nothing. The ETA goes by the pace of ticked sub-tasks, or, with none ticked, by what is left of the tile's estimate.

**Handoff.** Each session keeps an indented `Handoff:` line on the tile: what is done, what it is doing, what comes next. The tile and the branch are all a new session needs, so **Continue** resumes the same conversation, or, if it can't be resumed, starts a new session in the same worktree; after a Pause it always starts a new one from the handoff.

**Ready for review.** When every sub-task is ticked and every gate has passed, the session adds a `**Ready for review:**` line (gate results, commit count, what to look at first) and stops. The board outlines the tile in green until it is in Done or the line is removed. You review, squash and merge.

**Message.** Every session the board can reach, Claude or Copilot, has a message box in its block, and **Send** (⌘↵ or Ctrl+↵ sends too) puts what you type into the same conversation. When the session waits for your input (it asked something, ended on an error, or its story is ready for review and it stopped) the box is open. Otherwise it is folded behind a chevron: unfold it to **Interrupt with a message** while the session works, which stops it mid-step, gives it your message, and has it carry on from where it was; or to message one that is idle or gone. A paused story has Continue instead.

**Log.** The same box shows the session's log above the message field: what it was told (`❯`), what it said and each tool it ran (`⏺`), and the start of each result (`⎿`), laid out as the session's own screen lays them out, newest last, refreshed every few seconds while the box is open. It stays at the newest line unless you scroll up to read, is taller in an opened tile, and **↗ window** opens it full-size in a tab of its own (`/log?key=PLN-01`). A Claude session's log is read from Claude Code's own transcript of it under `~/.claude/projects`; a Copilot session's from the output the board keeps. On a story ready for review the box also has **Squash**, **Merge into** the trunk and **Push**: tick them and the session does those, in that order, though its rules otherwise leave them to you. Squash rebases onto the trunk first and squashes only the story's own commits; Merge rebases onto the trunk and fast-forwards it, so there is no merge commit; each runs in every repo the story works in. Push after Merge pushes the trunk, and Push alone pushes the story's branch. The session stops at the first step that can't be done cleanly and says why on the tile. After a Merge it takes the `Ready for review` line off; without one the line stays, as the story still waits to be merged. A merged story with nothing outstanding (every sub-task ticked, every step done) is closed: the session moves its tile to the top of Done, as the board's header says to close one. With something outstanding the tile stays in In flight and its `Handoff:` line says what. The go-ahead is for that reply only: any later reply tells the session squashing, merging and pushing are yours again. A story with nothing to merge (its branch has no commits beyond the trunk and nothing uncommitted, in every repo, as with a master whose sub-stories did the work) has a **✓ Close** button instead of the three: the session takes the `Ready for review` line off and closes the story the same way, without squashing, merging or pushing.

**Stats.** While pickup is on, a strip under the plans board's header counts the builds: **at work**, **need you** (the session asked something, ended on an error, is gone, or couldn't be started), **ready for review** (which a finished story counts as, however its session stopped), **queued**, **paused**, **idle** (the session stopped without asking anything, as a master's does while its sub-stories are built), and **not merged** (in Done with its branch unmerged). Each story is counted once. A count's tooltip lists its stories; click it to show only those tiles, and again to show them all.

**Model.** Sessions run on `opus`. Change the store's default with `"model"`, or one story's with the Model dropdown in its dialog, which writes a `Model: sonnet` line. Change it while the story is in flight and the board stops the session and resumes it on the new model.

**Base.** A story is cut from the trunk (`dev` when the app has a dev branch) and merged back into it. To work off another branch, type or pick it in the Base field of the tile's dialog, which writes a `Base: feature-x` line; the review steps (squash, merge, push) then go against that branch too. Clearing the field, or naming the trunk, takes the line off. A branch the repo hasn't got is ignored: the story is cut from the trunk and its session is told so. The branch a story was cut from stays its base, whatever the line says later.

**Looks.** The header's Theme menu picks the board's look (Clean and light, Classic, Aqua bubble, Sticker pop, Realm ledger, Soft and sunny), its swatches an accent colour, and the button beside them light or dark: ◐ System follows your computer, ☀ Light and ☾ Dark stay put. Every theme has a dark palette. All three are kept in this browser only.

**Settings.** The ⚙ button in the header opens the store's settings: pickup, agent, model, permission mode, stories at once, sessions, full suite on merge, trunk, tidy, push and commit delay. Each shows its default and, once `board.json` sets it, a mark and a Reset button; Save writes only what you changed to `board.json` (Reset removes the key) and pickup follows without a restart. The store's other facts (name, key, port, folder, repos) are listed read-only: set those in `board.json`.

**Repos.** Tick the repos in a tile's dialog (shown when the store has more than one) and it writes the `Repos:` line, in `board.json` order; ticking only the main repo removes the line, and a different first repo is a hand edit. Change a tile's `Repos:` line while the story is in flight and the board stops the session and resumes it with the new set: new repos get a worktree and are added to the session. Name a different repo first and a new session starts there, taking over from the tile and the branch. A repo you drop keeps its worktree, so tidy still clears it. A line that names an unknown repo is ignored, with a note on the board's console, while a session is at work or idle. A story that is queued, lost or failed is not started: its tile says "no repo named ...". Run `kandalf repo add` to add one; a running `kandalf serve` picks it up from `board.json` within seconds.

**Several at once.** One tile works at a time unless `"parallel"` in `board.json` says more. While that many sessions are at work, the next tile waits (**queued**) and the board asks whether to start it in parallel. Leave it queued and its tile keeps a **▶ Start now** button, to start it whenever you like. A master story's sub-stories each get their own session, two at work at once, the rest queued; the master itself gets none while any of them is in flight. Once the last has left In flight, the master is picked up like a tile that just landed, so its own sub-tasks are worked and it shows a status and buttons. A story's session runs only the tests for what it changes; the full suite runs on the trunk after a merge from the board when the store (`fullSuiteOnMerge` in `board.json`) or the tile (`Tests: full`) asks, for a master's sub-story only when it is the last of them, as a **Full suite** sub-task on the story's tile (its master's, for a sub-story) before anything is pushed. Pulling a master with several sub-stories into In flight asks how many may work at once; a number other than the store's is kept on the master as `Sessions: 3`.

**If it can't start**, the tile shows **not started** with the reason, and **Retry**.

**Archiving tidies.** When a story is archived (Clear, or dropped on Archive) the server stops and removes its session, removes its worktree and deletes the feature branch in every repo the story worked in (the ones its session line names). It does so only when nothing would be lost in any of them: the branch is already in that repo's trunk (merged, rebased or squashed), every worktree has nothing uncommitted, the session isn't at work, and every repo is still in `board.json`. Otherwise it keeps everything and says which repo holds it up in the banner and on the console. The console gives the deleted branch's last commit, so `git branch <name> <commit>` brings it back. `"tidy": false` keeps everything.

**Deleting throws away.** Deleting a tile that has a session (the card's Delete button) asks first, then stops and removes the session, removes its worktree with anything uncommitted in it, and deletes the branch unless it has commits of its own (then the branch is kept, so no commit is lost). `"tidy": false` doesn't apply: a delete is asked for.

## The board without an agent

Leave pickup off and it is a markdown kanban.

- **Columns and tiles.** `plans.md` is the board: one `## ` column per status, one `- ` tile per plan. Drag a tile to change its status, or edit it in place. A tile can have a priority, an estimate and tick-box sub-tasks. The server refuses to save over a file that changed on disk since your page loaded it, and reloads it instead.
- **Plan docs.** Longer plans live in `active/` (`YYYY-MM-DD-slug.md`), linked from the tile. A key written anywhere in a tile's text ("after PLN-41") jumps to that tile, and a tile's dialog lists what it is Related to.
- **Backlog and Promote.** `backlog.md` is a second board of unscheduled tiles, one column per kind of work. Drop a tile on Promote and it moves to the top of To do, keyed if it had no key. On the plans board, Back to backlog lists the backlog's columns.
- **Archive.** Drop a tile on Archive: its plan docs move from `active/` to `archive/`, links to them are rewritten, and the tile is filed at the top of `archive/archived.md`. **Clear** in Done's header archives every tile there at once (a Done tile whose branch isn't merged stays). **Undo** brings the last clear back; it is gone once the page reloads.
- **Keys and sort.** Every tile has a key (`PLN-07`). The Sort menu orders every column by priority, estimate or when the tile was first committed; it is display only, and the file keeps its order. Cmd/Ctrl+Enter saves a tile's dialog.
- **Any folder.** `kandalf serve --dir ~/notes/boards --port 7789` serves any folder of boards, with no `board.json`.

## Tile conventions

Write these in a tile and the board acts on them.

| You write | What it does |
|---|---|
| `PLN-07` at the start of the line | The tile's key, in the store's prefix. `kandalf key next` prints the next free one, and the board keys a tile you create on it. A tile needs a key to be picked up. |
| `P1` to `P4` after the key | Priority: Highest, High, Medium, Low. |
| `~4h`, `~1.5d`, `~12-18h`, `~1w` after the priority | Estimate, in wall-clock time. Used for sorting and the ETA. |
| `Moves with PLN-74` (or `PLN-74, PLN-75 and PLN-76`) | Linked tiles: move one to another column and the others follow. Works both ways and chains. Nothing follows a tile into Done, out of Done or to Archive. |
| `Part of PLN-70` | The tile is a sub-story of master PLN-70 and folds into it. A sub-story picked up on its own is given the master's tile to read, wherever the master sits. The master shows a bar and count of sub-stories done. Moving the master takes its sub-stories along; a sub-story moves alone. The first `Part of` counts. |
| `No pickup` | Never pick this tile up. Write it on a tile a session of yours is already working. |
| `Model: sonnet` (own line) | The model for this story's session: an alias or a full model id. |
| `Base: feature-x` (own line) | The branch this story is cut from and merged into, instead of the trunk, in each repo that has it. The dialog's Base field writes it. A sub-story takes its master's. |
| `Tests: full` (own line) | Merging this story from the board runs the full suite on the trunk afterwards; `Tests: affected` says not to, over the store's `fullSuiteOnMerge`. A sub-story takes its master's. The tile's dialog has a **Full suite on merge** box that writes and removes the line. |
| `Repos: api, web` (own line) | The repos this story works in. The session starts in the first one. No line: the main repo. The dialog's Repos boxes write it. A sub-story takes its master's. |
| `Sessions: 3` (own line, on a master) | How many sub-stories have a session at work at once, instead of the store's `"sessions"`. |
| ``Claude session `ab12cd34`: ...`` | Written by the board when a session starts, and shown as its own block. Leave it as it is. |
| `Handoff: ...` (own line) | The session's note on what is done, doing and next. Shown in the opened tile's status. |
| `**Status 2026-11-18 14:58:** ...` | Written by the Status button; each refresh replaces it. |
| `**Ready for review:** ...` (own line) | The story is finished and waits for you. The tile is outlined green. Anyone's line counts, not only a session's. |
| A branch in backticks on a Done tile, with a slash in its name (`` `feat/x` ``), or the branch on the session line | The tile is outlined red until that branch is merged into the `trunk` (or the story's `Base:` branch) of each repo the tile names (its session line's repos, else its `Repos:` line's, else the main repo). Only in a column named Done. |

Only the board applies links and sub-story moves: a tile moved by hand, in an editor or by an agent, moves alone. The filing rules are in the header of each store's `plans.md`.

## Settings

A store's `board.json` is optional; every key has a default.

| Key | Default | What it does |
|---|---|---|
| `name` | the folder's name | Shown in the page title. |
| `key` | `PLN` (`kandalf init` picks the name's first three letters) | Tile key prefix: `PLN-01`, `PLN-02`, ... Capital letters and digits. |
| `port` | `7788` | Where `kandalf serve` listens. |
| `link` | `plans` | The folder name app repos link the store in by (`docs/plans`). |
| `app` | none | The app repo, relative to the store. Needed for pickup and for the red outline on unmerged Done tiles. |
| `trunk` | `dev` when the app has a dev branch, else `main` (`kandalf init` picks `dev` when there is one, else the app's default branch) | The app branch feature branches are cut from and merged into. A tile's `Base:` line picks another for its story. |
| `repos` | none | Other repos stories can work in, by name: a path relative to the store, or `{"path", "trunk"}` (no trunk: the store's `trunk`, else `dev` when that repo has it, else `main`). A tile's `Repos:` line picks them. |
| `agent` | `claude` | The agent pickup hands stories to: `claude` (Claude Code) or `copilot` (GitHub Copilot CLI). |
| `pickup` | `false` | Start an agent session (`agent`) for a tile that lands in In flight. Needs `app`. |
| `pickupColumn` | any column named like In flight: in-flight, in progress (typos too), doing, WIP, working, underway, ongoing, active; not one that says "not" | The one column, by name (any case), whose tiles are picked up. |
| `permission` | `auto` | Permission mode sessions start in: `auto`, `acceptEdits`, `manual`, `dontAsk`, `plan` or `bypassPermissions` (`claude --help` lists them). |
| `model` | `opus` (`auto` for copilot) | Model sessions run on: an alias or a full id. A tile's `Model:` line overrides it. |
| `parallel` | `1` | How many tiles on their own have a session at work at once. The rest are queued, each with **Start now**. |
| `sessions` | `2` | How many of a master's sub-stories have a session at work at once. A master's `Sessions:` line overrides it. |
| `fullSuiteOnMerge` | `false` | `true`: a merge from the board runs the full suite on the trunk before the push (a sub-story's only when it is the last of its master's), unless the tile has a `Tests: affected` line. `false`: only when the tile has a `Tests: full` line. Every session runs only the tests for what it changes either way. |
| `branch` | `{key}-{slug}` | A session's feature branch. `{key}` is the tile's key in lower case, `{KEY}` as written, `{slug}` its title, `{app}` the app folder's name. Must contain `{key}` or `{KEY}`. |
| `worktree` | `{app}-{key}` | Its worktree, relative to the folder the app is in, same placeholders. Must contain `{key}` or `{KEY}` and climbs at most one folder (`../`). `kandalf init` sets `{app}/.worktrees/{key}`, inside the app. |
| `hooks` | `true` | The board's hooks (see Hooks). `false`, set by `kandalf init --no-hooks`, keeps `kandalf serve` from adding them. |
| `tidy` | `true` | Archiving a story removes its worktree, branch and session, when nothing would be lost. |
| `push` | `true` | The commit queue pushes each commit to the store's upstream, when it has one. |
| `commitDelay` | `300` | Seconds of quiet before the queue commits. |

Environment variables:

| Variable | What it does |
|---|---|
| `BOARD_COMMIT_DELAY` | Seconds of quiet before commit; overrides `commitDelay`. |
| `BOARD_NO_OPEN` | When set, `kandalf serve` doesn't open the browser (as `--no-open`). Nor does it when its output isn't a terminal. |
| `BOARD_PICKUP_SETTLE` | Seconds a just-started session counts as at work before it shows itself busy (default `90`). |
| `PLANS_APP_REPO`, `PLANS_APP_TRUNK` | Override `app` and `trunk`. |

**`pickup.md`**, at the top of the store, replaces the default working rules every session is given. The default (`kandalf/templates/pickup.md`) says commit subjects carry the key, and adds a last Gates sub-task, which is unticked again (or joined by a new one) for any later run of tests. `{key}` in it stands for the tile's key. The rules follow "Tick each sub-task with its commit as it lands." in the session's list, so a first line that is a sentence carries on that rule and each `- ` line is a rule of its own. An empty file gives none. What the board reads back (the session line, ticked sub-tasks, `Handoff:`, `Ready for review`) is always asked for.

## Commands

With no `--dir`, `kandalf` uses the store you're in, or the one the current git repo links in under `docs/` (so from anywhere in an app checkout or worktree).

```
kandalf init [DIR]          a new store beside this git repo, linked in at docs/plans
                            (--name, --key, --port, --app, --no-app, --pickup, --no-pickup,
                             --hooks, --no-hooks, --example, --yes)
kandalf serve               serve the store's boards (--dir STORE, --port N, --no-open)
kandalf key next            the next free tile key
kandalf key assign          key every tile that has none, oldest first
kandalf commit status       what's queued, and when it will commit
kandalf commit now          commit and push the queue right away
kandalf check FILE...       would the board open this file view-only? names the first bad line, and a second In flight or Done column
kandalf where               which store, its key, port, app and repo
kandalf attach ID|KEY       open a story's session (for Claude, `claude attach`; for Copilot, follow it, then `copilot --resume`)
kandalf status KEY          the Status line for a story's session, as the tile's Status button writes it
kandalf agent [NAME]        the agent pickup hands stories to; or switch to claude or copilot (--force)
kandalf repo add PATH       another repo stories can work in (--name NAME, --trunk BRANCH)
kandalf repo list           the store's repos, the main one first
kandalf repo rm NAME        take a repo out of board.json (its docs/plans link stays)
kandalf uninstall           remove the docs/plans link from every repo of the store (the store stays)
kandalf doctor              what works and what doesn't, one line per check
kandalf hooks               print the hooks block (--add: put it in the store and link it into the app)
kandalf hook autocommit     the Claude Code hook that queues an edit (reads the call on stdin)
kandalf hook check          the Claude Code hook that flags a file the board would open view-only
```

`kandalf init` asks for what you don't pass when run in a terminal; `--yes` takes the defaults (pickup off, hooks on; with `--pickup`, the agent is `claude` when it is on PATH, else `copilot` when that is).

## Commits and pushes

You never commit board changes by hand. A change is saved to disk at once and committed later, in the store's own repo.

1. **Every change is queued:** each save, archive and promote the server makes, and, through the hooks below, each Edit/Write of a `.md` file inside a store in Claude Code. The queue is the store's `.git/board-pending.json`.
2. **The commit waits for quiet.** After 5 minutes with no new change (`commitDelay`), everything queued goes in as one commit. Every change restarts the wait.
3. **The subject says what changed overall**, e.g. `board: PLN-26 backlog.md → To do, PLN-33 archived`. The body lists each step.
4. **Each commit is pushed** in the background. A failed push is logged in `.git/board-push.log` and the next push sends everything waiting. A store with no upstream isn't pushed. `"push": false` commits without pushing.

On start the server also picks up changes left over from before. A commit takes only the queued paths: anything changed another way (a shell `mv` or `sed`) isn't queued, so commit it yourself or restart the server. A running server keeps the code it started with, so restart it after updating kandalf.

## Hooks

The board's hooks are Claude Code hooks, which the Copilot CLI reads too. They live in the store, in `.claude/settings.json`, committed with the rest of it. They are part of the board, not an option: `kandalf init` puts them there, and `kandalf serve` adds them to a store that lacks them when it starts (as `kandalf hooks --add` does), unless `kandalf init --no-hooks` set `"hooks": false` in its `board.json`. The app gets nothing committed:

- **Pickup sessions** start with `claude --settings <store>/.claude/settings.json`, so every worktree gets the hooks without a file of its own.
- **Your own sessions in the app** get them through `.claude/settings.local.json`, a gitignored symlink to the store's file. If the app already has a `settings.local.json` of its own, the hooks are merged into it and everything else in it is kept.
- **Old hooks** that run `board`, the command's name before it was `kandalf`, do nothing now. `kandalf hooks --add` replaces them in the store and in the app's `settings.local.json`; `kandalf doctor` points out any left in the app's committed `.claude/settings.json`, to take out by hand.

`kandalf doctor` says where the hooks are. `kandalf hooks` prints the block:

```json
"PostToolUse": [{ "matcher": "Edit|Write", "hooks": [
  { "type": "command", "command": "command -v kandalf >/dev/null && kandalf hook autocommit 2>/dev/null || true" },
  { "type": "command", "command": "command -v kandalf >/dev/null || exit 0; kandalf hook check" }
]}]
```

- **`kandalf hook autocommit`** queues an Edit/Write of a `.md` file inside a store for the next batched commit. It ignores everything else and never fails the tool call.
- **`kandalf hook check`** runs after an edit to `plans.md`, `backlog.md` or `archive/archived.md`. If the board would open the file view-only, or the file has two In flight columns or two Done columns, it hands the error back to the session that made the edit, so it fixes the file at once. It needs node.

## Safety

`kandalf serve` listens on 127.0.0.1 only. It answers only requests addressed to `127.0.0.1:<port>` or `localhost:<port>`, and takes changes only from its own page, so another site open in your browser can't use it. Requests with no `Origin` (curl, scripts) are answered.

What it can do on your machine, and the setting for each:

- **Start Claude Code sessions** in your repos, one per tile that lands in In flight. Only with `"pickup": true`; it is off by default. They run in the mode `"permission"` names: `auto` by default, where a session edits files and runs commands without asking and Claude Code's auto-mode checks decide what is allowed. Pick another mode for more stops. A session may change every repo its tile names, but only in worktrees, never in your checkouts. It is told not to merge or push in any of them.
- **Delete branches, worktrees and sessions** when a story is archived, but only ones already in the trunk with nothing uncommitted, and never a session still at work. `"tidy": false` keeps them all.
- **Commit and push the store.** Every board change is committed in the store's own repo and pushed to its upstream if it has one. `"push": false` commits without pushing.

When it starts, the server says whether pickup is on, and if not, why and how to fix it.

## Platforms and requirements

- Python 3.10 or later, standard library only.
- git.
- node, only for `kandalf check` and the check hook.
- For pickup only, one agent CLI on your PATH:
  - [Claude Code](https://claude.com/claude-code) (`claude`), with `--bg`, `agents --json`, `logs`, `stop` and `rm` (`claude update` brings them), or
  - the [GitHub Copilot CLI](https://github.com/features/copilot/cli) (`copilot`), signed in (`copilot login`), with `-p`, `--session-id`, `--resume`, `--add-dir`, `--name`, `--allow-all-tools` and `--allow-all-paths`.
- macOS and Linux. Windows is untested.

## Development

```
bin/kandalf               the command (a script; symlink it onto your PATH)
kandalf/
  cli.py                  the commands
  serve.py, board.html    the server and its page
  pickup.py               hands a tile in In flight to an agent
  agent.py                the agent in use (board.json "agent")
  settings.py             the settings panel's editable board.json keys, checked and written
  claude_code.py          every call to the claude CLI
  copilot_cli.py          every call to the copilot CLI
  init.py                 kandalf init, kandalf doctor, kandalf agent, kandalf hooks (and --add)
  gitlog.py               the batched commit queue
  store.py                what a store is, and its board.json
  pln.py                  tile keys
  stamp.py                where a build came from, for the page's corner (setup.py writes it at build time)
  autocommit.py, checkhook.py   the Claude Code hooks
  check.js                does a board file round-trip through the page's parser?
  templates/              starting files for a new store, and the default pickup.md
test_*.py, test_page.js   the tests
```

Run the tests:

```bash
python3 -m unittest discover -p "test_*.py"
node --test test_page.js
```

The page's bottom right corner says which kandalf is serving it: `kandalf 1.1.10` for a build of a release's tag with every file committed, else `kandalf 1.1.10+dev` and when it was built, with the commit in its tooltip. `setup.py` stamps an installed copy when it is built; one run from a checkout asks git when the server starts.

To support another agent, `kandalf/claude_code.py` and `kandalf/copilot_cli.py` are the seams: nothing else runs `claude` or `copilot`, and its docstring lists the functions another agent's module would provide.

## Licence

MIT, see `LICENSE`. The bundled marked is under its own licence: see `THIRD_PARTY`.
