# Setting up n8n for B3's Notification agent

B3's Notification node POSTs a JSON payload to a webhook URL after a human
decision is made. n8n is what turns that webhook call into an actual
notification — and building/editing that workflow visually (no code) is
part of the real B3 pitch to a client, not just a convenient shortcut.

## 1. Start n8n

It's already in `docker-compose.yml`:
```powershell
docker compose up -d n8n
```
Open `http://localhost:5678` — first run asks you to create a local account
(stays entirely on your machine, nothing external).

## 2. Build the workflow

1. **New workflow** → add a **Webhook** node
   - HTTP Method: `POST`
   - Path: anything, e.g. `b3-approval`
   - Response: "Immediately" is simplest
2. Click **Listen for test event** (n8n will show you a test webhook URL)
3. Add whatever node you want after the Webhook — for a first pass, a
   **Set** node or **NoOp** node is enough to prove the flow works. Later,
   swap in an **Email** or **Slack** node reading fields from the webhook
   payload (`request_text`, `requested_days`, `approver`, `decision`,
   `approver_note`).
4. **Save**, then **Activate** the workflow (toggle top-right) — this gives
   you the permanent **Production URL**, not just the test one.

## 3. Wire it into the suite

Copy the production webhook URL into `.env`:
```
N8N_WEBHOOK_URL=http://localhost:5678/webhook/b3-approval
```
Restart Streamlit. Submit a leave request in B3, approve or reject it — the
Notification step will POST to that URL, and you'll see the event land in
n8n's execution log.

## What the payload looks like

```json
{
  "request_text": "...",
  "requested_days": 3,
  "approver": "Direct Manager",
  "decision": "approved",
  "approver_note": "..."
}
```

## If you skip this

That's fine — `N8N_WEBHOOK_URL` empty in `.env` is the default. The
Notification node detects that and logs "notification skipped" rather than
failing the whole run. B3's approval flow (classify → policy-check → route →
human decision) works completely standalone; n8n is the last-mile delivery
piece, not a dependency for testing everything before it.
