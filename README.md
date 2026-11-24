# kandalf

A wizard for your backlog, straight outta middle management.

What do you get when you cross kanban with wizardry (then add "alf", for reasons)? kandalf. A story is never late, nor is it early. It arrives precisely when its agent finishes.

It's a local kanban board for planning work for AI agents, served on your machine.

**Why it exists.** kandalf is for organising planning sessions: the plans you write with an agent, the stories they break into, and what becomes of each. Plans already live as markdown files, so that is all kandalf keeps. The board is one more markdown file, readable and editable by hand, and each plan doc sits beside it as it was written. No database, nothing to import or sync, and nothing to install beyond Python 3.10+ and git.

Plans pass both ways between kandalf and your agent. Plan in a Claude Code, GitHub Copilot CLI or Antigravity CLI session in your repo, then say "add a story to kandalf": `kandalf init` taught the repo's agents how, with a section in its `AGENTS.md` and `CLAUDE.md`, so the session writes the plan doc into the store and files its tile on the backlog. The other way, plan on the board and hand it to an agent.

On top of the files sits just enough process to help things along. Drag a story to In flight and a background Claude Code, Copilot or Antigravity session starts on it, in its own git worktree, given the tile and the plan doc it links. Its progress shows on the tile, `kandalf attach` opens the session to carry on by hand, and when it's ready for review you squash, merge and close it from the board.

Contributions are welcome if you have a burning interest, or an interest in burning tokens.

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

Pickup is off until you turn it on. Once it's on, each story that lands in In flight gets a background agent session in your repo, working in its own worktree and branch. By default the session edits files and runs commands without asking (Claude Code's auto mode decides what's allowed), and it's told to stop before merging or pushing.

