# Project PI Change Request

Manages the transfer of a ColdFront project from one PI to another through an
approval workflow. A request is initiated by a project manager, approved by the
current and new PI (plus optional per-resource reviewers), and activated by
center staff, at which point the new PI becomes the project's owner and the
outgoing PI remains an active manager.

## Enabling

Enable the plugin with an environment variable:

```sh
PLUGIN_PI_CHANGE_REQUEST=True
```

by adding it directly in `coldfront.env` or `local_settings.py`.

The plugin serves its pages at `/project-pi-change/`. Run migrations
afterwards, then backfill the per-resource approval settings (only necessary if for
some reason the migration doesn't at them.):

```sh
python manage.py migrate
python manage.py add_pi_change_request_defaults
```

All workflow statuses are seeded by the plugin's migrations. New allocatable
resources automatically get an approval setting row (defaulting to off) via a
`post_save` signal on `Resource`.

## Workflow

```
New ──> Awaiting Approvals ──> Ready ──> Complete (staff activate)
              │
              └──> Blocked (any denial) ──> Rejected (staff deny)
```

A request's status is recomputed from its approvals after every response: all
approved means **Ready**, any denial means **Blocked**. A request skips
"Awaiting Approvals" when the last approval resolves the outcome immediately,
and staff may deny from any active state.

- A manager of the project (or a superuser) initiates the request for a project
  not in a status of Archived, Denied, Expired, or Renewal Denied,
  choosing the new PI from the project's active managers. The initiator's own
  approval, when they are the current or new PI, is recorded automatically.
- Center staff activate (**Complete**) or deny (**Rejected**) a request from
  the center page. Activating swaps the PI and leaves the outgoing PI an active
  manager. Pending approvals are cancelled when the request is denied.

### User approvals

The current PI and the new PI each get an approval row. A decline requires a
reason, which is shared with the Pi change requests participants.

### Resource approvals

A request only includes resources the
project holds a live allocation on ("Active" or "Renewal Requested"). A resource
denial blocks the request; an optional reason can be provided and is shared the
same way.

## Permissions

The plugin defines four permissions:

| Permission | Grants |
|---|---|
| `pi_change_request.view_projectpichangerequest` | Access to the center and detail pages |
| `pi_change_request.change_projectpichangerequest` | Activate or deny requests |
| `pi_change_request.change_projectpichangerequestresourceapproval` | Respond to resource approvals |
| `pi_change_request.change_projectpichangerequestresourceapprovalsetting` | Toggle the "Requires Approval" settings |

Who may respond to a resource approval (or toggle its setting) depends on the
resource's review groups:

- A resource **with** review groups: a member of one of its review groups
  holding the permission. Each group can be mapped to a ticket-queue email
  (via the `ProjectPiChangeRequestReviewGroupTicketEmail` admin) to receive
  pending approvals; the queue with the permission receives one email per
  request listing its resources.
- A resource **without** review groups: any staff user.

Superusers bypass all of the above.

## Notifications

Emails are sent when messaging is enabled (`EMAIL_ENABLED`). Where each one goes:

| Template (under `templates/pi_change_request/email/`) | Sent when | Recipients |
|---|---|---|
| `new_pi_change_request.txt` | A request is submitted | Ticket system address (`EMAIL_TICKET_SYSTEM_ADDRESS`) |
| `pi_change_request_user_approval.txt` | A user's response is needed | Each approver with a pending response |
| `pi_change_request_resource_approval.txt` | A review group's response is needed | Each mapped review-group ticket email |
| `pi_change_request_user_response.txt` | A user approval is answered | Center alerts address (`EMAIL_ALERTS_EMAIL_ADDRESS`) |
| `pi_change_request_resource_response.txt` | A resource approval is answered | Center alerts address (`EMAIL_ALERTS_EMAIL_ADDRESS`) |
| `pi_change_request_ready.txt` | All approvals are in | Center alerts address (`EMAIL_ALERTS_EMAIL_ADDRESS`) |
| `pi_change_request_blocked.txt` | A denial blocks the request | The request's participants (current PI, new PI, initiator) |
| `pi_change_request_approved.txt` | A request is activated | The request's participants |
| `pi_change_request_denied.txt` | A request is denied | The request's participants |

A Slack message is also sent for new requests when `SLACK_MESSAGING_ENABLED` is on.

## Signals

The plugin sends four custom signals so other systems can react to the
workflow. All fire after the relevant transaction commits, and each sends the
view (or ModelAdmin) class as `sender` so receivers can distinguish, for
example, an approval from a decline, or a center submission from an
admin-created request.

| Signal | Sent when | Keyword arguments |
|---|---|---|
| `pi_change_request_created` | A request is submitted (view or admin) | `pi_change_request_pk` |
| `pi_change_request_user_response` | A user approval is answered, including the initiator's automatic approval | `user_approval_pk`, `pi_change_request_pk` |
| `pi_change_request_resource_response` | A resource approval is answered | `resource_approval_pk`, `pi_change_request_pk` |
| `pi_change_request_completed` | A request is activated or denied | `pi_change_request_pk` |

Receivers fetch the objects they need by pk; the request's status distinguishes
"Complete" from "Rejected" for the completed signal.

## History

Requests and approval rows are tracked with `django-simple-history`. The center
page combines request creations and approval status changes into a single
chronological table, scoped to the resources each viewer manages. Every
approval also records its responder and response time on the detail page.

## Admin

The Django admin mirrors the creation flow for requests created there, without
sending notifications (signals still fire). Setting a request's status there bypasses the center actions, so flipping a request to "Complete" does not apply the PI change.
