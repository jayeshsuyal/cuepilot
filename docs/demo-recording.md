# Record the CuePilot demo

Use the configured demo machine with the API, frontend, local services and
authenticated bridge still running. Open the desk at `http://127.0.0.1:5173/`
and use **Open stage** for the projected output. This is a 60-second edited
recording target, not an execution-time claim. Capture actual results; shorten
idle provider waits with a visible “wait shortened” caption.

## Exact controls

1. Select **Local API**, then **Live** under **New run mode**. Every page reload
   resets the mode to **Practice**, so select **Live** again after reloading.
2. Select **Ravi Shah**. Leave **Production notes** unedited so the backend keeps
   its current learned rules. Confirm Ravi's **Presentation** switch is on.
3. Click **Create live run**. If the currently displayed run is blocked or stale,
   the same button reads **Create recovery plan**; confirm **Live** remains
   selected. Wait for the sponsor plan, inspect its three cues, then click
   **Approve plan**.
4. Click **Execute live** once. Let introduction → presentation → holding finish.
   Capture **3 / 3 cues accepted** and **Live execution verified**, including
   verified Rote/Hydra outcome and final RocketRide evidence. Physical cues alone
   do not establish live verification. Capture Ravi's trace before creating Alex;
   the desk has no history picker.
5. Select **Alex Rivera**, keep **Live** selected, and turn **Presentation** off
   **before** creating his run. Click **Create live run** (or **Create recovery
   plan**). Wait for the readiness block: the stage stays holding and the new run
   has zero cue receipts. This case needs no approval or execution. Capture the
   returned reason, then restore Alex's **Presentation** switch to on. Restoring
   readiness does not resume the blocked run.

## 60-second voiceover

| Time | Picture | Voiceover |
| --- | --- | --- |
| 0–10 s | Desk and stage; retained Maya evidence | “CuePilot is a live-show director that learns a safe cue sequence from rehearsal. Maya's completed live rehearsal produced three verified receipts and a reusable Rote procedure.” |
| 10–25 s | Select Ravi, create and review the live plan | “For Ravi, we keep the production notes unchanged. Cognee and Hydra provide the learned rules, Hotdata checks current readiness, and RocketRide prepares the plan for operator approval.” |
| 25–40 s | Approve, execute once, then show verified completion | “After approval, Rote replays that same procedure for a different speaker. The stage moves through introduction, presentation and holding, with three new receipts and verified live execution.” |
| 40–53 s | Alex presentation off, new run blocked | “Now Alex's presentation is unavailable. The fresh readiness check blocks the run before any cue, and the stage stays on holding.” |
| 53–60 s | Restore readiness; leave blocked result visible | “Restoring the asset doesn't restart the blocked plan. Reuse saves the procedure; current checks and explicit approval still control the show.” |

The active play was already learned by Maya in the successful **22:35 UTC,
September 11, 2026** acceptance. A new Maya run now replays it; do not describe
that as fresh learning. Use the [retained learning and replay evidence](../cuepilot/INTEGRATIONS_ROTE.md)
for the opening, and preserve the active pointer, packages and private reports.
The earlier live Alex interruption is separate evidence: it stopped **after
introduction with one receipt**. The manual Alex case above blocks **before
execution with zero receipts**. Neither demonstrates a latency or cost reduction.

See [sponsor readiness](sponsor-readiness.md) for verified scope and
[security status](../SECURITY.md) for the six open dependency advisories. Keep
credentials and raw private evidence out of the recording.
