import logging
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from coldfront.core.project.models import ProjectUser
from coldfront.core.test_helpers import utils
from coldfront.core.test_helpers.factories import (
    AllocationFactory,
    AllocationStatusChoiceFactory,
    ProjectFactory,
    ProjectStatusChoiceFactory,
    ProjectUserFactory,
    ProjectUserRoleChoiceFactory,
    ProjectUserStatusChoiceFactory,
    ResourceFactory,
    UserFactory,
)
from coldfront.plugins.pi_change_request.models import (
    ProjectPiChangeRequest,
    ProjectPiChangeRequestResourceApproval,
    ProjectPiChangeRequestResourceApprovalSetting,
    ProjectPiChangeRequestResourceApprovalStatusChoice,
    ProjectPiChangeRequestReviewGroupTicketEmail,
    ProjectPiChangeRequestStatusChoice,
    ProjectPiChangeRequestUserApprovalStatusChoice,
)
from coldfront.plugins.pi_change_request.templatetags.pi_change_request_tags import (
    active_pi_change_request,
    full_name_with_username,
    pi_change_user_approval,
)
from coldfront.plugins.pi_change_request.utils import send_email
from coldfront.plugins.pi_change_request.views import (
    PI_CHANGE_REQUEST_VIEW_PERMISSION,
    RESOURCE_APPROVAL_CHANGE_PERMISSION,
    RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION,
    ProjectPiChangeRequestCenterView,
)

logging.disable(logging.CRITICAL)

# Keep tests deterministic and offline no matter what local_settings enables.
SILENT = override_settings(EMAIL_ENABLED=False, SLACK_MESSAGING_ENABLED=False)


def get_permission(dotted_permission):
    """Return the Permission object for a dotted "app_label.codename" string."""
    app_label, codename = dotted_permission.split(".", 1)
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


def resolve_user_approval(approval, status_name):
    approval.status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get_by_natural_key(status_name)
    approval.save()


class ResourceApprovalSettingSignalTests(TestCase):
    """post_save on Resource keeps a ProjectPiChangeRequestResourceApprovalSetting in sync."""

    def test_allocatable_resource_gets_setting(self):
        resource = ResourceFactory()
        setting = ProjectPiChangeRequestResourceApprovalSetting.objects.get(resource=resource)
        self.assertFalse(setting.requires_approval)

    def test_non_allocatable_resource_gets_no_setting(self):
        resource = ResourceFactory(is_allocatable=False)
        self.assertFalse(ProjectPiChangeRequestResourceApprovalSetting.objects.filter(resource=resource).exists())

    def test_resource_toggled_allocatable_later_gets_setting(self):
        resource = ResourceFactory(is_allocatable=False)
        resource.is_allocatable = True
        resource.save()
        self.assertTrue(ProjectPiChangeRequestResourceApprovalSetting.objects.filter(resource=resource).exists())

    def test_resave_preserves_requires_approval(self):
        resource = ResourceFactory()
        setting = ProjectPiChangeRequestResourceApprovalSetting.objects.get(resource=resource)
        setting.requires_approval = True
        setting.save()

        resource.description = "Edited after the fact"
        resource.save()

        setting.refresh_from_db()
        self.assertTrue(setting.requires_approval)
        self.assertEqual(ProjectPiChangeRequestResourceApprovalSetting.objects.filter(resource=resource).count(), 1)


class StatusChoiceModelTests(TestCase):
    """All three status-choice models share natural-key behavior and ordering via the abstract base."""

    def test_natural_key_behavior_and_ordering(self):
        cases = [
            (ProjectPiChangeRequestStatusChoice, "New"),
            (ProjectPiChangeRequestResourceApprovalStatusChoice, "Pending"),
            (ProjectPiChangeRequestUserApprovalStatusChoice, "Pending"),
        ]
        for model, name in cases:
            with self.subTest(model=model.__name__):
                choice = model.objects.get_by_natural_key(name)
                self.assertEqual(choice.name, name)
                self.assertEqual(choice.natural_key(), (name,))
                self.assertEqual(str(choice), name)

                names = list(model.objects.values_list("name", flat=True))
                self.assertEqual(names, sorted(names))

    def test_models_keep_separate_tables(self):
        with self.assertRaises(ProjectPiChangeRequestStatusChoice.DoesNotExist):
            ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Pending")


