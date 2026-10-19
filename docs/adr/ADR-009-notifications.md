# ADR-009 — Notifications and approvals from a phone

- **Status:** Accepted

## Context
Approvals (architecture, merge) block runs until a human decides, and the people deciding are usually away
from the dashboard. They read Telegram, WhatsApp and Zalo on their phones.

## Decision
- **Transactional outbox.** `audit.record` writes a `notifications` row in the same transaction as the event
  (approval requested/resolved, run failed/completed, worker offline, run moved). A notification exists exactly
  when its event was committed; nothing is sent from inside a request or a tick.
- **One delivery service per control plane** (`awf.notify.service`, next to the orchestrator) sends pending rows to
  every configured channel and recipient, tracks each recipient separately (no duplicates on retry), retries
  transient errors with exponential backoff up to 8 attempts, and ends in `SENT`, `PARTIAL` or `FAILED`.
  Provider errors are redacted before they are stored or logged (bot tokens).
- **Links, not secrets.** Every message links to `#/approvals/{id}` on `AWF_PUBLIC_URL`. The link carries no
  authority: the dashboard session (or a login) is still required to act on it.
- **Telegram can approve in the chat**, because Telegram authenticates the user who presses a button: only user
  ids in `AWF_TELEGRAM_APPROVERS`, in chats listed in `AWF_TELEGRAM_CHAT_IDS`, may decide, with two taps
  (choose, then confirm). Updates are read by long polling, so the control plane needs no public webhook.
  The actor is recorded as `telegram:<username>`; buttons are removed from every copy once decided anywhere.
- **WhatsApp and Zalo send only** (link to the dashboard). Replies would need public webhooks and their message
  windows (WhatsApp 24 h, Zalo OA consultation window) make two-way flows unreliable. WhatsApp uses an approved
  template when configured; Zalo refreshes its short-lived OA token and stores the rotated pair in `system_state`.

## Consequences
- No channel configured: nothing is queued.
- Only one control-plane process may poll a given Telegram bot.
- Approving from WhatsApp/Zalo requires the dashboard to be reachable from the phone (Tailscale or HTTPS).
