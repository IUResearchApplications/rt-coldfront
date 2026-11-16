from unittest import mock

from coldfront_custom_resources.rules import check_user_has_account
from django.test import TestCase
from django.urls import reverse

from coldfront.core.resource.models import Resource
from coldfront.core.test_helpers.factories import ProjectFactory, ProjectUserFactory, ResourceFactory, UserFactory
from coldfront.plugins.customizable_forms.utils import standardize_resource_name


class AllocationResourceSelectionRuleRenderingTestCase(TestCase):
    """The resource-selection page renders failing rules' titles escaped and link_url as the href."""

    @classmethod
    def setUpTestData(cls) -> None:
        super().setUpTestData()
        cls.user = UserFactory(username="rule_render_pi")
        cls.project = ProjectFactory(pi=cls.user, status__name="Active")
        # The selection view requires ProjectPermission.UPDATE, which user_permissions grants via
        # the Manager role on an active ProjectUser row.
        ProjectUserFactory(project=cls.project, user=cls.user, role__name="Manager", status__name="Active")
        cls.resource = ResourceFactory(
            name="Rule Render Resource", is_allocatable=True, is_available=True, is_public=True
        )

    def setUp(self) -> None:
        super().setUp()
        self.client.force_login(self.user)

    def _get_selection_page(self, rule):
        """GET the selection page with the given rule registered for the test resource.

        The view reads its configuration from module-level dicts populated at app ready, so the
        patches swap in one rule for the test resource and no persistence functions (which could
        reach LDAP).
        """
        with (
            mock.patch(
                "coldfront.plugins.customizable_forms.views.CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS",
                {self.resource.name: {"view": "coldfront_custom_resources.views.GenericView", "info_url": ""}},
            ),
            mock.patch.dict(
                "coldfront.plugins.customizable_forms.utils.rule_functions",
                {standardize_resource_name(self.resource.name): [rule]},
                clear=True,
            ),
            mock.patch.dict(
                "coldfront.plugins.customizable_forms.utils.persistence_functions",
                {},
                clear=True,
            ),
        ):
            return self.client.get(reverse("custom-allocation-create", kwargs={"project_pk": self.project.pk}))

    def test_failing_rule_title_renders_escaped(self):
        """Rule titles are escaped, not trusted as raw HTML."""

        def rule(resource_obj, persistent_values):
            return {"passed": False, "title": "<b>Injected</b>", "description": "blocked"}

        response = self._get_selection_page(rule)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<b>Injected</b>")
        self.assertContains(response, "&lt;b&gt;Injected&lt;/b&gt;")

    def test_failing_rule_link_url_renders_as_anchor(self):
        """A failing rule's link_url becomes the anchor href wrapping the plain title."""

        def rule(resource_obj, persistent_values):
            return {
                "passed": False,
                "title": "Missing Account",
                "description": "blocked",
                "link_url": "https://example.com/accounts/create",
            }

        response = self._get_selection_page(rule)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<a href="https://example.com/accounts/create" target="_blank" rel="noopener noreferrer">',
        )
        self.assertContains(response, ">Missing Account</a>")

    def test_missing_account_rule_renders_creation_link(self):
        """The real missing-account rule renders the plain title wrapped in the creation-page anchor."""

        with mock.patch.object(Resource, "get_user_account_statuses", return_value={}):
            response = self._get_selection_page(check_user_has_account)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="https://access.iu.edu/Accounts/Create"')
        self.assertContains(response, ">Missing Account</a>")