class PiChangeRequestTestBase(TestCase):
    """A project with two active managers, one allocatable resource, and an active allocation."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = UserFactory(is_staff=True, is_superuser=True)
        cls.project = ProjectFactory(status=ProjectStatusChoiceFactory(name="Active"))

        manager_role = ProjectUserRoleChoiceFactory(name="Manager")
        ProjectUserFactory(project=cls.project, role=manager_role, user=cls.project.pi)
        cls.new_pi = UserFactory()
        ProjectUserFactory(project=cls.project, role=manager_role, user=cls.new_pi)

        cls.project_user = ProjectUserFactory(project=cls.project)  # plain "User" role
        cls.outsider = UserFactory()

        # is_allocatable defaults to True, so the post_save signal creates the setting row
        cls.resource = ResourceFactory()
        cls.allocation = AllocationFactory(status=AllocationStatusChoiceFactory(name="Active"), project=cls.project)
        cls.allocation.resources.add(cls.resource)

    @classmethod
    def set_requires_approval(cls, resource, requires_approval):
        ProjectPiChangeRequestResourceApprovalSetting.objects.update_or_create(
            resource=resource, defaults={"requires_approval": requires_approval}
        )

    def create_request(self, status_name="New", resources=None, with_approvals=True, initiator=None):
        """Create a request directly, bypassing form validation like the seeded state would."""
        if initiator is None:
            initiator = self.project.pi
        request_obj = ProjectPiChangeRequest.objects.create(
            project=self.project,
            current_pi=self.project.pi,
            new_pi=self.new_pi,
            initiator=initiator,
            justification="Test justification",
            status=ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key(status_name),
        )
        request_obj.resources.set(resources if resources is not None else [self.resource])
        if with_approvals:
            request_obj.create_resource_approvals()
            request_obj.create_user_approvals([self.project.pi, self.new_pi])
        return request_obj


class AddPiChangeRequestDefaultsCommandTests(PiChangeRequestTestBase):
    """The defaults command backfills settings without clobbering existing ones."""

    def test_creates_missing_setting_with_default(self):
        ProjectPiChangeRequestResourceApprovalSetting.objects.filter(resource=self.resource).delete()
        call_command("add_pi_change_request_defaults")
        setting = ProjectPiChangeRequestResourceApprovalSetting.objects.get(resource=self.resource)
        self.assertFalse(setting.requires_approval)

    def test_preserves_existing_requires_approval(self):
        self.set_requires_approval(self.resource, True)
        call_command("add_pi_change_request_defaults")
        setting = ProjectPiChangeRequestResourceApprovalSetting.objects.get(resource=self.resource)
        self.assertTrue(setting.requires_approval)


class ProjectPiChangeRequestModelTests(PiChangeRequestTestBase):
    def build_request(self, new_pi):
        return ProjectPiChangeRequest(
            project=self.project,
            current_pi=self.project.pi,
            new_pi=new_pi,
            initiator=self.project.pi,
            justification="Test justification",
            status=ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("New"),
        )

    def test_clean_requires_new_pi_to_be_manager(self):
        with self.assertRaises(ValidationError):
            self.build_request(new_pi=self.project_user.user).clean()

    def test_clean_blocks_second_active_request(self):
        self.create_request()
        with self.assertRaises(ValidationError):
            self.build_request(new_pi=self.new_pi).clean()

    def test_clean_allows_request_after_previous_is_terminal(self):
        self.create_request(status_name="Rejected")
        self.build_request(new_pi=self.new_pi).clean()  # should not raise

    def test_clean_rejects_disallowed_project_status(self):
        self.project.status = ProjectStatusChoiceFactory(name="Archived")
        self.project.save()
        with self.assertRaises(ValidationError):
            self.build_request(new_pi=self.new_pi).clean()

    def test_clean_rejects_new_pi_matching_current_pi(self):
        with self.assertRaises(ValidationError):
            self.build_request(new_pi=self.project.pi).clean()

    def test_create_resource_approvals_only_for_required_resources(self):
        self.set_requires_approval(self.resource, True)
        request_obj = self.create_request(with_approvals=False)
        approvals = request_obj.create_resource_approvals()
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].resource, self.resource)
        self.assertEqual(approvals[0].status.name, "Pending")

    def test_duplicate_resource_approval_blocked(self):
        self.set_requires_approval(self.resource, True)
        request_obj = self.create_request(with_approvals=False)
        request_obj.create_resource_approvals()
        with self.assertRaises(IntegrityError):
            ProjectPiChangeRequestResourceApproval.objects.create(
                request=request_obj,
                resource=self.resource,
                status=ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key("Pending"),
            )

    def test_create_user_approvals_dedupes(self):
        request_obj = self.create_request(with_approvals=False)
        first = request_obj.create_user_approvals([self.project.pi, self.new_pi])
        second = request_obj.create_user_approvals([self.project.pi, self.new_pi])
        self.assertEqual([approval.pk for approval in first], [approval.pk for approval in second])
        self.assertEqual(request_obj.user_approvals.count(), 2)

    def test_create_user_approvals_auto_approves_initiator(self):
        request_obj = self.create_request(with_approvals=False)  # the project PI initiated this request
        request_obj.create_user_approvals([self.project.pi, self.new_pi])
        self.assertEqual(request_obj.user_approvals.get(user=self.project.pi).status.name, "Approved")
        self.assertEqual(request_obj.user_approvals.get(user=self.new_pi).status.name, "Pending")

    def test_create_user_approvals_leaves_both_pending_for_third_party_initiator(self):
        request_obj = self.create_request(with_approvals=False, initiator=self.outsider)
        request_obj.create_user_approvals([self.project.pi, self.new_pi])
        self.assertEqual(request_obj.user_approvals.get(user=self.project.pi).status.name, "Pending")
        self.assertEqual(request_obj.user_approvals.get(user=self.new_pi).status.name, "Pending")

    def test_update_status_flow_to_ready(self):
        request_obj = self.create_request(initiator=self.outsider)
        pi_approval = request_obj.user_approvals.get(user=self.project.pi)
        new_pi_approval = request_obj.user_approvals.get(user=self.new_pi)

        request_obj.update_status_from_approvals()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status.name, "Awaiting Approvals")

        resolve_user_approval(pi_approval, "Approved")
        request_obj.refresh_from_db()
        request_obj.update_status_from_approvals()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status.name, "Awaiting Approvals")

        resolve_user_approval(new_pi_approval, "Approved")
        request_obj.refresh_from_db()
        request_obj.update_status_from_approvals()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status.name, "Ready")

    def test_update_status_denied_is_blocked(self):
        request_obj = self.create_request(initiator=self.outsider)
        resolve_user_approval(request_obj.user_approvals.get(user=self.project.pi), "Denied")
        request_obj.update_status_from_approvals()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status.name, "Blocked")

    def test_update_status_skips_terminal_states(self):
        request_obj = self.create_request(status_name="Complete")
        request_obj.update_status_from_approvals()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status.name, "Complete")

    def test_cancel_pending_approvals(self):
        request_obj = self.create_request()
        resolve_user_approval(request_obj.user_approvals.get(user=self.project.pi), "Approved")
        request_obj.cancel_pending_approvals()
        self.assertEqual(request_obj.user_approvals.filter(status__name="Cancelled").count(), 1)
        self.assertEqual(request_obj.user_approvals.filter(status__name="Approved").count(), 1)

    def test_apply_pi_change(self):
        request_obj = self.create_request(status_name="Ready")
        request_obj.apply_pi_change()
        self.project.refresh_from_db()
        self.assertEqual(self.project.pi, self.new_pi)

    def test_apply_pi_change_keeps_outgoing_pi_as_active_manager(self):
        membership = ProjectUser.objects.get(project=self.project, user=self.project.pi)
        membership.role = ProjectUserRoleChoiceFactory(name="User")
        membership.status = ProjectUserStatusChoiceFactory(name="Removed")
        membership.save()

        request_obj = self.create_request(status_name="Ready")
        request_obj.apply_pi_change()

        membership.refresh_from_db()
        self.assertEqual(membership.role.name, "Manager")
        self.assertEqual(membership.status.name, "Active")

    def test_apply_pi_change_creates_missing_membership_for_outgoing_pi(self):
        ProjectUser.objects.filter(project=self.project, user=self.project.pi).delete()

        request_obj = self.create_request(status_name="Ready")
        request_obj.apply_pi_change()

        membership = ProjectUser.objects.get(project=self.project, user=request_obj.current_pi)
        self.assertEqual(membership.role.name, "Manager")
        self.assertEqual(membership.status.name, "Active")

    def test_is_ready_and_is_denyable(self):
        request_obj = self.create_request()
        self.assertFalse(request_obj.is_ready)
        self.assertTrue(request_obj.is_denyable)
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Ready")
        self.assertTrue(request_obj.is_ready)
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Complete")
        self.assertFalse(request_obj.is_denyable)

    def test_template_tags_report_active_request_and_user_approval(self):
        request_obj = self.create_request()
        self.assertEqual(active_pi_change_request(self.project), request_obj)
        self.assertIsNone(pi_change_user_approval(self.project, self.project_user.user))

        approval = pi_change_user_approval(self.project, self.new_pi)
        self.assertEqual(approval.request, request_obj)
        resolve_user_approval(approval, "Approved")
        self.assertEqual(pi_change_user_approval(self.project, self.new_pi).status.name, "Approved")

        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Rejected")
        request_obj.save()
        self.assertIsNone(active_pi_change_request(self.project))
        self.assertIsNone(pi_change_user_approval(self.project, self.new_pi))

    def test_full_name_with_username_filter(self):
        user = UserFactory()
        self.assertEqual(full_name_with_username(user), f"{user.get_full_name()} ({user.username})")

        user.first_name = ""
        user.last_name = ""
        self.assertEqual(full_name_with_username(user), user.username)

    def test_response_property_reports_approval_record(self):
        request_obj = self.create_request()
        pi_approval = request_obj.user_approvals.get(user=self.project.pi)
        new_pi_approval = request_obj.user_approvals.get(user=self.new_pi)

        # A pending approval has no response yet.
        self.assertIsNone(new_pi_approval.response)

        # The PI's approval was recorded as approved when the request was created.
        self.assertIsNotNone(pi_approval.response)
        self.assertEqual(pi_approval.response.status.name, "Approved")

        resolve_user_approval(new_pi_approval, "Denied")
        self.assertEqual(request_obj.user_approvals.get(user=self.new_pi).response.status.name, "Denied")


class CenterHistoryFilterTests(PiChangeRequestTestBase):
    """Only request creation and real status changes should appear in the combined history."""

    def test_only_creation_and_status_changes_are_kept(self):
        request_obj = self.create_request()
        request_obj.justification = "Unrelated edit"
        request_obj.save()
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Ready")
        request_obj.save()

        records = ProjectPiChangeRequest.history.filter(history_type__in=["+", "~"]).order_by("id", "history_id")
        kept = list(ProjectPiChangeRequestCenterView().get_status_changed_records(records))
        self.assertEqual([record.history_type for record in kept], ["+", "~"])
        self.assertEqual(kept[-1].status.name, "Ready")


@SILENT
class PiChangeRequestCreationViewTests(PiChangeRequestTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.url = reverse("pi-change-request", kwargs={"pk": cls.project.pk})

    def post_creation(self, user, new_pi):
        self.client.force_login(user)
        return self.client.post(self.url, {"new_pi": new_pi.pk, "justification": "PI is stepping down"})

    def test_access(self):
        utils.test_logged_out_redirect_to_login(self, self.url)
        utils.test_user_can_access(self, self.superuser, self.url)
        utils.test_user_can_access(self, self.project.pi, self.url)
        utils.test_user_can_access(self, self.new_pi, self.url)
        utils.test_user_cannot_access(self, self.project_user.user, self.url)
        utils.test_user_cannot_access(self, self.outsider, self.url)

    def test_form_lists_next_steps(self):
        self.client.force_login(self.project.pi)
        response = self.client.get(self.url)
        self.assertContains(response, self.project.title)
        self.assertContains(response, "What happens next?")
        self.assertContains(response, "recorded automatically when you submit")
        self.assertContains(response, "remains a manager of the project")

    def test_creates_request_with_approvals(self):
        self.set_requires_approval(self.resource, True)
        response = self.post_creation(self.project.pi, self.new_pi)
        self.assertRedirects(response, self.project.get_absolute_url())
        success_messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn("Project PI change request received.", success_messages)

        request_obj = ProjectPiChangeRequest.objects.get()
        self.assertEqual(request_obj.status.name, "New")
        self.assertEqual(request_obj.current_pi, self.project.pi)
        self.assertEqual(request_obj.initiator, self.project.pi)
        self.assertEqual(list(request_obj.resources.all()), [self.resource])
        self.assertEqual(
            set(request_obj.user_approvals.values_list("user_id", flat=True)), {self.project.pi.pk, self.new_pi.pk}
        )
        # the initiator's approval starts approved; only the new PI must respond
        self.assertEqual(request_obj.user_approvals.get(user=self.project.pi).status.name, "Approved")
        self.assertEqual(request_obj.user_approvals.filter(status__name="Pending").count(), 1)
        resource_approval = request_obj.resource_approvals.get()
        self.assertEqual(resource_approval.resource, self.resource)
        self.assertEqual(resource_approval.status.name, "Pending")

    def test_new_pi_initiated_request_auto_approves_new_pi_side(self):
        self.post_creation(self.new_pi, self.new_pi)

        request_obj = ProjectPiChangeRequest.objects.get()
        self.assertEqual(request_obj.initiator, self.new_pi)
        self.assertEqual(request_obj.user_approvals.get(user=self.new_pi).status.name, "Approved")
        self.assertEqual(request_obj.user_approvals.get(user=self.project.pi).status.name, "Pending")

    def test_third_party_manager_initiated_request_leaves_both_pending(self):
        third_manager = UserFactory()
        ProjectUserFactory(project=self.project, role=ProjectUserRoleChoiceFactory(name="Manager"), user=third_manager)
        self.post_creation(third_manager, self.new_pi)

        request_obj = ProjectPiChangeRequest.objects.get()
        self.assertEqual(request_obj.initiator, third_manager)
        self.assertEqual(request_obj.user_approvals.filter(status__name="Pending").count(), 2)

    def test_no_resource_approval_when_not_required(self):
        self.post_creation(self.project.pi, self.new_pi)
        self.assertEqual(ProjectPiChangeRequest.objects.get().resource_approvals.count(), 0)

    def test_duplicate_active_request_blocked(self):
        self.create_request()
        response = self.post_creation(self.project.pi, self.new_pi)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An active PI change request already exists for this project.")
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 1)

    def test_new_pi_must_be_project_manager(self):
        self.post_creation(self.project.pi, self.project_user.user)
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 0)

    def test_disallowed_project_status_rejected(self):
        self.project.status = ProjectStatusChoiceFactory(name="Archived")
        self.project.save()
        response = self.post_creation(self.project.pi, self.new_pi)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 0)


@SILENT
class PiChangeRequestUserResponseViewTests(PiChangeRequestTestBase):
    def setUp(self):
        # a third-party initiator leaves both approvals pending
        self.request_obj = self.create_request(initiator=self.outsider)
        self.pi_approval = self.request_obj.user_approvals.get(user=self.project.pi)
        self.new_pi_approval = self.request_obj.user_approvals.get(user=self.new_pi)
        self.pi_detail_url = reverse("pi-change-request-user", kwargs={"pk": self.pi_approval.pk})
        self.pi_approve_url = reverse("pi-change-request-user-approve", kwargs={"pk": self.pi_approval.pk})
        self.pi_deny_url = reverse("pi-change-request-user-deny", kwargs={"pk": self.pi_approval.pk})
        self.new_pi_approve_url = reverse("pi-change-request-user-approve", kwargs={"pk": self.new_pi_approval.pk})

    def test_detail_access(self):
        self.client.force_login(self.project.pi)
        response = self.client.get(self.pi_detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Back to Project")
        self.assertContains(response, "Justification")
        self.assertContains(response, self.request_obj.justification)
        self.assertContains(response, "user-response-form")
        self.assertEqual(response.context["help_email"], settings.EMAIL_TICKET_SYSTEM_ADDRESS)
        utils.test_user_can_access(self, self.superuser, self.pi_detail_url)
        utils.test_user_cannot_access(self, self.outsider, self.pi_detail_url)

    def test_initiator_sees_auto_approval_note(self):
        # On a PI-initiated request the PI's approval is recorded at submission time, so the
        # page explains that instead of claiming a manual response was made.
        request_obj = self.create_request()
        pi_approval = request_obj.user_approvals.get(user=self.project.pi)
        url = reverse("pi-change-request-user", kwargs={"pk": pi_approval.pk})

        self.client.force_login(self.project.pi)
        response = self.client.get(url)
        self.assertContains(response, "Your approval was recorded automatically when you submitted this request.")
        self.assertNotContains(response, "You have already responded")

    def test_approvals_take_request_to_ready(self):
        self.client.force_login(self.project.pi)
        response = self.client.post(self.pi_approve_url)
        self.assertRedirects(response, self.pi_detail_url)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Awaiting Approvals")

        self.client.force_login(self.new_pi)
        self.client.post(self.new_pi_approve_url)
        self.new_pi_approval.refresh_from_db()
        self.assertEqual(self.new_pi_approval.status.name, "Approved")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Ready")

    def test_decline_blocks_request_and_further_responses_rejected(self):
        self.client.force_login(self.project.pi)
        self.client.post(self.pi_deny_url)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Blocked")

        self.client.force_login(self.new_pi)
        response = self.client.post(self.new_pi_approve_url, follow=True)
        self.assertTrue(any("not accepting approvals" in message.message for message in response.context["messages"]))
        self.new_pi_approval.refresh_from_db()
        self.assertEqual(self.new_pi_approval.status.name, "Pending")
        # the closed request should not offer the still-pending user a way to respond
        self.assertFalse(response.context["can_respond"])

    def test_cannot_respond_twice(self):
        self.client.force_login(self.project.pi)
        self.client.post(self.pi_approve_url)
        response = self.client.post(self.pi_approve_url, follow=True)
        self.assertTrue(any("already responded" in message.message for message in response.context["messages"]))
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Awaiting Approvals")

    def test_cannot_respond_for_another_user(self):
        self.client.force_login(self.outsider)
        response = self.client.post(self.pi_approve_url)
        self.assertEqual(response.status_code, 403)

    def test_initiator_approval_starts_approved(self):
        """When the current PI initiates, the new PI's response takes the request straight to Ready."""
        request_obj = self.create_request()  # project PI initiated; their approval starts approved
        new_pi_approval = request_obj.user_approvals.get(user=self.new_pi)
        self.assertEqual(request_obj.user_approvals.get(user=self.project.pi).status.name, "Approved")

        self.client.force_login(self.new_pi)
        response = self.client.post(reverse("pi-change-request-user-approve", kwargs={"pk": new_pi_approval.pk}))
        self.assertRedirects(response, reverse("pi-change-request-user", kwargs={"pk": new_pi_approval.pk}))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status.name, "Ready")


