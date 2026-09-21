import logging

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.exceptions import ValidationError
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
    ProjectPiChangeRequestResourceApprovalSetting,
    ProjectPiChangeRequestReviewGroupTicketEmail,
    ProjectPiChangeRequestStatusChoice,
    ProjectPiChangeRequestUserApprovalStatusChoice,
)
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

    def create_request(self, status_name="New", resources=None, with_approvals=True):
        """Create a request directly, bypassing form validation like the seeded state would."""
        request_obj = ProjectPiChangeRequest.objects.create(
            project=self.project,
            current_pi=self.project.pi,
            new_pi=self.new_pi,
            initiator=self.project.pi,
            justification="Test justification",
            status=ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key(status_name),
        )
        request_obj.resources.set(resources if resources is not None else [self.resource])
        if with_approvals:
            request_obj.create_resource_approvals()
            request_obj.create_user_approvals([self.project.pi, self.new_pi])
        return request_obj


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

    def test_create_resource_approvals_only_for_required_resources(self):
        self.set_requires_approval(self.resource, True)
        request_obj = self.create_request(with_approvals=False)
        approvals = request_obj.create_resource_approvals()
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].resource, self.resource)
        self.assertEqual(approvals[0].status.name, "Pending")

    def test_create_user_approvals_dedupes(self):
        request_obj = self.create_request(with_approvals=False)
        first = request_obj.create_user_approvals([self.project.pi, self.new_pi])
        second = request_obj.create_user_approvals([self.project.pi, self.new_pi])
        self.assertEqual([approval.pk for approval in first], [approval.pk for approval in second])
        self.assertEqual(request_obj.user_approvals.count(), 2)

    def test_update_status_flow_to_ready(self):
        request_obj = self.create_request()
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
        request_obj = self.create_request()
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

    def test_is_ready_and_is_denyable(self):
        request_obj = self.create_request()
        self.assertFalse(request_obj.is_ready)
        self.assertTrue(request_obj.is_denyable)
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Ready")
        self.assertTrue(request_obj.is_ready)
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Complete")
        self.assertFalse(request_obj.is_denyable)


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

    def test_creates_request_with_approvals(self):
        self.set_requires_approval(self.resource, True)
        response = self.post_creation(self.project.pi, self.new_pi)
        self.assertRedirects(response, self.project.get_absolute_url())

        request_obj = ProjectPiChangeRequest.objects.get()
        self.assertEqual(request_obj.status.name, "New")
        self.assertEqual(request_obj.current_pi, self.project.pi)
        self.assertEqual(request_obj.initiator, self.project.pi)
        self.assertEqual(list(request_obj.resources.all()), [self.resource])
        self.assertEqual(
            set(request_obj.user_approvals.values_list("user_id", flat=True)), {self.project.pi.pk, self.new_pi.pk}
        )
        self.assertEqual(request_obj.user_approvals.filter(status__name="Pending").count(), 2)
        resource_approval = request_obj.resource_approvals.get()
        self.assertEqual(resource_approval.resource, self.resource)
        self.assertEqual(resource_approval.status.name, "Pending")

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


@SILENT
class PiChangeRequestUserResponseViewTests(PiChangeRequestTestBase):
    def setUp(self):
        self.request_obj = self.create_request()
        self.pi_approval = self.request_obj.user_approvals.get(user=self.project.pi)
        self.new_pi_approval = self.request_obj.user_approvals.get(user=self.new_pi)
        self.pi_detail_url = reverse("pi-change-request-user", kwargs={"pk": self.pi_approval.pk})
        self.pi_approve_url = reverse("pi-change-request-user-approve", kwargs={"pk": self.pi_approval.pk})
        self.pi_deny_url = reverse("pi-change-request-user-deny", kwargs={"pk": self.pi_approval.pk})
        self.new_pi_approve_url = reverse("pi-change-request-user-approve", kwargs={"pk": self.new_pi_approval.pk})

    def test_detail_access(self):
        utils.test_user_can_access(self, self.project.pi, self.pi_detail_url)
        utils.test_user_can_access(self, self.superuser, self.pi_detail_url)
        utils.test_user_cannot_access(self, self.outsider, self.pi_detail_url)

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
        # user approvals are still pending, so the request is not ready yet
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


@SILENT
class PiChangeRequestActivationViewTests(PiChangeRequestTestBase):
    def setUp(self):
        self.request_obj = self.create_request()
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
        self.assertEqual(request_obj.resource_approvals.count(), 1)

    def test_add_blocks_duplicate_active_request(self):
        self.create_request()
        response = self.post_add(self.new_pi)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An active PI change request already exists")
        self.assertEqual(ProjectPiChangeRequest.objects.count(), 1)


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
        self.assertIn(self.project.pi.email, recipients)
        self.assertIn(self.new_pi.email, recipients)
        self.assertIn("queue@example.com", recipients)
        self.assertIn(settings.EMAIL_TICKET_SYSTEM_ADDRESS, recipients)

    def test_ready_email_sent_when_request_becomes_ready(self):
        request_obj = self.create_request()
        pi_approval = request_obj.user_approvals.get(user=self.project.pi)
        new_pi_approval = request_obj.user_approvals.get(user=self.new_pi)

        self.client.force_login(self.project.pi)
        self.client.post(reverse("pi-change-request-user-approve", kwargs={"pk": pi_approval.pk}))
        self.client.force_login(self.new_pi)
        self.client.post(reverse("pi-change-request-user-approve", kwargs={"pk": new_pi_approval.pk}))

        # send_email_template prefixes the center name, so match on the subject suffix.
        subjects = [message.subject for message in mail.outbox]
        self.assertTrue(any("PI Change Request Ready for Activation" in subject for subject in subjects))