1. **Turn it on.** Answer yes when `kandalf init` asks, or set `"pickup": true` in the store's `board.json`. A running `kandalf serve` notices within a few seconds, with no restart: turned on, it starts on the tiles already in flight; turned off, it leaves running sessions alone.
2. **Trust the repo.** Run the agent (`claude`, `copilot` for a Copilot store, or `agy` for an Antigravity store, signed in) once in your repo and accept its trust prompt. Claude Code won't start a background session in a repo it doesn't trust, and the tile will say so. Trusting the repo covers its worktrees, which pickup keeps under `.worktrees/` inside it (kept out of git: `kandalf init` adds it to `.gitignore`, and otherwise pickup adds it to the repo's `info/exclude`). A story that works in several repos (`Repos:` on its tile) needs each of them trusted.
3. **Check.** `kandalf doctor` lists what works and what's missing.

Pickup needs [Claude Code](https://claude.com/claude-code) (the default), the [GitHub Copilot CLI](https://github.com/features/copilot/cli), or the [Antigravity CLI](https://antigravity.google/docs) (`agy`). See [Platforms and requirements](#platforms-and-requirements).

`kandalf agent claude` (or `copilot`, or `agy`) switches an existing store. It sets `agent`, drops `model` (that was the old agent's), and marks each story the old agent picked up as gone, so **Continue** on its tile starts the new agent in the same worktree. It refuses while an old session is working or idle; `--force` stops those first.

### Using Claude Code

Claude Code is the default: `kandalf init` picks it when `claude` is on your PATH, and `"agent": "claude"` in `board.json` (or no `agent` at all) keeps it.

- **It runs as a Claude Code background session**, `claude --bg`, so Claude Code keeps it and lists it in `claude agents`.
- **Attaching opens it.** `kandalf attach ab12cd34` runs `claude attach ab12cd34`: the session's own screen, to watch it or carry on. A key finds a draft's session too.
- **It can stop to ask.** When it asks a question or is interrupted, status and the tile say it's waiting for your input.
- **It gets the hooks with `--settings`**, pointing at the store's `.claude/settings.json`, so the worktree needs no file of its own.
- **It reaches the store through `docs/plans`**, the link in its worktree. The other repos a `Repos:` line names come with `--add-dir`.
- **Models** are `opus` (the default), `sonnet`, `haiku` and `fable`, or a full model id.

The `permission` setting goes to Claude Code as it is, as `--permission-mode`: `auto`, `acceptEdits`, `manual`, `dontAsk`, `plan` or `bypassPermissions`. A store set to `plan` cannot draft options, and in `manual` a draft stops at its first write prompt and shows as needing you.

### Using the Copilot CLI

Pick Copilot when `kandalf init` asks (or pass `--agent copilot`), or set `"agent": "copilot"` in `board.json`.

A Copilot session works a little differently:

- **It runs detached**, as `copilot -p --output-format json`, tracked under `~/.kandalf/copilot/` (`KANDALF_COPILOT_DIR` overrides that).
- **Attaching follows it.** `kandalf attach ab12cd34` prints what the session has said and the tools it ran, then follows it while it works (Ctrl-C leaves it working). Once it has finished, it opens the conversation with `copilot --resume` in the worktree, with the folders and permissions it ran with, so you can read it and carry on. Quit that (`/exit`) before you press Continue on the tile: while it's open, the board can't resume the session.
- **It can't stop to ask.** Status and the tile call it waiting for your input when it ended on an error or with a question.
- **It gets the hooks through a link.** Copilot reads Claude Code hooks from a repo's `.claude/settings.local.json`, so the worktree gets a link there to the store's hooks (kept out of git by the repo's `info/exclude`).
- **It's given the store.** A Copilot session reaches only its worktree and the folders it's given, so it gets the store with `--add-dir`, for the board and plan docs behind `docs/plans`.

The `permission` setting maps onto Copilot's flags:

| `permission` | Copilot runs with |
|---|---|
| `auto` | `--allow-all-tools --allow-all-paths`: any tool, any file path (URLs are still checked) |
| `acceptEdits` | `--allow-all-tools` |
| `bypassPermissions` | `--allow-all` |
| `plan` | `--mode plan` |
| anything else | nothing, so it denies whatever isn't pre-approved |

A store set to `plan` cannot draft options.

### Using the Antigravity CLI

Pick Antigravity when `kandalf init` asks (or pass `--agent agy`), set `"agent": "agy"` in `board.json`, or switch an existing store with `kandalf agent agy`.

An Antigravity session works much like a Copilot session:

- **It runs detached**, as `agy --output-format stream-json --print`, tracked under `~/.kandalf/agy/` (`KANDALF_AGY_DIR` overrides that).
- **Attaching follows it.** `kandalf attach ab12cd34` prints what the session has said and the tools it ran, then follows it while it works (Ctrl-C leaves it working). Once it has finished, it opens the conversation with `agy --conversation` in the worktree, with the folders and permissions it ran with.
- **It gets the hooks in `.agents/hooks.json`.** Pickup writes the board's `PostToolUse` hooks there in the worktree (kept out of git by the repo's `info/exclude`).
- **It's given the store.** The session gets the store with `--add-dir`, for the board and plan docs behind `docs/plans`.
- **Models have short names.** `model` (or a tile's `Model:` line) can be `auto` (Antigravity picks), a full id such as `gemini-4-neon-high`, or an alias: `neon`, `argon`, `hydrogen`, `flash`, `opus` and `sonnet` each run that model's `-medium` variant.

The `permission` setting maps onto Antigravity's flags:

| `permission` | Antigravity runs with |
|---|---|
| `auto` | `--dangerously-skip-permissions` |
| `acceptEdits` | `--mode accept-edits` |
| `bypassPermissions` | `--dangerously-skip-permissions` |
| `plan` | `--mode plan` |
| anything else | nothing (default prompts) |

A store set to `plan` cannot draft options.

## What a session does with a story

### Pickup

While `kandalf serve` runs for a store with an `app` and `"pickup": true`, a keyed tile that lands in the In flight column of `plans.md` is picked up, however it got there: dragged on the board, edited in by hand, or moved by an agent. The server:

1. **Cuts a worktree** on a new branch from the trunk, or from the branch the tile's `Base:` line names. For PLN-74, that's worktree `myapp/.worktrees/pln-74` on branch `pln-74-colour-tokens`. `"branch"` and `"worktree"` in `board.json` change the names; `"worktree": "{app}-{key}"` puts it beside the app (`myapp-pln-74`), the default before. The store is linked in at `docs/plans` there too.
2. **Starts a background session** in it, given the tile and told to follow the repo's rules, tick sub-tasks with their commits as they land, keep a `Handoff:` line on the tile, and stop before merging or pushing.
3. **Writes the session, branch and worktree onto the tile.**

A tile with a `Repos: api, web` line gets a worktree in each repo, all on the same branch, each placed by the `worktree` pattern for its own repo (`api/.worktrees/pln-74` and `web/.worktrees/pln-74` in a store made by `kandalf init`). The session starts in the first and gets the others with `--add-dir`; its prompt lists them. The store is linked in only where that repo's checkout links it.

When the server starts, it also picks up any tile already in flight that never had a session (no session line and no sub-stories). Unkeyed tiles, and tiles that say `No pickup`, are never picked up; take the words out and the tile is picked up on the next look. The server's startup list says when `No pickup` is why a tile has no session. A tile that says `Blocked by PLN-30` waits the same way: while a blocker isn't done it stays in In flight without a session, the console says so once, the startup list and the tile (`waits for PLN-30`) say what for, and it is picked up on the first look after its last blocker is done (a change to `backlog.md` or the archive log brings a look, as one to `plans.md` does, so deciding a backlog tile starts what it blocked). A story that already has a session carries on if a `Blocked by` line is added, and one paused in place stays paused. A story still queued gives up its place when a `Blocked by` line is added, and is queued again at the back once its blockers are done; one waiting to be taken over from its handoff is paused again, and taken over in turn once unblocked. When a story is picked up, its session is given the tiles of its decided blockers (the ones with a `**Decision …**` line) to follow.

If a session can't be started, the tile shows **not started** with the reason, and a **Retry** button.

### The session block

The board writes the session onto the tile as one line:

``Claude session `ab12cd34`: `kandalf attach ab12cd34`. Branch `pln-74-colour-tokens`, worktree `myapp/.worktrees/pln-74`. Model `opus`. Started 2026-11-18.``

A Copilot store's line starts ``Copilot session``, an Antigravity store's ``Antigravity session``. A story in several repos names each worktree: ``Branch `pln-74-colour-tokens`, worktrees api `api/.worktrees/pln-74`, web `web/.worktrees/pln-74`.``

The page shows that line as a block with the session's state, a button that copies the attach command, and a button for what you can do next:

| State | What it means | Button |
|---|---|---|
| **working** | The session is at work. | **Pause** |
| **pausing** | It was paused and is writing its handoff before it stops. | **Pause now** stops it there if it hangs; the tile keeps the Handoff line it has. |
| **paused** | It wrote its handoff and stopped. | **Continue** |
| **idle** | It finished its turn. | **Continue** |
| **waiting for you** | It's idle because it needs input: it was interrupted, or it asked a question. The tile gets an amber outline. This is a best guess from `claude logs`. | **Continue**, or answer in the message box |
| **gone** | It was stopped, closed or removed. The tile flashes red until you continue it or move it out of In flight. | **Continue** |

### Pause, Continue and the handoff

Each session keeps an indented `Handoff:` line on its tile: what's done, what it's doing and what comes next. The tile and the branch are all a new session needs to carry on.

- **Pause** puts a story aside, whichever agent runs the session. The board stops the session and resumes it once, telling it to finish or undo the step it's on, commit what's worth keeping, rewrite the `Handoff:` line in full (done, uncommitted, next, undecided) and stop. A paused story gives up its place, so the next queued tile starts. Moving a working tile out of In flight, to any column, pauses it the same way.
- **Continue** gets a stopped session going again:
  - On a **paused** story, a new session takes over from the `Handoff:` line and the branch, in the same worktree. It doesn't need the old conversation, so this works days later, after a server restart, or after switching agents.
  - On an **idle** or **gone** session, it resumes the same conversation, telling the session to carry on from where it was, check `git status` and the tile, and take another way rather than wait again. If the conversation can't be resumed, a new session takes over in the same worktree.
- **Queue**, beside Continue on a paused story, puts it at the back of the queue, to be taken over in its turn instead of now.
- A paused tile **out of In flight** has no buttons. Move it back to In flight and it queues at the back (**queued**, with **Start now** to start it beside the session at work).

A take-over, whether from Continue or the queue, runs on the model and repos the tile names when it starts.

### Status

**Status**, in the session block, checks the story right now and writes the result under the session line: the session's state, commits ahead of the trunk, the last commit, uncommitted files, sub-tasks ticked with an ETA, and the last words on the session's screen. The board works this out from the agent CLI's session list, git in the worktree and the tile; it doesn't ask the session anything. The ETA goes by the pace of ticked sub-tasks or, with none ticked, by what's left of the tile's estimate. `kandalf status KEY` gives the same line on the command line.

### Messages and the log

Every session the board can reach, whichever the agent, has a message box in its block. **Send** (or ⌘↵ / Ctrl+↵) puts what you type into the same conversation.

- The box is **open** when the session waits for you: it asked something, ended on an error, or its story is ready for review and it stopped.
- Otherwise it's **folded** behind a chevron. Unfold it to **Interrupt with a message** while the session works (it stops mid-step, reads your message and carries on from where it was), or to message a session that's idle or gone.
- A paused story has no message box; it has **Continue** instead.

Above the message field, the box shows the session's **log**: what it was told (`❯`), what it said and each tool it ran (`⏺`), and the start of each result (`⎿`), laid out like the session's own screen, newest last. It refreshes every few seconds while the box is open and stays at the newest line unless you scroll up. It's taller in an opened tile, and **↗ window** opens it full-size in its own tab (`/log?key=PLN-01`). A Claude session's log comes from Claude Code's transcript under `~/.claude/projects`, a Copilot or Antigravity session's from the output the board keeps.

### Review and merge

When every sub-task is ticked and every gate has passed, the session adds a `**Ready for review:**` line (gate results, commit count, what to look at first) and stops. The board outlines the tile in green until it's in Done or the line is removed.

You can review, squash and merge yourself, or hand those steps to the session. On a story that's ready for review, the message box has three checkboxes: **Squash**, **Merge into** the trunk, and **Push**. Tick any of them, send, and the session does them in that order, though its rules otherwise leave them to you:

- **Squash** rebases onto the trunk and squashes only the story's own commits.
- **Merge** rebases onto the trunk and fast-forwards it, so there's no merge commit.
- **Push** after Merge pushes the trunk. Push on its own pushes the story's branch.

Each step runs in every repo the story works in. The session stops at the first step that can't be done cleanly and says why on the tile. The go-ahead covers that one reply only: any later reply tells the session that squashing, merging and pushing are yours again.

After a Merge, the session takes the `Ready for review` line off. (Without a Merge the line stays, since the story is still waiting to be merged.) If nothing is outstanding (every sub-task ticked, every step done), it closes the story by moving its tile to the top of Done, as the board's header says. Otherwise the tile stays in In flight and its `Handoff:` line says what's left.

A story with nothing to merge gets a **✓ Close** button instead of the checkboxes. That means its branch has no commits beyond the trunk and nothing uncommitted, in every repo, as with a master whose sub-stories did the work. The session takes the `Ready for review` line off and closes the story the same way, without squashing, merging or pushing.

### Several at once

One tile works at a time unless `"parallel"` in `board.json` allows more. When that many sessions are at work, the next tile is **queued** and the board asks whether to start it in parallel anyway. A queued tile keeps a **▶ Start now** button, so you can start it whenever you like.

**Master stories.** Each sub-story of a master gets its own session, two at a time (the store's `"sessions"`), with the rest queued. The master itself gets no session while any of its sub-stories is in flight, however they got there, and that holds when `No pickup` is taken off such a master. Once the last one leaves In flight, the master is picked up like a tile that just landed, so its own sub-tasks get worked and it shows a status and buttons. Pulling a master with several sub-stories into In flight asks how many may work at once; a number other than the store's is kept on the master as `Sessions: 3`.

**Tests.** A session runs only the tests for what it changes. The full suite runs on the trunk after a merge from the board, before anything is pushed, when the store (`fullSuiteOnMerge`) or the tile (`Tests: full`) asks for it. It shows as a **Full suite** sub-task on the story's tile. For a sub-story it runs only when it's the last of its master's, and goes on the master's tile.

### Stats

While pickup is on, a strip under the plans board's header counts the stories:

- **at work**
- **need you**: the session asked something, ended on an error, is gone, or couldn't be started
- **ready for review**: this includes any finished story, however its session stopped
- **queued**
- **paused**
- **idle**: the session stopped without asking anything, as a master's does while its sub-stories are built
- **not merged**: in Done with its branch unmerged

Each story is counted once. A count's tooltip lists its stories. Click a count to show only those tiles, and click again to show them all.

### Per-story choices

Each of these has a field in the tile's dialog that writes a line on the tile (see [Tile conventions](#tile-conventions)).

- **Model.** Sessions run on `opus` unless the store's `"model"` says otherwise. The Model dropdown writes a `Model: sonnet` line. Change it while the story is in flight and the board stops the session and resumes it on the new model.
- **Base.** A story is cut from the trunk (`dev` when the app has a dev branch) and merged back into it. Type or pick another branch in the Base field to write a `Base: feature-x` line; the review steps then go against that branch too. Clearing the field, or naming the trunk, removes the line. A branch the repo doesn't have is ignored: the story is cut from the trunk and its session is told so. A story keeps the base it was cut from, whatever the line says later.
- **Repos.** When the store has more than one repo, tick the ones the story works in to write a `Repos:` line, in `board.json` order. Ticking only the main repo removes the line; putting a different repo first is a hand edit. Change the line while the story is in flight and the board stops the session and resumes it with the new set: new repos get a worktree and are added to the session. If the first repo changes, a new session starts there, taking over from the tile and the branch. A repo you drop keeps its worktree, so archiving still tidies it. A line that names an unknown repo is ignored, with a note on the board's console, while a session is working or idle. A story that's queued, gone or not started is held back instead, and its tile says "no repo named ...". `kandalf repo add` adds a repo, and a running `kandalf serve` picks it up from `board.json` within seconds.

### Cleaning up

- **Archiving tidies.** When a story is archived (Clear, or dropped on Archive), the server stops and removes its session, removes its worktree and deletes its branch, in every repo its session line names. It does this only when nothing would be lost: the branch is already in that repo's trunk (merged, rebased or squashed), no worktree has uncommitted changes, the session isn't at work, and every repo is still in `board.json`. Otherwise it keeps everything and says which repo held it up, in the banner and on the console. The console logs each deleted branch's last commit, so `git branch <name> <commit>` brings it back. `"tidy": false` keeps everything.
- **Deleting throws away.** Deleting a tile that has a session (the card's Delete button) asks first. Then it stops and removes the session, removes its worktree along with anything uncommitted in it, and deletes the branch, unless the branch has commits of its own (then it's kept, so no commit is lost). `"tidy": false` doesn't apply here, since you asked for the delete.

## The board without an agent

Leave pickup off and it's a markdown kanban.

- **Columns and tiles.** `plans.md` is the board: one `## ` column per status, one `- ` tile per plan. Drag a tile to change its status, or edit it in place. A tile can have a priority, an estimate and tick-box sub-tasks. If the file changed on disk since your page loaded it, the server refuses to save over it and reloads it instead.
- **Plan docs.** Longer plans live in `active/` (`YYYY-MM-DD-slug.md`), linked from the tile. A key written anywhere in a tile's text ("after PLN-41") jumps to that tile, and a tile's dialog lists what it's Related to.
- **Backlog and Promote.** `backlog.md` is a second board of unscheduled tiles, one column per kind of work. Drop a tile on Promote and it moves to the top of To do, with a key if it had none. On the plans board, Back to backlog lists the backlog's columns. A tile in a column whose name says decision (Design decision, Decisions, Decide) is answered, not promoted: its card has an Answer box, and **Decide** writes the answer on the tile as a `**Decision <date>:**` line (folded to one line), ticks the tile and archives it, then names the stories it unblocked. A story that waits on a decision says `Blocked by` and its key.
- **Draft options.** A tile in a decision column, on plans or backlog, has a **Draft options** button on its card, with an optional one-line note saying what to look at. It cuts a throwaway detached worktree (no branch) of each repo the tile's `Repos:` line names, else of the main repo, and starts one background session of the store's agent in the first, which researches the question and writes one research doc, `active/<date>-<key>-options.md`, in the store. When the session finishes, the board reads the doc and writes an options block onto the tile: an `**Options <date>:**` line (which option is recommended, a link to the research doc, and `Asked: <note>` when a note was given) and up to four `- **A. Title.** summary` lines. The card lists them above the Answer box, each with **Use**, which copies the option into the Answer box to edit (it asks first if you typed something else). Nothing is decided until **Decide**. A hand-written options block in the same shape is listed the same way, with no draft.
  - **Draft again** takes a note and replaces the draft: the new session is told the note and to revise the last draft. **Stop** ends a draft. `kandalf attach KEY` opens a draft's session in a terminal (a story's session wins when the key has both). When the research doc changes later, because an attached session revises it or you edit it by hand, the tile's options follow.
  - The tile's tag says where a draft stands: `queued to draft`, `drafting options · 12m`, `options ready`, `draft needs you`, `draft gave no options` or `draft lost`. A draft needs you when its session stopped on a question, a permission prompt or an error, or when a redraft finished without changing the research doc.
  - One draft works at a time and the others queue. Drafts are apart from the story queue: they don't count against `sessions` or `parallel`, and stories don't wait for them.
  - It works with `"pickup": false`, since it is an explicit press, but needs what pickup needs: an app repo that is there and the agent's CLI installed. A store whose `permission` is `plan` cannot draft.
  - The session's code changes are discarded, not prevented. Its worktrees are removed when the draft ends: when the tile is decided, archived, deleted or leaves its decision column, when you press Stop, or when Draft again replaces it. Only the draft's own worktrees are removed: the one at its `draft-<key>` path (the store's `worktree` pattern, with `draft-<key>` for the key), or one git lists as detached; any other worktree on a branch is left alone. Deciding or archiving the tile moves the research doc to `archive/` with it.
  - The research doc is read as follows: an optional `Recommends: B` line before the first option; each option a heading `## A. Title` (`## Option A: Title` and similar are accepted) followed by a first paragraph that becomes the summary. Headings inside code blocks are ignored.
- **Archive.** Drop a tile on Archive: its plan docs move from `active/` to `archive/`, links to them are rewritten, and the tile is filed at the top of `archive/archived.md`. **Clear** in Done's header archives every tile there at once, except a tile whose branch isn't merged. **Undo** brings the last clear back, until the page reloads.
- **Keys and sort.** Every tile has a key (`PLN-07`). The Sort menu orders every column by priority, estimate, or when the tile was first committed. It only changes the display; the file keeps its order. Cmd/Ctrl+Enter saves a tile's dialog.
- **Looks.** The header's Theme menu picks the board's look (Clean and light, Classic, Aqua bubble, Sticker pop, Realm ledger, Soft and sunny), the swatches pick an accent colour, and the button beside them picks light or dark: ◐ System follows your computer, ☀ Light and ☾ Dark stay put. Every theme has a dark palette. All three choices are kept in this browser only.
- **Any folder.** `kandalf serve --dir ~/notes/boards --port 7789` serves any folder of boards, with no `board.json`.

## The map

**Map**, beside **Board** in the header, shows the same stories as a map. Every keyed story on plans, backlog and the archive is on it, whichever board is open.

- **Masters and sub-stories.** A master is a hub with its sub-stories round it. The ring on the hub fills as they are done, and the list on the left sorts masters into moving, not started and finished.
- **Lines.** A dashed line joins two stories when one mentions the other's key; a solid one joins linked tiles, which move together. A blocker and the story it blocks are joined too, and the panel lists what blocks a story and what it blocks.
- **Colour** says where a story is: backlog, planned (on the plans board, not yet in flight), in flight, or done. An archived story is done. With pickup on, a story at work has a turning ring, and one that needs you is red.
- **Next up.** Stories that pulse are the ones to look at: any that needs you or is ready for review, the sub-stories not yet started of a master that is already moving, and a planned story that stands alone. A story that is blocked doesn't pulse.
- **Domains.** Stories are grouped into a region per domain, and the chips at the top show one domain only. A story's domain is its own `Domain:` line, else its master's, else the first word before the colon in its title (`Admin: analytics`) when two masters or three stories start with that word.
- **Getting around.** Click a story to bring it to the middle with everything it is joined to; the panel lists those, each a click to hop to, and **Open tile** opens its card on its board. Esc steps back the way you came. Drag to pan, scroll to zoom, and type in the filter box to fade everything that doesn't match. With pickup on, the strip above the map counts the stories in each state, and pressing one fades the rest.
- **Arranging.** Hold Ctrl (or ⌘) and drag a master's circle to move its cluster, or a region to move a whole domain; with Alt as well, one sub-story. The arrangement is kept in your browser, per store, and **Reset layout** forgets it. Arranging changes no file.
- **Selecting.** Shift-click a story to select it or take it out of the selection (it doesn't focus the story, and a story the filter, the strip or the domain scope has faded can't be selected, and one faded after it was selected leaves the selection), or Shift-drag a box from empty space to add the shown stories inside it. Esc, a click on empty space, or changing the filter or the domain scope clears the selection. Drag a selected story (no Ctrl or ⌘) and the whole selection goes with it, a selected master's sub-stories moving with it on screen. The region under the pointer lights up; let go over it and the selection is filed in that domain. Let go anywhere else, or press Esc, and nothing is written and the selection stays.
- **Filing.** A drop writes one `Domain:` line per family: a story's own, or for a sub-story its top master's (adding it, replacing it, or taking it off when you drop on **No domain**), and nothing else, on plans, backlog or the archive, whichever board is open, as one commit (`board: 6 stories → Billing`). Families stay whole: dropping any story of a family files its top master, and its sub-stories follow, except one with a `Domain:` line of its own. `Part of` is never changed, and the map can't change or add a story's parent. A story that is in a domain only by its title (`Admin: analytics`) can be filed elsewhere, but dropping it on **No domain** changes nothing, and the page says so. The page says what it did (`PLN-70 and its 4 sub-stories → Billing`), and **Undo** appears in the map's bar until the next drop that files something or a reload. Undo is refused when a filed story's domain has been changed since (on its card, by an agent or in an editor), whichever board it is on: the server checks it against the files at the moment you press Undo, so that edit isn't lost, and the map then shows what is on disk. A drop is refused with "Save the board first." while the open board has unsaved edits.
- **New domain.** **+ New domain** in the map's bar asks for a name and adds an empty region to drop stories into. A name starts with a letter or a digit, is at most 39 characters, has no backtick or line break, and doesn't end in a full stop; a bad name is refused with the reason. A name that matches a domain already on the map, ignoring case, is that domain. An empty domain is kept in your browser, per store, with the arrangement across reloads until a story is in it, and **Reset layout** clears it; the files know a domain only through the tiles that carry it.
- **Help.** The **(i)** at the end of the map's bar shows a short guide to all of this while you hover it, or focus it with Tab or a tap.

Moving a story between columns and editing its card are done on the board; the map only files stories into domains.

## Keeping the filing rules current

Agents file stories by two sets of instructions that `kandalf init` wrote once: the header of `plans.md`, and a note in each linked repo's `AGENTS.md`, `CLAUDE.md` or `GEMINI.md` (between `<!-- kandalf -->` marks). An upgrade of kandalf doesn't touch either. `kandalf notes` says what is behind, and `kandalf notes --update` brings it up to date:

- **The header** gains the rules it lacks, each where the template has it, and the store commits the change. A rule you already have is never changed, however you worded it; the ones whose wording differs from today's are listed for you to compare.
- **The note** in each repo is replaced with today's. The files aren't committed for you.

## Tile conventions

Write these in a tile and the board acts on them.

| You write | What it does |
|---|---|
| `PLN-07` at the start of the line | The tile's key, in the store's prefix. A tile needs a key to be picked up. The board keys a tile you create on it, and `kandalf key next` prints the next free key. |
| `P1` to `P4` after the key | Priority: Highest, High, Medium, Low. |
| `~4h`, `~1.5d`, `~12-18h`, `~1w` after the priority | Estimate, in wall-clock time. Used for sorting and the ETA. |
| `Moves with PLN-74` (or `PLN-74, PLN-75 and PLN-76`) | Linked tiles: move one to another column and the others follow. It works both ways and chains. Nothing follows a tile into Done, out of Done or to Archive. |
| `Part of PLN-70` | The tile is a sub-story of master PLN-70 and folds into it. The master shows a bar and a count of sub-stories done. Moving the master takes its sub-stories along; a sub-story moves alone. A sub-story picked up on its own is given the master's tile to read, wherever the master is. Only the first `Part of` counts. |
| `No pickup` | Never pick this tile up. Use it on a tile one of your own sessions is already working on. |
| `Blocked by PLN-30` (or `PLN-30, PLN-31 and PLN-32`) | The story waits for those tiles: it shows a lock, and pickup leaves it alone in In flight until each is in Done or archived. A key no tile has blocks too. A sub-story takes its master's. Take the words off to start it anyway. |
| `**Decision 2026-11-23:** retire it.` (own line) | The answer to a decision. The board writes it when you answer a tile in a decision column, and archives the tile. Stories it blocked show the answer, and their sessions are given the tile to follow. |
| `**Options 2026-11-24:** recommends B. [Research](active/…-options.md)` and `- **A. Title.** summary` lines under it | The options drafted for a decision. The board writes them when a draft's research doc is ready and lists them on the tile's card, each with **Use**. Write the block yourself in the same shape and the card lists it too. |
| `Model: sonnet` (own line) | The model for this story's session: an alias or a full model id. |
| `Base: feature-x` (own line) | The branch this story is cut from and merged into, instead of the trunk, in each repo that has it. The dialog's Base field writes it. A sub-story takes its master's. |
| `Domain: Admin` (own line) | The part of the product the story is in. The map groups stories by it, and filing a story on the map writes it. A sub-story takes its master's. |
| `Tests: full` (own line) | A merge of this story from the board runs the full suite on the trunk afterwards. `Tests: affected` says not to, whatever the store's `fullSuiteOnMerge`. The dialog's **Full suite on merge** box writes and removes the line. A sub-story takes its master's. |
| `Repos: api, web` (own line) | The repos this story works in. The session starts in the first one. Without the line, it's the main repo. The dialog's Repos boxes write it. A sub-story takes its master's. |
| `Sessions: 3` (own line, on a master) | How many sub-stories have a session at work at once, instead of the store's `"sessions"`. |
| ``Claude session `ab12cd34`: ...`` | Written by the board when a session starts, and shown as its own block. Leave it as it is. |
| `Handoff: ...` (own line) | The session's note on what's done, what it's doing and what's next. Shown in the opened tile's status. |
| `**Status 2026-11-18 14:58:** ...` | Written by the Status button. Each refresh replaces it. |
| `**Ready for review:** ...` (own line) | The story is finished and waits for you. The tile is outlined green. Anyone's line counts, not only a session's. |
| A branch in backticks with a slash in its name (`` `feat/x` ``) on a Done tile, or the branch on its session line | The tile is outlined red until that branch is merged into the trunk (or the story's `Base:` branch) of each repo the tile names: its session line's repos, else its `Repos:` line's, else the main repo. Only in a column named Done. |

Only the board applies links and sub-story moves. A tile moved by hand, in an editor or by an agent, moves alone. The filing rules are in the header of each store's `plans.md`.

## Settings

A store's `board.json` is optional; every key has a default. You can edit it by hand or with the ⚙ button in the board's header, which covers pickup, agent, model, permission mode, stories at once, sessions, full suite on merge, trunk, worktrees, tidy, push and commit delay. Each setting shows its default and, once `board.json` sets it, a mark and a Reset button. Save writes only what you changed (Reset removes the key), and pickup follows without a restart. The store's other facts (name, key, port, folder, repos, branch pattern) are listed read-only; set those in `board.json`.

| Key | Default | What it does |
|---|---|---|
| `name` | the folder's name | Shown in the page title. |
| `key` | `PLN` (`kandalf init` uses the name's first three letters) | Tile key prefix: `PLN-01`, `PLN-02`, ... Capital letters and digits. |
| `port` | `7788` | Where `kandalf serve` listens. |
| `link` | `plans` | The folder name app repos link the store in by (`docs/plans`). |
| `app` | none | The app repo, relative to the store. Needed for pickup and for the red outline on unmerged Done tiles. |
| `trunk` | `dev` when the app has a dev branch, else `main` (`kandalf init` picks `dev` if there is one, else the app's default branch) | The app branch feature branches are cut from and merged into. A tile's `Base:` line picks another for its story. |
| `repos` | none | Other repos stories can work in, by name: a path relative to the store, or `{"path", "trunk"}`. Without a trunk, a repo uses the store's `trunk`, else `dev` when it has one, else `main`. A tile's `Repos:` line picks them. |
| `agent` | `claude` | The agent pickup hands stories to: `claude` (Claude Code), `copilot` (GitHub Copilot CLI) or `agy` (Antigravity CLI). |
| `pickup` | `false` | Start an agent session (`agent`) for each tile that lands in In flight. Needs `app`. |
| `pickupColumn` | any column named like In flight: in-flight, in progress (typos too), doing, WIP, working, underway, ongoing, active; not one that says "not" | The one column, by name (any case), whose tiles are picked up. |
| `permission` | `auto` | The permission mode sessions start in: `auto`, `acceptEdits`, `manual`, `dontAsk`, `plan` or `bypassPermissions` (`claude --help` lists them). |
| `model` | `opus` (`auto` for copilot and agy) | The model sessions run on: an alias or a full id. A tile's `Model:` line overrides it. |
| `parallel` | `1` | How many standalone tiles have a session at work at once. The rest are queued, each with **Start now**. |
| `sessions` | `2` | How many of a master's sub-stories have a session at work at once. A master's `Sessions:` line overrides it. |
| `fullSuiteOnMerge` | `false` | `true`: a merge from the board runs the full suite on the trunk before the push (for a sub-story, only when it's the last of its master's), unless the tile has a `Tests: affected` line. `false`: only when the tile has a `Tests: full` line. Either way, sessions run only the tests for what they change. |
| `branch` | `{key}-{slug}` | A session's feature branch. `{key}` is the tile's key in lower case, `{KEY}` as written, `{slug}` its title, `{app}` the app folder's name. Must contain `{key}` or `{KEY}`. |
| `worktree` | `{app}/.worktrees/{key}` | Its worktree, relative to the folder the app is in, with the same placeholders. Must contain `{key}` or `{KEY}`, and may climb at most one folder (`../`). The default is inside the app; when the app doesn't ignore that folder yet, pickup adds it to the app's `info/exclude`. `{app}-{key}` puts it beside the app. |
| `hooks` | `true` | The board's hooks (see [Hooks](#hooks)). `false`, set by `kandalf init --no-hooks`, stops `kandalf serve` adding them. |
| `tidy` | `true` | Archiving a story removes its worktree, branch and session, when nothing would be lost. |
| `push` | `true` | The commit queue pushes each commit to the store's upstream, when it has one. |
| `commitDelay` | `300` | Seconds of quiet before the queue commits. |

Environment variables:

| Variable | What it does |
|---|---|
| `BOARD_COMMIT_DELAY` | Seconds of quiet before a commit. Overrides `commitDelay`. |
| `BOARD_NO_OPEN` | When set, `kandalf serve` doesn't open the browser (like `--no-open`). It doesn't either when its output isn't a terminal. |
| `BOARD_PICKUP_SETTLE` | Seconds a just-started session counts as at work before it shows itself busy (default `90`). |
| `PLANS_APP_REPO`, `PLANS_APP_TRUNK` | Override `app` and `trunk`. |

**Your own working rules.** A `pickup.md` at the top of the store replaces the default rules every session is given (`kandalf/templates/pickup.md`). The default says commit subjects carry the key, and adds a last Gates sub-task, which is unticked again (or joined by a new one) whenever tests run later. `{key}` in the file stands for the tile's key. The rules come after "Tick each sub-task with its commit as it lands." in the session's list, so a first line that's a sentence continues that rule, and each `- ` line is a rule of its own. An empty file adds no rules. What the board reads back (the session line, ticked sub-tasks, `Handoff:`, `Ready for review`) is always asked for.

## Commands

With no `--dir`, `kandalf` uses the store you're in, or the one the current git repo links in under `docs/`, so it works from anywhere in an app checkout or worktree.

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
kandalf attach ID|KEY       open a story's session, or a draft's when the key has no story session (for Claude, `claude attach`; for Copilot or Antigravity, follow it, then `copilot --resume` or `agy --conversation`)
kandalf status KEY          the Status line for a story's session, as the tile's Status button writes it
kandalf agent [NAME]        the agent pickup hands stories to; or switch to claude, copilot or agy (--force)
kandalf repo add PATH       another repo stories can work in (--name NAME, --trunk BRANCH)
kandalf repo list           the store's repos, the main one first
kandalf repo rm NAME        take a repo out of board.json (its docs/plans link stays)
kandalf uninstall           remove the docs/plans link from every repo of the store (the store stays)
kandalf doctor              what works and what doesn't, one line per check
kandalf notes               whether the filing rules agents read are today's (--update: bring them up to date)
kandalf hooks               print the hooks block (--add: put it in the store and link it into the app)
kandalf hook autocommit     the Claude Code hook that queues an edit (reads the call on stdin)
kandalf hook check          the Claude Code hook that flags a file the board would open view-only
```

Run in a terminal, `kandalf init` asks for whatever you don't pass. `--yes` takes the defaults: pickup off and hooks on. With `--pickup`, the agent is `claude` if it's on your PATH, else `copilot` if that is, else `agy` if that is.

## Commits and pushes

You never commit board changes by hand. A change is saved to disk at once and committed later, in the store's own repo.

1. **Every change is queued:** each save, archive and promote the server makes, and, through the hooks below, each Edit/Write of a `.md` file inside a store in Claude Code. The queue is the store's `.git/board-pending.json`.
2. **The commit waits for quiet.** After 5 minutes with no new change (`commitDelay`), everything queued goes in as one commit. Every change restarts the wait.
3. **The subject says what changed overall**, e.g. `board: PLN-26 backlog.md → To do, PLN-33 archived`. The body lists each step.
4. **Each commit is pushed** in the background. A failed push is logged in `.git/board-push.log`, and the next push sends everything waiting. A store with no upstream isn't pushed, and `"push": false` commits without pushing.

On start, the server also picks up changes left over from before. A commit takes only the queued paths: anything changed another way (a shell `mv` or `sed`) isn't queued, so commit it yourself or restart the server. A running server keeps the code it started with, so restart it after updating kandalf.

## Hooks

The board's hooks are Claude Code hooks. The Copilot CLI reads them too, and an Antigravity session gets them written into its worktree's `.agents/hooks.json`. They live in the store's `.claude/settings.json`, committed with the rest of it. They're part of the board, not an option: `kandalf init` puts them there, and `kandalf serve` adds them on start to a store that lacks them (as `kandalf hooks --add` does), unless `"hooks": false` is set (`kandalf init --no-hooks` sets it). Nothing is committed to the app:

- **Pickup sessions** start with `claude --settings <store>/.claude/settings.json`, so every worktree gets the hooks without a file of its own.
- **Your own sessions in the app** get them through `.claude/settings.local.json`, a gitignored symlink to the store's file. If the app already has its own `settings.local.json`, the hooks are merged into it and everything else in it is kept.
- **Old hooks** that run `board` (the command's name before it was `kandalf`) do nothing now. `kandalf hooks --add` replaces them in the store and in the app's `settings.local.json`. `kandalf doctor` points out any left in the app's committed `.claude/settings.json`, for you to take out by hand.

`kandalf doctor` says where the hooks are, and `kandalf hooks` prints the block:

```json
"PostToolUse": [{ "matcher": "Edit|Write", "hooks": [
  { "type": "command", "command": "command -v kandalf >/dev/null && kandalf hook autocommit 2>/dev/null || true" },
  { "type": "command", "command": "command -v kandalf >/dev/null || exit 0; kandalf hook check" }
]}]
```

- **`kandalf hook autocommit`** queues an Edit/Write of a `.md` file inside a store for the next batched commit. It ignores everything else and never fails the tool call.
- **`kandalf hook check`** runs after an edit to `plans.md`, `backlog.md` or `archive/archived.md`. If the board would open the file view-only, or the file has two In flight or two Done columns, it hands the error back to the session that made the edit, so the session fixes the file at once. It needs node.

## Safety

`kandalf serve` listens on 127.0.0.1 only. It answers only requests addressed to `127.0.0.1:<port>` or `localhost:<port>`, and takes changes only from its own page, so another site open in your browser can't use it. Requests with no `Origin` (curl, scripts) are answered.

What it can do on your machine, and the setting for each:

- **Start agent sessions** in your repos, one per tile that lands in In flight. Only with `"pickup": true`, which is off by default. Sessions run in the mode `"permission"` names: by default `auto`, where a session edits files and runs commands without asking and Claude Code's auto-mode checks decide what's allowed. Pick another mode for more stops. A session may change every repo its tile names, but only in worktrees, never in your checkouts, and it's told not to merge or push in any of them.
- **Delete branches, worktrees and sessions** when a story is archived, but only ones already in the trunk with nothing uncommitted, and never a session still at work. `"tidy": false` keeps them all.
- **Commit and push the store.** Every board change is committed in the store's own repo and pushed to its upstream, if it has one. `"push": false` commits without pushing.

When it starts, the server says whether pickup is on and, if not, why and how to fix it.

## Platforms and requirements

- Python 3.10 or later, standard library only.
- git.
- node, only for `kandalf check` and the check hook.
- For pickup only, one agent CLI on your PATH:
  - [Claude Code](https://claude.com/claude-code) (`claude`), with `--bg`, `agents --json`, `logs`, `stop` and `rm` (`claude update` brings them), or
  - the [GitHub Copilot CLI](https://github.com/features/copilot/cli) (`copilot`), signed in (`copilot login`), with `-p`, `--session-id`, `--resume`, `--add-dir`, `--name`, `--allow-all-tools` and `--allow-all-paths`, or
  - the [Antigravity CLI](https://antigravity.google/docs) (`agy`), signed in, with `--print`, `--output-format`, `--conversation`, `--add-dir`, `--mode` and `--dangerously-skip-permissions`.
- macOS and Linux. Windows is untested.

## Development

```
bin/kandalf               the command (a script; symlink it onto your PATH)
kandalf/
  cli.py                  the commands
  serve.py, board.html    the server and its page
  tiles.py                reads and edits board markdown: tiles, their attribute lines, the session line, and a
                          snapshot of plans, backlog and the archive log (index, lineage, In flight tiles)
  pickup.py               hands a tile in In flight to an agent
  drafts.py               drafts a decision's options: the research doc's reading, the draft's session and worktree
  jobs.py                 the job ledger: board-pickup.json, locked across processes, and its queries
  agent.py                the agent in use (board.json "agent")
  settings.py             the settings panel's editable board.json keys, checked and written
  claude_code.py          every call to the claude CLI
  copilot_cli.py          every call to the copilot CLI
  agy_cli.py              every call to the agy CLI
  init.py                 kandalf init, kandalf doctor, kandalf agent, kandalf hooks (and --add)
  gitlog.py               the batched commit queue
  store.py                what a store is, and its board.json
  pln.py                  tile keys
  stamp.py                where a build came from, for the page's corner (setup.py writes it at build time)
  autocommit.py, checkhook.py   the Claude Code hooks
  check.js                does a board file round-trip through the page's parser?
  templates/              starting files for a new store, and the default pickup.md
tile_cases.json         cases for the board grammar: kandalf/tiles.py and board.html each read every one
view_cases.json         cases for the ledger's view of a job and the page's fallback to the same table
test_*.py, test_page.js   the tests
```

Run the tests:

```bash
python3 -m unittest discover -p "test_*.py"
node --test test_page.js
```

The two `*_cases.json` files are run by both readers: `test_tiles.py` and `test_jobs.py` on the Python side, `test_page.js` on the page's. A rule that board.html and the server both read gets a case in one of them.

The page's bottom right corner says which kandalf is serving it: `kandalf 1.2.0` for a build of a release's tag with every file committed, else `kandalf 1.2.0+dev` and when it was built, with the commit in its tooltip. `setup.py` stamps an installed copy when it is built; one run from a checkout asks git when the server starts.

To support another agent, `kandalf/claude_code.py`, `kandalf/copilot_cli.py` and `kandalf/agy_cli.py` are the seams: nothing else runs `claude`, `copilot` or `agy`, and its docstring lists the functions another agent's module would provide.

## Licence

MIT, see `LICENSE`. The bundled marked is under its own licence: see `THIRD_PARTY`.
