# Mech Royale bot (team "5-hour limit reached")

The bot for the Builders Buildathon Mech Royale track (Nov 20-22, 2026). Planning notes live in the private repo `buildathon-2026` under `tracks/builders-mech-royale/`.

## Layout
| Path | What |
|---|---|
| `bot.py` | The bot. For now it's the kit's example bot under our name. |
| `mechfight.py` | The kit's I/O wrapper (stdin/stdout loop, typed messages, debug drawing). |
| `src/` | Our modules (world model, navigation, combat). Copied into the image. |
| `Dockerfile` | What the scorer builds. Copies only `bot.py`, `mechfight.py` and `src/`. |
| `kit/` | The untouched starter kit: `protocol.md` (the rules), schema, conformance check, map, TypeScript wrapper. Not copied into the image; the map is mounted at `/arena` in a match. |
| `tools/check.ps1` | Runs the conformance check locally, then in the real sandbox (Docker: 1 CPU, 512 MB, no network, read-only). |

## Check before every submission
```
powershell tools/check.ps1
```
Docker Desktop has to be running.

## Submission rules (we get 4 an hour; rejected ones count)
1. Only submit a version that beats the last submitted one in `tools/sim.py --squad` over at least 20 matches with the same seeds, and doesn't get worse in `--ffa`.
2. It must pass `tools/check.ps1` (both checks) first, so it can't be rejected.
3. At most 2 submissions an hour. Keep the other 2 as a buffer for fixes.
4. Group several improvements into one submission instead of submitting each tweak.
5. Never resubmit a commit that has already been scored. Log every submission in the planning repo's `LOG.md`.

## Submitting
The scorer needs a **public** GitHub repo with the Dockerfile at its top. Submit the repo URL on the track page (4 per hour), then log it in the buildathon repo with `tools/log.py`.