@SILENT
class PiChangeRequestResourceApprovalViewTests(PiChangeRequestTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.review_group = Group.objects.create(name="Storage Reviewers")
        cls.review_group.permissions.add(get_permission(RESOURCE_APPROVAL_CHANGE_PERMISSION))
        cls.resource.review_groups.add(cls.review_group)
        cls.reviewer = UserFactory()
        cls.reviewer.groups.add(cls.review_group)
        cls.center_url = reverse("pi-change-request-center")

    def setUp(self):
        self.set_requires_approval(self.resource, True)
        self.request_obj = self.create_request()
        self.resource_approval = self.request_obj.resource_approvals.get()
        self.approve_url = reverse("pi-change-request-resource-approve", kwargs={"pk": self.resource_approval.pk})
        self.deny_url = reverse("pi-change-request-resource-deny", kwargs={"pk": self.resource_approval.pk})

    def test_review_group_member_can_approve(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(self.approve_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.center_url)
        self.resource_approval.refresh_from_db()
        self.assertEqual(self.resource_approval.status.name, "Approved")
        self.assertEqual(self.resource_approval.handler, self.reviewer)
        # the new PI's approval is still pending, so the request is not ready yet
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Awaiting Approvals")

    def test_review_group_member_can_deny_and_blocks_request(self):
        self.client.force_login(self.reviewer)
        self.client.post(self.deny_url)
        self.resource_approval.refresh_from_db()
        self.assertEqual(self.resource_approval.status.name, "Denied")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Blocked")

    def test_user_outside_review_group_cannot_respond(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.approve_url).status_code, 403)

    def test_review_group_without_permission_cannot_respond(self):
        unprivileged_group = Group.objects.create(name="Onlookers")
        self.resource.review_groups.add(unprivileged_group)
        user = UserFactory()
        user.groups.add(unprivileged_group)
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.approve_url).status_code, 403)

    def test_staff_can_respond_for_resource_without_review_groups(self):
        open_resource = ResourceFactory(name="storage/open")
        self.set_requires_approval(open_resource, True)
        request_obj = self.create_request(resources=[open_resource])
        approval = request_obj.resource_approvals.get()

        staff_user = UserFactory(is_staff=True)
        self.client.force_login(staff_user)
        response = self.client.post(reverse("pi-change-request-resource-approve", kwargs={"pk": approval.pk}))
        self.assertEqual(response.status_code, 302)
        approval.refresh_from_db()
        self.assertEqual(approval.status.name, "Approved")

    def test_grouped_user_cannot_respond_for_resource_without_review_groups(self):
        open_resource = ResourceFactory(name="storage/open")
        self.set_requires_approval(open_resource, True)
        request_obj = self.create_request(resources=[open_resource])
        approval = request_obj.resource_approvals.get()

        grouped_user = UserFactory()
        grouped_user.groups.add(Group.objects.create(name="Unrelated"))
        self.client.force_login(grouped_user)
        response = self.client.post(reverse("pi-change-request-resource-approve", kwargs={"pk": approval.pk}))
        self.assertEqual(response.status_code, 403)
        approval.refresh_from_db()
        self.assertEqual(approval.status.name, "Pending")


