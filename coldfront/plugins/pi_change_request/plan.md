# Structure
Request model, build form off this
Request status model

Approvals based on resources in project, so need a formset to create an entry for each one

Create settings page where admins can enable what resources need team approval
Settings page could also show historical entries
Super user can see all entries, staff can only see entries containing a resource they manage

# Workflow
* Request comes in
* Email sent to rtprojects ticket queue
* Request status set to New
* Email goes to each teams ticket queue that manages a resource in the project, make this toggleable
* Admin can reject request or initiate it
* Initiated request status is set to Awaiting Approvals if it contains resources that need approvals, or set to Ready if not
* If status is awaiting approvals and one is denied then set request status to Blocked
* If status is awaiting approvals and all are approved then set request status to Ready
* Each team managing a resource that's active in the project will need to approve the PI change
* Email sent to rtprojects ticket queue about it being ready, maybe to same ticket? Probably not possible to do automatically
* Admin approves it and PI is auto updated

# Restrictions
The new PI must exist and be a manager in a project.

# Outstanding Quastions
1. Should we allow an admin to assign a ticket number to a request. This might allow is to send updates to the ticket via email instead of creating a new one.
