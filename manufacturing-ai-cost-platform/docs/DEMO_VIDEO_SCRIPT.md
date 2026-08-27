# Demo Video Script

Shot-by-shot recording script for the demonstration video. Target length **5:00**,
hard ceiling 6:00.

This is a *recording* script — it assumes you can retake a shot and edit. For
the live, unedited version, use the five-minute story in
[`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) section 6 instead.

---

## Before you record

**Set the stage** (10 minutes, once):

1. `backend/.env`: `APP_ENV=development`, `AUTH_MODE=development`,
   `MODEL_GATEWAY_PROVIDER=mock`, `REDIS_ENABLED=false`, a real `JWT_SECRET`.
2. From `backend/`: `.venv/Scripts/python -m app.db.seed.demo_cli reset`
3. Start the backend on `:8000` and the frontend on `:5173`.
4. Mint three tokens and keep them in a scratch file:
   `AI_ENGINEER`, `ADMIN`, `VIEWER`.
5. Paste the ADMIN token into the field in the application header. Confirm the
   chip reads **Live API**, not *Demo fixtures*. **If you skip this, every page
   shows fixture data and the video is wrong.**
6. Open `http://localhost:5173/dashboard` and confirm you see 500 requests and
   the spike on the trend chart.

**Recording setup:**

- 1920×1080. Terminal font at 16pt or larger — judges may watch on a laptop.
- Two windows arranged in advance: browser left, terminal right. Do not
  alt-tab hunting for a window on camera.
- Pre-type every command into the terminal's history so you can recall it with
  the up-arrow rather than typing live.
- Zoom to 125% in the browser. Default text is too small on video.

**Reset between takes** — this is not optional, the story is one-directional:

```bash
.venv/Scripts/python -m app.db.seed.demo_cli reset
```

Once `demo-opt-rec-001` is applied it stays applied, and a second approval
returns `409` on camera.

---

## Shot list

Timings are cumulative. The narration is written to be read aloud at a natural
pace — roughly 150 words per minute.

---

### Shot 1 — The problem · 0:00–0:30

**On screen:** Title card, then the Dashboard.

> **Narration**
>
> A manufacturing plant runs AI on nearly every part coming off the line —
> vision-based quality checks, predictive maintenance, supply-chain
> forecasting. Every one of those calls costs money.
>
> Most teams find out what that cost was at the end of the month, from an
> invoice. By then the decisions that caused it are three weeks old, and nobody
> can remember which change did it.
>
> This platform moves that decision to before the spend.

**Direction:** Let the dashboard sit on screen for two full seconds before you
start talking. Give the viewer time to see it.

---

### Shot 2 — Decide before you spend · 0:30–1:30

**On screen:** Terminal. Run the execute call, then let the JSON fill the pane.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/execute -H "Authorization: Bearer $ENG" -H "Content-Type: application/json" -d "$REQ" | python -m json.tool
```

**Highlight on screen:** draw a box around `execution_plan` in the edit.

> **Narration**
>
> Here is a quality inspection being submitted. Watch what comes back.
>
> Before any model was called, the platform had already decided: this request is
> medium complexity, it goes to this vision model, it gets a four-thousand-token
> ceiling and at most five tool calls, the budget check says allow, and all of
> that came from routing policy version one.
>
> That decision is made by deterministic rules and a versioned policy. We do not
> call an expensive model to decide which model to call — that would be paying
> twice to answer a question we can answer with code.

**Then point the cursor at `cost`:**

> And look at the cost field. Amount: null. Provenance: unavailable.
>
> The gateway reported no token usage for this run, so there is nothing to
> price. We report unknown. We do not report zero. Zero is a number a dashboard
> will happily average, and averaging a number nobody measured is how cost tools
> start lying to you.

**Direction:** This is the most important shot in the video. Do not rush the
null-versus-zero point — it is the credibility anchor for everything after it.

---

### Shot 3 — Something changed · 1:30–2:15

**On screen:** Browser. Dashboard trend chart, then scroll to the anomalies
panel.

> **Narration**
>
> Now the cost view. Six flat days, then this.
>
> The platform flagged it on its own: expected forty-eight cents, actual six
> dollars twenty-four, twelve hundred percent over.
>
> And the cause is not a guess — it is in the telemetry. Volume tripled, and
> every one of those requests routed to the high-capability vision model,
> including simple single-part inspections that never needed it.

**Cut to the by-model breakdown:**

```bash
curl -s -H "Authorization: Bearer $ADM" http://127.0.0.1:8000/api/v1/cost/by-model | python -m json.tool
```

> One hundred and twenty requests on the expensive model cost more than three
> hundred and eighty on the cheaper one. That comparison is the entire argument
> for what happens next.

---

### Shot 4 — What to do about it · 2:15–3:15

**On screen:** Browser. Optimization Center, recommendation card visible in
full.

> **Narration**
>
> The platform's proposal: route the simple checks to the smaller vision model,
> keep the large one for complex and safety-critical work.
>
> Estimated saving, eighteen and a half percent. Quality impact, minus half a
> percent. Latency, up fifteen percent. Risk, low.

**Direction:** pause here. Let the trade-off numbers stay on screen.

> Notice that it reports what this costs you, not just what it saves you. A cost
> tool that shows you only the saving is asking to be trusted with a decision it
> has not shown you enough to make.
>
> And notice the two labels: estimated, and draft. This has not run. Nothing has
> been saved. This is a proposal.

**Optional — include only if you are under time.** Cut to the What-if Simulator,
2 seconds on screen:

> The simulator answers the same question for volumes you have not hit yet —
> and labels every figure in the response with what kind of number it is:
> estimated, forecast, simulated.

---

### Shot 5 — A human decides · 3:15–4:00

**On screen:** Terminal. Run the VIEWER approval first.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/optimization/demo-opt-rec-001/approve -H "Authorization: Bearer $VIEW" -H "Content-Type: application/json" -d '{"decision":"APPROVED"}'
```

> **Narration**
>
> First, an analyst tries to approve it.
>
> Forbidden. An analyst cannot approve a change that spends money — and that is
> enforced in the backend, so it holds whether the request comes from our own
> interface or from a terminal like this one. Hiding a button is not a control.

**Cut to the browser.** Click **Approve** in the Optimization Center as ADMIN,
then **Apply**.

> Now a FinOps manager approves it, and applies it.
>
> Version two created. Version one superseded. Recorded against the person who
> approved it. Policies here are immutable and versioned, which is what makes
> this auditable — and reversible.

---

### Shot 6 — It takes effect on its own · 4:00–4:45

**On screen:** Terminal. Recall the **exact same command** from Shot 2 with the
up-arrow. Make the recall visible — the viewer should see you are not typing
something new.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ai/execute -H "Authorization: Bearer $ENG" -H "Content-Type: application/json" -d "$REQ" | python -m json.tool
```

**Highlight:** `routing_policy_version: 2`

> **Narration**
>
> Same request. No restart, no redeploy, no code change.
>
> Routing policy version two. The next execution picked up the new policy by
> itself.
>
> That is the loop closing. A workload creates demand. The orchestrator decides
> before spending. Telemetry records what happened. Anomaly detection catches
> the change. Optimization proposes a fix. A human approves it. A versioned
> policy activates. And the next request routes under it.

**Direction:** the version number is small on screen. Zoom in on it in the edit,
or the whole payoff is invisible.

---

### Shot 7 — Close · 4:45–5:00

**On screen:** Dashboard, or a closing card.

> **Narration**
>
> One last thing, and it is the point.
>
> Every number in this demo is simulated or estimated. This is a seeded dataset,
> not production spend — and the platform labels which is which on every single
> field, rather than blending them into one number that looks more impressive
> than it is.
>
> What is real is the control loop. And it runs end to end.

---

## Things that will ruin a take

| Problem | Prevention |
|---|---|
| Every page shows fixture data | Connect the ADMIN token in the header first. Chip must read **Live API** |
| `409` on approve | You did not reset after the last take |
| `401 invalid_token` | Token minted with a different `JWT_SECRET` than the running server — run both from `backend/` |
| Readiness shows `not_ready` | `REDIS_ENABLED=true` with no Redis running |
| `routing_policy_version` unreadable | Zoom in during the edit. It is the payoff of the whole video |
| Dead air while you find a window | Arrange both windows before recording; use up-arrow for every command |

---

## If you have only three minutes

Cut shots 3 and 4 down to one sentence each and drop the What-if Simulator
entirely. **Never cut shot 2 or shot 6** — they are the claim and its proof.
Everything between them is supporting evidence.

## If you have eight minutes

Add, after shot 6:

- **Rollback** — `POST /optimization/{id}/rollback`, showing v2 reverting and
  v1 returning to active. Reversibility is a strong point and takes 30 seconds.
- **Guardrails** — a prompt-injection attempt refused at the input layer.
- **Tenant isolation** — a cross-tenant read returning `404` rather than `403`,
  because `403` confirms the resource exists.

---

## Claims you must not make on camera

The video is a durable artifact. These are the sentences to keep out of it:

- "This saved X dollars." Nothing was saved. Nothing was billed.
- "In production, this reduces cost by 18%." The 18.5% is an estimate over a
  simulated dataset.
- "It automatically switches to the cheaper model." It advances the policy
  version; it does not currently repin the model. See
  [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) section 3, step 11.
- "It's production-ready." See [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md).
- Any claim about live GenAILab behaviour. The demo runs on the mock gateway,
  and the live service has never been exercised.

The honest version of this demo is more persuasive than the inflated one,
because the subject is *cost integrity*. A judge who catches one inflated number
will discount every other number in the video.