@SILENT
class PiChangeRequestActivationViewTests(PiChangeRequestTestBase):
    def setUp(self):
        # a third-party initiator leaves both approvals pending, so cancellation counts hold
        self.request_obj = self.create_request(initiator=self.outsider)
        self.activate_url = reverse("pi-change-request-approval", kwargs={"pk": self.request_obj.pk})
        self.deny_url = reverse("pi-change-request-denial", kwargs={"pk": self.request_obj.pk})

    def mark_ready(self):
        self.request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Ready")
        self.request_obj.save()

    def test_activate_requires_permission(self):
        self.mark_ready()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.activate_url).status_code, 403)

    def test_activate_requires_ready(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.activate_url)
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "New")
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.pi, self.new_pi)

    def test_activate_swaps_pi_and_cancels_pending_approvals(self):
        self.mark_ready()
        self.client.force_login(self.superuser)
        response = self.client.post(self.activate_url)
        self.assertRedirects(response, reverse("pi-change-request-center"))

        self.project.refresh_from_db()
        self.assertEqual(self.project.pi, self.new_pi)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Complete")
        self.assertEqual(self.request_obj.user_approvals.filter(status__name="Cancelled").count(), 2)

    def test_activate_requires_new_pi_to_stay_manager(self):
        self.mark_ready()
        membership = ProjectUser.objects.get(project=self.project, user=self.new_pi)
        membership.status = ProjectUserStatusChoiceFactory(name="Inactive")
        membership.save()

        self.client.force_login(self.superuser)
        response = self.client.post(self.activate_url)
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.pi, self.new_pi)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Ready")

    def test_second_activate_is_blocked(self):
        self.mark_ready()
        self.client.force_login(self.superuser)
        self.client.post(self.activate_url)
        response = self.client.post(self.activate_url, follow=True)
        self.assertTrue(any("Cannot approve" in message.message for message in response.context["messages"]))
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Complete")

    def test_get_redirects_without_side_effects(self):
        self.mark_ready()
        self.client.force_login(self.superuser)
        response = self.client.get(self.activate_url)
        self.assertRedirects(response, reverse("pi-change-request-center"))
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.pi, self.new_pi)

    def test_denial_rejects_and_cancels_pending(self):
        self.client.force_login(self.superuser)
        self.client.post(self.deny_url)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status.name, "Rejected")
        self.assertEqual(self.request_obj.user_approvals.filter(status__name="Cancelled").count(), 2)
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.pi, self.new_pi)

    def test_cancelled_approval_page_offers_no_action(self):
        self.client.force_login(self.superuser)
        self.client.post(self.deny_url)
        cancelled_approval = self.request_obj.user_approvals.get(user=self.new_pi)
        response = self.client.get(reverse("pi-change-request-user", kwargs={"pk": cancelled_approval.pk}))
        self.assertContains(response, "This request closed before a response was submitted")
        self.assertNotContains(response, "Action Required")

    def test_deny_after_complete_is_blocked(self):
        self.mark_ready()
        self.client.force_login(self.superuser)
        self.client.post(self.activate_url)
        response = self.client.post(self.deny_url, follow=True)
        self.assertTrue(any("Cannot deny" in message.message for message in response.context["messages"]))


