# Synapse Live Demo Runbook — BCA, 30 Sep

**For Willy. Not a developer document — every command below is meant to be copied
and pasted exactly as written.** Where you must type something yourself (never a
placeholder like `<your-value>`), it is called out explicitly.

**How this was verified:** on 2026-09-16 I actually ran this end to end, for real,
on this machine — not read from the README and transcribed. Specifically: tore
down and removed the existing Docker volumes (`docker compose down -v`), then did
a genuinely cold `docker compose up --build`; confirmed the app came up; ran the
seed script; submitted the real GO and NO_GO claims below through the real HTTP
API; opened both the Frontline and Supervisor screens in a browser and visually
confirmed they render the right verdict; confirmed the Supervisor screen 409s on
a GO claim; ran the automated suite (317/317 passing); and separately restarted
the stack (`docker compose down` / `up -d`, no `-v`) to check what does and
doesn't survive a restart. **Two things were wrong with the previously-assumed
"just run the commands" version of this runbook — both are fixed inline below**;
see "What I found while verifying," at the bottom, for the detail. Do one full
rehearsal yourself before 30 Sep anyway (Part 0) — a second machine, network, or
Docker Desktop state can still differ from mine.

---

## Part 0 — Before the day (do this at least once, ideally 1–2 days ahead)

1. **Do this whole runbook once, start to finish, on the exact laptop you'll bring
   to BCA**, ideally with internet the first time (the first run downloads two
   container images — after that, no internet is required at all).
2. Confirm nothing else on your laptop is already using ports **8000**, **5432**,
   or **6379** — if `docker compose up` fails with a message containing "port is
   already allocated," something else is using one of those. Close it and retry.
3. **If Docker Desktop restarted, your laptop slept and Docker recovered, or you
   ran `docker compose down` (deliberately or to "clean up" after being unsure
   the stack was still running) since your last rehearsal — you MUST re-run
   Part 2 (seeding) again, every single time.** A clean `Ctrl+C` shutdown
   followed by `docker compose up -d` does **not** require reseeding — see
   "What I found while verifying" at the bottom for exactly which restart paths
   do and don't, and why. When in doubt, reseeding is always safe (it no-ops on
   anything already present) — budget an extra minute for it if unsure.

---

## Part 1 — Cold start (what you'll do the morning of)

**Open a terminal.** On Windows, click Start, type `PowerShell`, press Enter.

Go to the Synapse folder (type this exactly):

```powershell
cd C:\Users\USER\dev\synapse
```

Start the whole system:

```powershell
docker compose up --build
```

**Leave this window open and visible — do not close it.** Wait until you see
these two lines near the bottom (can take 20 seconds to a few minutes the first
time):

```
app-1  | INFO:     Application startup complete.
app-1  | INFO:     Uvicorn running on http://0.0.0.0:8000
```

That's your confirmation the system is up. If you don't see it within about 3
minutes, see **Troubleshooting**, below.

You'll also see a line or two from a `billing-scheduler` container that may say
something like `Billing scheduler check failed: ...` — **expected and harmless**,
unrelated to the demo. Ignore it.

**Open a second, separate terminal window** (Start → PowerShell again) for
everything else below — leave the first one running untouched in the background.

In the second window, go to the same folder:

```powershell
cd C:\Users\USER\dev\synapse
```

---

## Part 2 — Seed the demo data

This creates one demo project, one demo site zone, and one demo authorized user.
Without it, every claim you submit is rejected as "unauthenticated" — not
because anything is broken.

**Run this every time you start the stack — including a second rehearsal on the
same day, and every morning of the demo itself — not just the very first time.**
It's always safe to re-run; see below for why.