@SILENT
class PiChangeRequestCenterViewTests(PiChangeRequestTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.center_url = reverse("pi-change-request-center")
        cls.view_permission = get_permission(PI_CHANGE_REQUEST_VIEW_PERMISSION)

    def setUp(self):
        self.set_requires_approval(self.resource, True)
        self.request_obj = self.create_request()

    def test_access(self):
        utils.test_logged_out_redirect_to_login(self, self.center_url)
        utils.test_user_cannot_access(self, self.outsider, self.center_url)
        utils.test_user_can_access(self, self.superuser, self.center_url)

    def test_viewer_permission_grants_access(self):
        viewer = UserFactory()
        viewer.user_permissions.add(self.view_permission)
        utils.test_user_can_access(self, viewer, self.center_url)

    def test_superuser_context(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.center_url)
        self.assertEqual(response.context["pending_pi_change_requests"].count(), 1)
        self.assertEqual(response.context["pending_resource_approvals"].count(), 1)
        self.assertTrue(any("created" in entry["description"] for entry in response.context["history"]))

    def test_staff_only_sees_resource_approvals_they_manage(self):
        reviewer = UserFactory()
        reviewer.user_permissions.add(self.view_permission)
        self.client.force_login(reviewer)
        self.assertEqual(self.client.get(self.center_url).context["pending_resource_approvals"].count(), 0)

        review_group = Group.objects.create(name="Storage Reviewers")
        review_group.permissions.add(get_permission(RESOURCE_APPROVAL_CHANGE_PERMISSION))
        self.resource.review_groups.add(review_group)
        reviewer.groups.add(review_group)
        self.assertEqual(self.client.get(self.center_url).context["pending_resource_approvals"].count(), 1)

    def test_resource_without_review_groups_is_actionable_by_staff_only(self):
        """Resources without review groups are open to staff users, not to other grouped users."""
        reviewer = UserFactory()
        reviewer.user_permissions.add(self.view_permission)
        reviewer.groups.add(Group.objects.create(name="Unrelated"))
        self.client.force_login(reviewer)
        self.assertEqual(self.client.get(self.center_url).context["pending_resource_approvals"].count(), 0)

        staff_viewer = UserFactory(is_staff=True)
        staff_viewer.user_permissions.add(self.view_permission)
        self.client.force_login(staff_viewer)
        self.assertEqual(self.client.get(self.center_url).context["pending_resource_approvals"].count(), 1)

    def test_requests_table_links_project_and_renders_status_badges(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.center_url)
        self.assertContains(response, f"{self.project.title} ({self.project.pk})")
        self.assertContains(response, 'badge bg-secondary">New</span>')
        self.assertNotContains(response, "Project ID")

    def test_history_without_user_renders_em_dash(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.center_url)
        self.assertContains(response, ">—</td>")
        self.assertNotContains(response, "&amp;mdash;")

    def test_action_buttons_request_confirmation(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.center_url)
        self.assertContains(response, 'data-confirm="Are you sure you want to deny this PI change request?"')
        self.assertContains(
            response, 'data-confirm="Are you sure you want to approve this resource for this PI change request?"'
        )
        self.assertContains(response, 'data-confirm="Are you sure you want to deny this resource?')
        self.assertNotContains(response, 'data-confirm="Are you sure you want to activate')

    def test_requests_table_displays_full_names(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.center_url)
        for user in [self.request_obj.initiator, self.project.pi, self.new_pi]:
            with self.subTest(user=user.username):
                self.assertContains(response, f"{user.get_full_name()} ({user.username})")

    def test_action_buttons_are_real_forms(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.center_url)
        deny_url = reverse("pi-change-request-denial", kwargs={"pk": self.request_obj.pk})
        self.assertContains(response, f'action="{deny_url}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(response, "post-link")


@SILENT
class PiChangeRequestDetailViewTests(PiChangeRequestTestBase):
    """The detail page lists a request's approvals and gates the admin actions on status and permission."""

    def setUp(self):
        self.request_obj = self.create_request()
        self.detail_url = reverse("pi-change-request-details", kwargs={"pk": self.request_obj.pk})

    def mark_request_ready(self):
        self.request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Ready")
        self.request_obj.save()

    def test_access(self):
        utils.test_logged_out_redirect_to_login(self, self.detail_url)
        utils.test_user_cannot_access(self, self.outsider, self.detail_url)
        utils.test_user_can_access(self, self.superuser, self.detail_url)

    def test_viewer_permission_grants_access(self):
        viewer = UserFactory()
        viewer.user_permissions.add(get_permission(PI_CHANGE_REQUEST_VIEW_PERMISSION))
        utils.test_user_can_access(self, viewer, self.detail_url)

    def test_missing_request_returns_404(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("pi-change-request-details", kwargs={"pk": self.request_obj.pk + 100}))
        self.assertEqual(response.status_code, 404)

    def test_page_lists_request_details_and_user_approvals(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.detail_url)
        self.assertContains(response, self.project.title)
        self.assertContains(response, self.project.pi.username)
        self.assertContains(response, self.new_pi.username)
        self.assertContains(response, self.request_obj.justification)
        self.assertContains(response, "Justification")
        self.assertContains(response, ">Users</h3>")
        self.assertContains(response, ">Pending</span>")
        self.assertNotContains(response, "No additional approvals required!")

    def test_page_lists_resource_approvals(self):
        self.set_requires_approval(self.resource, True)
        request_obj = self.create_request()
        url = reverse("pi-change-request-details", kwargs={"pk": request_obj.pk})

        self.client.force_login(self.superuser)
        response = self.client.get(url)
        self.assertContains(response, ">Resources</h3>")
        self.assertContains(response, self.resource.name)

    def test_action_buttons_follow_status(self):
        self.client.force_login(self.superuser)

        # New: denyable, but not yet ready.
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, ">Activate</button>")
        self.assertContains(response, ">Deny</button>")

        # Ready: both actions available.
        self.mark_request_ready()
        response = self.client.get(self.detail_url)
        self.assertContains(response, ">Activate</button>")
        self.assertContains(response, ">Deny</button>")

        # Complete: terminal, neither action available.
        self.request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Complete")
        self.request_obj.save()
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, ">Activate</button>")
        self.assertNotContains(response, ">Deny</button>")

    def test_page_lists_response_dates(self):
        # The new PI approves through the response view, recording them as the handler; the
        # PI's approval was auto-recorded at creation (no user in thread), so it falls back
        # to the em dash.
        new_pi_approval = self.request_obj.user_approvals.get(user=self.new_pi)
        self.client.force_login(self.new_pi)
        self.client.post(reverse("pi-change-request-user-approve", kwargs={"pk": new_pi_approval.pk}))

        self.client.force_login(self.superuser)
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Responded by")
        self.assertContains(response, f"Responded by {self.new_pi.username}")
        self.assertContains(response, "Responded by —")

    def test_page_lists_submitted_date(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Submitted")

    def test_action_buttons_request_confirmation(self):
        self.client.force_login(self.superuser)

        # New: only Deny is offered, and it asks for confirmation.
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'data-confirm="Are you sure you want to deny this PI change request?"')
        self.assertNotContains(response, 'data-confirm="Are you sure you want to activate')

        # Ready: Activate is offered with its own confirmation.
        self.mark_request_ready()
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'data-confirm="Are you sure you want to activate')

    def test_viewers_see_no_action_buttons(self):
        viewer = UserFactory()
        viewer.user_permissions.add(get_permission(PI_CHANGE_REQUEST_VIEW_PERMISSION))
        self.client.force_login(viewer)
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, ">Activate</button>")
        self.assertNotContains(response, ">Deny</button>")


@SILENT
class ResourceApprovalSettingViewTests(PiChangeRequestTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.url = reverse("update-resource-approval")

    def setUp(self):
        self.setting = ProjectPiChangeRequestResourceApprovalSetting.objects.get(resource=self.resource)

    def post_toggle(self, checked):
        return self.client.post(self.url, {"resource_approval_id": self.setting.pk, "checked": checked})

    def test_superuser_can_toggle(self):
        self.client.force_login(self.superuser)
        response = self.post_toggle("true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "checked")
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.requires_approval)

        response = self.post_toggle("false")
        self.assertEqual(response.content.decode(), "unchecked")
        self.setting.refresh_from_db()
        self.assertFalse(self.setting.requires_approval)

    def test_user_without_group_cannot_toggle(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.post_toggle("true").status_code, 403)

    def test_review_group_member_with_permission_can_toggle(self):
        review_group = Group.objects.create(name="Settings Reviewers")
        review_group.permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        self.resource.review_groups.add(review_group)
        member = UserFactory()
        member.groups.add(review_group)

        self.client.force_login(member)
        self.assertEqual(self.post_toggle("true").status_code, 200)
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.requires_approval)

    def test_staff_permission_holder_can_toggle_resource_without_review_groups(self):
        staff_holder = UserFactory(is_staff=True)
        staff_holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))

        self.client.force_login(staff_holder)
        self.assertEqual(self.post_toggle("true").status_code, 200)
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.requires_approval)

    def test_grouped_permission_holder_cannot_toggle_resource_without_review_groups(self):
        holder = UserFactory()
        holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        holder.groups.add(Group.objects.create(name="Unrelated"))

        self.client.force_login(holder)
        self.assertEqual(self.post_toggle("true").status_code, 403)
        self.setting.refresh_from_db()
        self.assertFalse(self.setting.requires_approval)

    def test_get_not_allowed(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_invalid_checked_value_leaves_setting_unchanged(self):
        self.client.force_login(self.superuser)
        self.post_toggle("true")
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.requires_approval)

        response = self.client.post(self.url, {"resource_approval_id": self.setting.pk, "checked": "yes"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url, {"resource_approval_id": self.setting.pk})
        self.assertEqual(response.status_code, 400)
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.requires_approval)