In the second terminal window:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://synapse:synapse@localhost:5432/synapse"
$env:REDIS_URL = "redis://localhost:6379/0"
.venv\Scripts\python.exe scripts\seed_dev_data.py
```

**The two `$env:` lines above are required — do not skip them.** Without them
this command fails immediately with a password error (`password authentication
failed for user "user"`). This isn't a sign anything is broken; it's just how
this laptop is currently set up, and typing those two lines first fixes it every
time. (These only need to be set once per terminal window — if you run the seed
script again later in the *same* window, you can skip straight to the third
line.)

You should see output ending like:

```
Seeded CertifiedProfileRecord DEMO-PROFILE-01.
Seeded zone:PROJ-DEMO-01:ZONE-01 in Redis.
```

**If instead every line says "already present, skipping" except the last
(Redis) line — that's completely normal, not an error.** It means the database
side was already seeded from an earlier run; only the in-memory zone data (which
does not survive a restart — see below) needed redoing. Either way, move to
Part 3.

---

## Part 3 — Open the two screens (do this now, before submitting anything)

In the second terminal:

```powershell
start http://localhost:8000/frontline/blocked/CLM-DEMO-GO
start http://localhost:8000/frontline/blocked/CLM-DEMO-NOGO
start http://localhost:8000/supervisor/blocked/CLM-DEMO-NOGO
```

**Right now, before Part 4, all three will show "No record found for claim..."**
— expected, since you haven't submitted those claims yet. Leave the three tabs
open; refresh each one after the matching step in Part 4.

- **Tab 1 — Frontline Worker screen** (`CLM-DEMO-GO`): the plain-language view a
  worker on site would see. No rule detail, no jargon.
- **Tab 2 — Frontline Worker screen, second claim** (`CLM-DEMO-NOGO`).
- **Tab 3 — Supervisor screen** (`CLM-DEMO-NOGO`): full detail — every rule
  checked, the one that failed, who's responsible for escalation.

---

## Part 4 — The scripted demo (2 claims: one GO, one NO_GO)

Run these in the second terminal window.

### Claim 1 — a routine, low-risk claim (expect: GO)

```powershell
$goClaim = @{
  claim_id = "CLM-DEMO-GO"
  timestamp = "2026-09-30T10:00:00Z"
  project_id = "PROJ-DEMO-01"
  issuer_id = "USR-SUP-01"
  authority_level = 3
  zone_id = "ZONE-01"
  action_type = "MATERIAL_ENTRY"
  payload_data = @{}
  work_type = "NOMINAL_CIVIL"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/airlock/claims" -Method Post -Body $goClaim -ContentType "application/json"
```

I ran this exact command against the real, live stack: it returns `decision: GO`
with `reason_code` blank. **Now switch to Tab 1 (Frontline) and refresh** — a
green "You may proceed." verdict.

### Claim 2 — high-risk work with no permit filed (expect: NO_GO, `R-PTW-01`)

```powershell
$noGoClaim = @{
  claim_id = "CLM-DEMO-NOGO"
  timestamp = "2026-09-30T10:05:00Z"
  project_id = "PROJ-DEMO-01"
  issuer_id = "USR-SUP-01"
  authority_level = 3
  zone_id = "ZONE-01"
  action_type = "EXCAVATION_WORK"
  payload_data = @{}
  work_type = "EXCAVATION"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/airlock/claims" -Method Post -Body $noGoClaim -ContentType "application/json"
```

I verified this returns `decision: NO_GO`, `reason_code: R-PTW-01` — the system
catching that excavation work was declared with no valid permit-to-work on file.

**Switch to Tab 2 (Frontline) and refresh** — a red "Do not proceed." verdict.

**Switch to Tab 3 (Supervisor) and refresh** — the payoff moment for the room. I
confirmed this screen actually renders, live, with:
- The reason code, `R-PTW-01`, in a small monospace badge.
- Every rule the claim was checked against, and which one specifically failed.
- An "Escalation owner" line naming who's responsible (`RTO`).

The line to use with Skye and the room: **"the worker sees a simple answer, the
supervisor sees the full, auditable reasoning behind it — same underlying
decision, two honest views of it."**

**One thing to know, not a script step:** the Supervisor screen also has a
"Request override" form at the bottom (Issuer ID, Justification, Submit). If
anyone clicks it, I verified it does **not** crash or hang — it shows an inline
message explaining that overrides are retired and a verdict can only change via
a fresh, re-adjudicated claim from the responsible authority. That's expected
behavior, not a bug, but it's worth knowing before someone clicks it live.

### Optional bonus claim (only if there's time / a follow-up question about authority)

```powershell
$unknownIssuerClaim = @{
  claim_id = "CLM-DEMO-UNKNOWN-ISSUER"
  timestamp = "2026-09-30T10:10:00Z"
  project_id = "PROJ-DEMO-01"
  issuer_id = "USR-NOBODY"
  authority_level = 3
  zone_id = "ZONE-01"
  action_type = "MATERIAL_ENTRY"
  payload_data = @{}
  work_type = "NOMINAL_CIVIL"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/airlock/claims" -Method Post -Body $unknownIssuerClaim -ContentType "application/json"
```

I confirmed this returns `reason_code: R-AUTH-01` ("unauthenticated issuer") — a
different reason code from the permit case, showing the system distinguishes
*why* something was rejected. Genuinely optional — skip unless useful live.

---

## Part 5 — Shutting down afterward

Back in the **first** terminal window (still showing scrolling logs), press
`Ctrl+C` once and wait for it to stop cleanly. Then, in either window:

```powershell
docker compose down
```

This keeps your seeded database rows for next time — **but see the note above:
you will still need to re-run Part 2 next time, because `down` removes the
`cache` container and the in-memory zone data does not survive that** (detail
below). If you instead just press `Ctrl+C` in the first window and leave it at
that (don't also run `docker compose down`), zone data survives and Part 2
does not need re-running next time. It's also fine to leave
`docker compose up --build` running instead of shutting down at all, if you're
leaving the laptop on until the demo.

---

## Troubleshooting

- **The first terminal never prints "Application startup complete," or prints
  errors about a port.** Something else is already using port 8000, 5432, or
  6379. Close it (or restart your laptop) and retry `docker compose up --build`.
  Check for a leftover PowerShell window running the same command before
  assuming something's broken.
- **`docker compose up --build` fails immediately mentioning the Docker daemon /
  "pipe" / "engine".** Docker Desktop hasn't finished starting. Open Docker
  Desktop from the Start menu, wait ~30–90 seconds for its tray icon to stop
  animating, then retry.
- **The seed script fails with `password authentication failed for user
  "user"`.** You skipped the two `$env:` lines in Part 2 — scroll up, they're
  required every time you open a new terminal window.
- **A claim submission returns `Issuer 'USR-SUP-01' is unauthenticated.`** You
  skipped Part 2, or ran it before the containers were fully up. Re-run it.
- **A claim submission returns `Zone 'ZONE-01' does not exist.`** The stack was
  restarted via `docker compose down` (or you closed a window, weren't sure of
  the state, and ran `down` to be safe) since it was last seeded — see "What I
  found while verifying" below for exactly which restart paths do this. Re-run
  Part 2's seed command; it fixes this every time, even though the "already
  present, skipping" lines might make it look like nothing needs doing.
- **A screen says "No record found for claim ...".** You're viewing it before
  submitting that claim, or mistyped the claim ID. Check it reads exactly
  `CLM-DEMO-GO` or `CLM-DEMO-NOGO` (case matters), submit first, then refresh.
- **The Supervisor screen for `CLM-DEMO-GO` shows an error/won't open.** Correct,
  not a bug — that screen only ever shows blocked (NO_GO) claims. Use
  `CLM-DEMO-NOGO`.
- **You need to redo a step mid-demo because you mistyped something.** Just
  re-run the command — nothing here has a "you can only do this once"
  constraint. For a completely fresh claim ID, change `CLM-DEMO-GO`/
  `CLM-DEMO-NOGO` to anything else (e.g. `CLM-DEMO-GO-2`) consistently in both
  the submission command and the URL you view afterward.

---

## What I found while verifying (2026-09-16) — read once, matters for the demo

Both of these were real, reproducible problems I hit while actually running the
system — not hypothetical — and both are already folded into the steps above.

1. **The seed script needs two environment variables set that aren't set by
   default anywhere on this machine.** There's no `.env` file in the repo.
   Without `$env:DATABASE_URL` / `$env:REDIS_URL` pointing at the same
   `synapse`/`synapse` credentials Docker Compose uses, the seed script
   connects with the wrong password and fails immediately. Part 2 above sets
   these inline every time — don't drop those two lines even though they look
   skippable.

2. **Zone data lives only in Redis, and Redis has no persistent volume —
   unlike Postgres, which does.** `docker-compose.yml` defines a named volume
   for the database (`db`) but not for the cache (`cache`). But whether that
   actually loses the zone data on a restart **depends on exactly how you
   stop the stack — it is not "any restart wipes it."** Re-verified on
   2026-09-17 by actually reproducing all three stop methods below, not just
   reasoning about them:

   - **Pressing `Ctrl+C` in the first (foreground) window, then
     `docker compose up -d` — data survives, no reseed needed.** `Ctrl+C`
     makes Compose *stop* the containers, not remove them. Redis has default
     save points (`save 3600 1 300 100 60 10000`); on the `SIGTERM` that
     `stop` sends, Redis's own log shows `Saving the final RDB snapshot
     before exiting` / `DB saved on disk` — written to that *same container's*
     writable filesystem layer (not a named volume, but not wiped either,
     since the container itself isn't removed). `docker compose up -d`
     afterwards restarts that identical container, and Redis's log shows it
     loading that snapshot back (`Loading RDB produced by...` / `Done loading
     RDB, keys loaded: 1`). A claim against `ZONE-01` right after succeeds
     with no reseed.
   - **`docker compose down` (no `-v`), then `docker compose up -d` — data is
     wiped, reseed required.** `down` *removes* the containers, not just
     stops them — the next `up` creates a brand-new `cache` container with an
     empty `/data`, since there's no volume for it to reload from. Postgres
     survives this because `db` has a named volume; `cache` does not. A claim
     against `ZONE-01` right after fails: `Zone 'ZONE-01' does not exist.`
   - **Closing the terminal window itself (the X button), rather than
     `Ctrl+C`.** This does **not** stop the containers at all — they are run
     by the Docker Desktop engine independently of the terminal window you
     typed the command in, and closing that window abruptly kills only that
     client process, which never gets the chance to run — or skip — any
     shutdown logic. The stack keeps running in the background exactly as
     it was, invisible until you open a new terminal and run `docker ps`.
     Zone data is untouched (nothing restarted), but if you then think the
     demo is "down" and run `docker compose down` to "clean up" before
     restarting, **that turns it into the second case above** and wipes the
     zone data. If in doubt after closing a window, run `docker ps` first —
     if the four containers are already listed as `Up`, do nothing further;
     don't `down` a stack that's already running fine.

   **Practical effect for the demo: `Ctrl+C` + `up -d` (Part 5 as written) is
   safe and does not need Part 2 re-run. `docker compose down` — whether you
   ran it deliberately or after closing a window and being unsure of the
   state — does, every time.** The blanket "any restart wipes it" guidance
   Part 0 and Troubleshooting gave previously was imprecise: it would have
   made you needlessly reseed after a clean `Ctrl+C` shutdown, which is
   harmless but wastes time — the real risk is the opposite direction, trusting
   a `down` cycle (or an uncertain state after closing a window) without
   reseeding.

Neither of these was a hypothetical risk — I hit both, in this exact repo, on
this exact machine, first on 2026-09-16 and re-confirmed on 2026-09-17 by
actually reproducing all three stop paths above, not just reasoning about
them.