@SILENT
class ResourceApprovalSettingsViewTests(PiChangeRequestTestBase):
    """The settings page lists every resource approval setting, with toggles only where the user may edit."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.url = reverse("pi-change-request-settings")
        cls.setting = ProjectPiChangeRequestResourceApprovalSetting.objects.get(resource=cls.resource)

    def test_access(self):
        utils.test_logged_out_redirect_to_login(self, self.url)
        utils.test_user_cannot_access(self, self.outsider, self.url)
        utils.test_user_can_access(self, self.superuser, self.url)

    def test_permission_holder_can_access(self):
        holder = UserFactory()
        holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        utils.test_user_can_access(self, holder, self.url)

    def test_superuser_sees_a_toggle_per_row(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertContains(response, self.resource.name)
        self.assertContains(response, f'data-pk="{self.setting.pk}"')
        self.assertContains(response, "form-check-input requires-approval-checkbox")

    def test_review_group_member_with_permission_sees_toggles(self):
        holder = UserFactory()
        holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        review_group = Group.objects.create(name="Storage Reviewers")
        review_group.permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        self.resource.review_groups.add(review_group)
        holder.groups.add(review_group)

        self.client.force_login(holder)
        response = self.client.get(self.url)
        self.assertContains(response, f'data-pk="{self.setting.pk}"')

    def test_user_outside_review_groups_sees_static_values(self):
        holder = UserFactory()
        holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        self.resource.review_groups.add(Group.objects.create(name="Storage Reviewers"))

        self.client.force_login(holder)
        response = self.client.get(self.url)
        self.assertNotContains(response, "form-check-input requires-approval-checkbox")
        self.assertContains(response, "badge bg-secondary")

    def test_staff_sees_toggles_for_resources_without_review_groups(self):
        staff_holder = UserFactory(is_staff=True)
        staff_holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))

        self.client.force_login(staff_holder)
        response = self.client.get(self.url)
        self.assertContains(response, f'data-pk="{self.setting.pk}"')

    def test_grouped_permission_holder_sees_static_values_for_resources_without_review_groups(self):
        holder = UserFactory()
        holder.user_permissions.add(get_permission(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION))
        holder.groups.add(Group.objects.create(name="Unrelated"))

        self.client.force_login(holder)
        response = self.client.get(self.url)
        self.assertNotContains(response, f'data-pk="{self.setting.pk}"')


@SILENT
class PiChangeRequestAdminTests(PiChangeRequestTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.add_url = reverse("admin:pi_change_request_projectpichangerequest_add")

    def post_add(self, new_pi):
        self.client.force_login(self.superuser)
        return self.client.post(
            self.add_url,
            {"project": self.project.pk, "new_pi": new_pi.pk, "justification": "Admin initiated"},
        )

    def test_add_derives_fields_and_creates_approvals(self):
        self.set_requires_approval(self.resource, True)
        response = self.post_add(self.new_pi)
        self.assertRedirects(response, reverse("admin:pi_change_request_projectpichangerequest_changelist"))

        request_obj = ProjectPiChangeRequest.objects.get()
        self.assertEqual(request_obj.current_pi, self.project.pi)
        self.assertEqual(request_obj.initiator, self.superuser)
        self.assertEqual(request_obj.status.name, "New")
        self.assertEqual(list(request_obj.resources.all()), [self.resource])
        self.assertEqual(request_obj.user_approvals.count(), 2)
        # the admin is not an approval party, so both approvals stay pending
        self.assertEqual(request_obj.user_approvals.filter(status__name="Pending").count(), 2)
        self.assertEqual(request_obj.resource_approvals.count(), 1)

    def test_add_blocks_duplicate_active_request(self):
        self.create_request()
        response = self.post_add(self.new_pi)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An active PI change request already exists")
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 1)

    def test_add_rejects_new_pi_matching_current_pi(self):
        response = self.post_add(self.project.pi)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The new PI must be different from the current PI.")
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 0)

    def test_changelist_renders(self):
        self.create_request()
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:pi_change_request_projectpichangerequest_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.project.title)
        self.assertContains(response, "New")

    def test_change_page_renders(self):
        request_obj = self.create_request()
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse("admin:pi_change_request_projectpichangerequest_change", args=(request_obj.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, request_obj.justification)

    def test_resource_approval_change_page_renders(self):
        self.set_requires_approval(self.resource, True)
        request_obj = self.create_request()
        approval = request_obj.resource_approvals.get()

        self.client.force_login(self.superuser)
        url = reverse("admin:pi_change_request_projectpichangerequestresourceapproval_change", args=(approval.pk,))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.resource.name)

    @override_settings(EMAIL_ENABLED=True, SLACK_MESSAGING_ENABLED=False)
    def test_add_sends_no_notifications(self):
        mail.outbox = []
        self.post_add(self.new_pi)
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_ENABLED=True)
class SendEmailTests(TestCase):
    """The plugin's send_email wrapper normalizes receivers for core's email helpers."""

    def test_iterable_receivers_are_normalized_to_a_list(self):
        receivers = {"a@example.com", "b@example.com"}
        with mock.patch("coldfront.plugins.pi_change_request.utils.send_email_template") as mock_send:
            send_email("Subject", "pi_change_request/email/pi_change_request_blocked.txt", {}, receivers)

        normalized = mock_send.call_args[0][3]
        self.assertIsInstance(normalized, list)
        self.assertEqual(sorted(normalized), ["a@example.com", "b@example.com"])


@override_settings(EMAIL_ENABLED=True, SLACK_MESSAGING_ENABLED=False)
class PiChangeRequestEmailTests(PiChangeRequestTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_url = reverse("pi-change-request", kwargs={"pk": cls.project.pk})

    def setUp(self):
        mail.outbox = []

    def test_creation_emails(self):
        self.set_requires_approval(self.resource, True)
        queue_group = Group.objects.create(name="Ticket Queue")
        queue_group.permissions.add(get_permission(RESOURCE_APPROVAL_CHANGE_PERMISSION))
        self.resource.review_groups.add(queue_group)
        ProjectPiChangeRequestReviewGroupTicketEmail.objects.create(group=queue_group, email="queue@example.com")

        self.client.force_login(self.project.pi)
        self.client.post(self.create_url, {"new_pi": self.new_pi.pk, "justification": "PI is stepping down"})

        recipients = [address for message in mail.outbox for address in message.to]
        self.assertIn(self.new_pi.email, recipients)
        # the initiator's approval starts approved, so they get no action-required email
        self.assertNotIn(self.project.pi.email, recipients)
        self.assertIn("queue@example.com", recipients)
        self.assertIn(settings.EMAIL_TICKET_SYSTEM_ADDRESS, recipients)

    def test_ready_email_sent_when_request_becomes_ready(self):
        request_obj = self.create_request()  # project PI initiated; their approval starts approved
        new_pi_approval = request_obj.user_approvals.get(user=self.new_pi)

        self.client.force_login(self.new_pi)
        self.client.post(reverse("pi-change-request-user-approve", kwargs={"pk": new_pi_approval.pk}))

        # send_email_template prefixes the center name, so match on the subject suffix.
        subjects = [message.subject for message in mail.outbox]
        self.assertTrue(any("PI Change Request Ready for Activation" in subject for subject in subjects))

    def test_blocked_email_links_to_the_project(self):
        request_obj = self.create_request()  # project PI initiated; their approval starts approved
        new_pi_approval = request_obj.user_approvals.get(user=self.new_pi)

        self.client.force_login(self.new_pi)
        self.client.post(reverse("pi-change-request-user-deny", kwargs={"pk": new_pi_approval.pk}))

        blocked_messages = [message for message in mail.outbox if "Was Blocked" in message.subject]
        self.assertEqual(len(blocked_messages), 1)
        self.assertIn(reverse("project-detail", kwargs={"pk": self.project.pk}), blocked_messages[0].body)
        self.assertEqual(set(blocked_messages[0].to), {self.project.pi.email, self.new_pi.email})

    def test_approval_email_goes_to_participants(self):
        request_obj = self.create_request()
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Ready")
        request_obj.save()

        self.client.force_login(self.superuser)
        self.client.post(reverse("pi-change-request-approval", kwargs={"pk": request_obj.pk}))

        approved_messages = [message for message in mail.outbox if "Was Approved" in message.subject]
        self.assertEqual(len(approved_messages), 1)
        # current_pi and initiator are the same user, so the recipient list deduplicates them.
        self.assertEqual(len(approved_messages[0].to), 2)
        self.assertEqual(set(approved_messages[0].to), {self.project.pi.email, self.new_pi.email})

    def test_denial_email_goes_to_participants(self):
        request_obj = self.create_request()

        self.client.force_login(self.superuser)
        self.client.post(reverse("pi-change-request-denial", kwargs={"pk": request_obj.pk}))

        denied_messages = [message for message in mail.outbox if "Was Denied" in message.subject]
        self.assertEqual(len(denied_messages), 1)
        self.assertEqual(len(denied_messages[0].to), 2)
        self.assertEqual(set(denied_messages[0].to), {self.project.pi.email, self.new_pi.email})

    def test_resource_approval_emails_are_grouped_by_queue(self):
        self.set_requires_approval(self.resource, True)
        second_resource = ResourceFactory(name="storage/b")
        self.set_requires_approval(second_resource, True)
        self.allocation.resources.add(second_resource)

        queue_group = Group.objects.create(name="Ticket Queue")
        queue_group.permissions.add(get_permission(RESOURCE_APPROVAL_CHANGE_PERMISSION))
        self.resource.review_groups.add(queue_group)
        second_resource.review_groups.add(queue_group)
        ProjectPiChangeRequestReviewGroupTicketEmail.objects.create(group=queue_group, email="queue@example.com")

        self.client.force_login(self.project.pi)
        self.client.post(self.create_url, {"new_pi": self.new_pi.pk, "justification": "PI is stepping down"})

        queue_messages = [message for message in mail.outbox if "queue@example.com" in message.to]
        self.assertEqual(len(queue_messages), 1)
        self.assertIn(self.resource.name, queue_messages[0].body)
        self.assertIn(second_resource.name, queue_messages[0].body)
