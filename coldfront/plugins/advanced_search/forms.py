from crispy_forms.bootstrap import Accordion, AccordionGroup, FormActions, InlineRadios
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Div, Fieldset, Layout, LayoutObject, Reset, Row, Submit
from django import forms
from django.template.loader import render_to_string

from coldfront.core.allocation.models import AllocationAttributeType, AllocationStatusChoice
from coldfront.core.project.models import ProjectAttributeType, ProjectStatusChoice, ProjectTypeChoice
from coldfront.core.resource.models import Resource, ResourceType


class AttributeFormSetHelper(FormHelper):
    """Helper for rendering attribute formsets."""

    def __init__(self, attribute_type, include_usage=True, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_custom_control = False
        layout_elements = [
            Row(Column("attribute__name"), Column("attribute__value")),
        ]

        if include_usage:
            layout_elements.append(
                Row(
                    Column("attribute__has_usage"),
                    Column("attribute__equality"),
                    Column("attribute__usage"),
                    Column("attribute__usage_format"),
                    css_id=f"{attribute_type}-usage-row",
                    css_class="d-none",
                )
            )

        self.layout = Layout(
            Div(
                Div(
                    *layout_elements,
                    css_class="card-body",
                    css_id=f"{attribute_type}-attribute-row",
                ),
                css_class="card mb-3",
            )
        )


class BaseAttributeSearchForm(forms.Form):
    """Base form for attribute search."""

    prefix = ""
    EQUALITY_CHOICES = (("lt", "<"), ("gt", ">"))
    FORMAT_CHOICES = (("whole", ".00"), ("percent", "%"))
    YES_NO_CHOICES = ((1, "Yes"), (0, "No"))

    attribute__name = forms.ModelChoiceField(queryset=None, required=False)
    attribute__value = forms.CharField(max_length=50, required=False)
    attribute__has_usage = forms.ChoiceField(initial=0, choices=YES_NO_CHOICES, required=False)
    attribute__equality = forms.ChoiceField(label="Equality", choices=EQUALITY_CHOICES, required=False)
    attribute__usage = forms.FloatField(label="Usage", required=False)
    attribute__usage_format = forms.ChoiceField(label="Format", choices=FORMAT_CHOICES, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_attribute_field_help_text()

    def setup_attribute_field_help_text(self):
        """Set help text for attribute field."""
        if "attribute__name" in self.fields:
            self.fields["attribute__name"].help_text = "Select an attribute to search by."


class AllocationAttributeSearchForm(BaseAttributeSearchForm):
    """Search form for allocation attributes with resource-based filtering."""

    def __init__(self, *args, resources=None, attribute_type_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_queryset(resources, attribute_type_queryset)

    def setup_queryset(self, resources, attribute_type_queryset):
        """Setup the attribute name queryset."""
        if attribute_type_queryset is not None:
            queryset = attribute_type_queryset
        elif resources:
            queryset = (
                AllocationAttributeType.objects.select_related("attribute_type")
                .prefetch_related("linked_resources")
                .filter(linked_resources__in=resources)
                .distinct()
                .order_by("name")
            )
        else:
            queryset = AllocationAttributeType.objects.none()

        self.fields["attribute__name"].queryset = queryset
        self.fields[
            "attribute__name"
        ].help_text = "To display the list of allocation attributes at least one resource must be selected."


class ProjectAttributeSearchForm(BaseAttributeSearchForm):
    """Search form for project attributes."""

    def __init__(self, *args, attribute_type_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_queryset(attribute_type_queryset)

    def setup_queryset(self, attribute_type_queryset):
        """Setup the attribute name queryset."""
        if attribute_type_queryset is not None:
            queryset = attribute_type_queryset
        else:
            queryset = ProjectAttributeType.objects.all()

        self.fields["attribute__name"].queryset = queryset


class SearchForm(forms.Form):
    """Base search form with common display and filter fields."""

    prefix = "search"

    display__id = forms.BooleanField(required=False)
    display__url = forms.BooleanField(required=False)
    display__status__name = forms.BooleanField(required=False)
    display__created = forms.BooleanField(required=False)
    display__end_date = forms.BooleanField(required=False)
    display__users = forms.BooleanField(required=False, help_text="Active users")
    display__total_users = forms.BooleanField(required=False, help_text="Active users")

    user_username = forms.CharField(label="Username Contains", max_length=25, required=False, help_text="Active user")
    status__name = forms.ModelMultipleChoiceField(queryset=None, required=False)
    created_after_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}), label="After", required=False, help_text="Includes date"
    )
    created_before_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}),
        label="Before",
        required=False,
        help_text="Does not include date",
    )
    end_date = forms.DateField(widget=forms.TextInput(attrs={"class": "datepicker"}), label="End Date", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_ordered_queryset(self, model, field_name="name", filter_kwargs=None):
        """Get an ordered queryset for a model, with optional filtering."""
        queryset = model.objects.all()
        if filter_kwargs:
            queryset = queryset.filter(**filter_kwargs)
        return queryset.order_by(field_name)

    def create_select_all_checkbox(self, field_prefix):
        """Creates a reusable select all checkbox HTML component."""
        css_id = f"div_id_{self.prefix}-select_all_{field_prefix}_displays"
        css_name = f"{self.prefix}-select_all_{field_prefix}_displays"
        return HTML(f'''
            <div class="form-group">
                <div id="{css_id}" class="form-check">
                    <input type="checkbox" name="{css_name}" class="form-check-input" id="{css_name}">
                    <label for="{css_name}" class="form-check-label">
                        <strong>Select All</strong>
                    </label>
                </div>
            </div>
        ''')


class ProjectSearchForm(SearchForm):
    """Form for searching projects with filters, displays, and attributes."""

    display__title = forms.BooleanField(required=False)
    display__description = forms.BooleanField(required=False)
    display__pi__username = forms.BooleanField(required=False)
    display__requestor__username = forms.BooleanField(required=False)
    display__project_code = forms.BooleanField(required=False)
    display__resources = forms.BooleanField(required=False)
    display__type__name = forms.BooleanField(required=False)

    title = forms.CharField(label="Project Title Contains", max_length=100, required=False)
    description = forms.CharField(label="Project Description Contains", max_length=100, required=False)
    pi__username = forms.CharField(label="PI Username Contains", max_length=25, required=False)
    requestor__username = forms.CharField(label="Requestor Username Contains", max_length=25, required=False)
    type__name = forms.ModelMultipleChoiceField(queryset=None, required=False)

    projects_using_ai = forms.BooleanField(label="Only AI", required=False)

    attribute_form = ProjectAttributeSearchForm()
    attribute_helper = AttributeFormSetHelper("project")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_querysets()
        self.setup_layout()

    def setup_querysets(self):
        """Setup querysets for status and type fields."""
        self.fields["status__name"].queryset = self.get_ordered_queryset(ProjectStatusChoice)
        self.fields["type__name"].queryset = self.get_ordered_queryset(ProjectTypeChoice)

    def setup_layout(self):
        """Setup the form layout with accordions."""
        self.helper = FormHelper(self)
        self.helper.use_custom_control = False
        self.helper.layout = Layout(
            Accordion(
                AccordionGroup(
                    "Filters",
                    "title",
                    "description",
                    "pi__username",
                    "requestor__username",
                    "user_username",
                    "status__name",
                    "type__name",
                    "projects_using_ai",
                    Fieldset(
                        "Created Date Range",
                        Div(
                            Div("created_after_date", css_class="col"),
                            Div("created_before_date", css_class="col"),
                            css_class="row",
                        ),
                    ),
                    "end_date",
                    active=False,
                ),
            ),
            Accordion(
                AccordionGroup(
                    "Displays",
                    self.create_select_all_checkbox("project"),
                    "display__id",
                    "display__url",
                    "display__title",
                    "display__description",
                    "display__pi__username",
                    "display__requestor__username",
                    "display__project_code",
                    "display__status__name",
                    "display__type__name",
                    "display__users",
                    "display__total_users",
                    "display__created",
                    "display__end_date",
                    "display__resources",
                    active=False,
                    css_id=f"{self.prefix}-project_displays",
                ),
            ),
            Accordion(
                AccordionGroup(
                    "Project Attributes",
                    Formset("projectattribute_form", "projectattribute_helper", label="projectattribute_formset"),
                    HTML(
                        '<button id="id_formset_add_project_attribute_button" type="button" class="btn btn-primary">Add Project Attribute</button>'
                    ),
                    active=False,
                )
            ),
            FormActions(Submit("submit", "Project Search"), Reset("reset", "Reset")),
        )


class UserSearchForm(forms.Form):
    """Form for searching users with filters and display options."""

    USER_TYPE_CHOICE = (("all", "All"), ("project", "Project"), ("allocation", "Allocation"))

    display__username = forms.BooleanField(label="Display usernames", required=False)
    display__first_name = forms.BooleanField(label="Display first names", required=False)
    display__last_name = forms.BooleanField(label="Display last names", required=False)
    display__userprofile__department = forms.BooleanField(label="Display departments", required=False)
    display__userprofile__title = forms.BooleanField(label="Display titles", required=False)
    display__total_projects = forms.BooleanField(label="Display total active projects", required=False)
    display__total_pi_projects = forms.BooleanField(label="Display total active PI projects", required=False)
    display__total_manager_projects = forms.BooleanField(label="Display total active Manager projects", required=False)
    display__total_allocations = forms.BooleanField(label="Display total active allocations", required=False)

    usernames = forms.CharField(label="Usernames", required=False, help_text="username1,username2,...")
    first_name = forms.CharField(label="First Name", max_length=100, required=False)
    last_name = forms.CharField(label="Last Name", max_length=100, required=False)
    userprofile__department = forms.CharField(label="Department Contains", max_length=100, required=False)
    userprofile__title = forms.CharField(label="Title Contains", max_length=30, required=False)
    type = forms.ChoiceField(initial="all", choices=USER_TYPE_CHOICE, widget=forms.RadioSelect)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_layout()

    def setup_layout(self):
        """Setup the form layout with accordions."""
        self.helper = FormHelper(self)
        self.helper.use_custom_control = False
        self.helper.layout = Layout(
            Accordion(
                AccordionGroup(
                    "Users",
                    "usernames",
                    "first_name",
                    "last_name",
                    "display__username",
                    "display__first_name",
                    "display__last_name",
                    InlineRadios("type"),
                    active=False,
                ),
                AccordionGroup(
                    "User Profiles",
                    "userprofile__department",
                    "userprofile__title",
                    "display__userprofile__department",
                    "display__userprofile__title",
                    active=False,
                ),
                AccordionGroup(
                    "Projects",
                    "display__total_projects",
                    "display__total_pi_projects",
                    "display__total_manager_projects",
                    active=False,
                ),
                AccordionGroup(
                    "Allocations",
                    "display__total_allocations",
                    active=False,
                ),
            ),
            FormActions(Submit("submit", "User Search"), Reset("reset", "Reset")),
        )


class AllocationSearchForm(SearchForm):
    """Form for searching allocations with project, allocation, and resource filters."""

    display__project__id = forms.BooleanField(required=False)
    display__project__url = forms.BooleanField(required=False)
    display__project__title = forms.BooleanField(required=False)
    display__project__description = forms.BooleanField(required=False)
    display__project__pi__username = forms.BooleanField(required=False)
    display__project__requestor__username = forms.BooleanField(required=False)
    display__project__status__name = forms.BooleanField(required=False)
    display__project__type__name = forms.BooleanField(required=False)
    display__project__created = forms.BooleanField(required=False)
    display__project__end_date = forms.BooleanField(required=False)
    display__project__users = forms.BooleanField(
        required=False,
        help_text='Active users. Enable by selecting "only search projects". Enables the user profiles section.',
    )
    display__project__total_users = forms.BooleanField(required=False, help_text="Active users")

    display__get_parent_resource__name = forms.BooleanField(required=False, label="Resource name")
    display__get_parent_resource__resource_type__name = forms.BooleanField(required=False, label="Resource type name")

    project__title = forms.CharField(label="Project Title Contains", max_length=100, required=False)
    project__description = forms.CharField(label="Project Description Contains", max_length=100, required=False)
    project__pi__username = forms.CharField(label="PI Username Contains", max_length=25, required=False)
    project__requestor__username = forms.CharField(label="Requestor Username Contains", max_length=25, required=False)
    project__user_username = forms.CharField(
        label="Username Contains", max_length=25, required=False, help_text="Active user"
    )
    project__status__name = forms.ModelMultipleChoiceField(label="Project Status", queryset=None, required=False)
    project__type__name = forms.ModelMultipleChoiceField(label="Project Type", queryset=None, required=False)
    project__created_after_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}), label="After", required=False, help_text="Includes date"
    )
    project__created_before_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}),
        label="Before",
        required=False,
        help_text="Does not include date",
    )
    project__end_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}), label="Project End Date", required=False
    )

    resources__name = forms.ModelMultipleChoiceField(label="Resource Name", queryset=None, required=False)
    resources__resource_type__name = forms.ModelMultipleChoiceField(
        label="Resource Type", queryset=None, required=False
    )

    allocationattribute_form = AllocationAttributeSearchForm()
    allocationattribute_helper = AttributeFormSetHelper("allocation")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_querysets()
        self.setup_layout()

        self.fields["display__users"].help_text = "Active users. Enables the user profiles section."

    def setup_querysets(self):
        """Setup all querysets for the form."""
        self.fields["project__status__name"].queryset = ProjectStatusChoice.objects.all().order_by("name")
        self.fields["project__type__name"].queryset = ProjectTypeChoice.objects.all().order_by("name")
        self.fields["status__name"].queryset = AllocationStatusChoice.objects.all().order_by("name")
        self.fields["resources__name"].queryset = (
            Resource.objects.filter(is_allocatable=True).select_related("resource_type").order_by("name")
        )
        self.fields["resources__resource_type__name"].queryset = ResourceType.objects.all().order_by("name")

    def setup_layout(self):
        """Setup the form layout with accordions."""
        self.helper = FormHelper(self)
        self.helper.use_custom_control = False
        self.helper.layout = Layout(
            Accordion(
                AccordionGroup(
                    "Projects",
                    Accordion(
                        AccordionGroup(
                            "Filters",
                            "project__title",
                            "project__description",
                            "project__pi__username",
                            "project__requestor__username",
                            "project__user_username",
                            "project__status__name",
                            "project__type__name",
                            Fieldset(
                                "Created Date Range",
                                Div(
                                    Div("project__created_after_date", css_class="col"),
                                    Div("project__created_before_date", css_class="col"),
                                    css_class="row",
                                ),
                            ),
                            "project__end_date",
                            active=False,
                        ),
                    ),
                    Accordion(
                        AccordionGroup(
                            "Displays",
                            self.create_select_all_checkbox("project"),
                            "display__project__id",
                            "display__project__url",
                            "display__project__title",
                            "display__project__description",
                            "display__project__pi__username",
                            "display__project__requestor__username",
                            "display__project__status__name",
                            "display__project__type__name",
                            "display__project__created",
                            "display__project__end_date",
                            active=False,
                            css_id=f"{self.prefix}-project_displays",
                        ),
                    ),
                    active=False,
                )
            ),
            Accordion(
                AccordionGroup(
                    "Allocations",
                    "user_username",
                    "status__name",
                    Fieldset(
                        "Created Date Range",
                        Div(
                            Div("created_after_date", css_class="col"),
                            Div("created_before_date", css_class="col"),
                            css_class="row",
                        ),
                    ),
                    self.create_select_all_checkbox("allocation"),
                    "display__id",
                    "display__url",
                    "display__status__name",
                    "display__users",
                    "display__total_users",
                    "display__created",
                    active=False,
                    css_id=f"{self.prefix}-allocation_displays",
                )
            ),
            Accordion(
                AccordionGroup(
                    "Resources",
                    "resources__name",
                    "resources__resource_type__name",
                    "display__get_parent_resource__name",
                    "display__get_parent_resource__resource_type__name",
                    active=False,
                )
            ),
            Accordion(
                AccordionGroup(
                    "Allocation Attributes",
                    Formset(
                        "allocationattribute_form", "allocationattribute_helper", label="allocationattribute_formset"
                    ),
                    HTML(
                        '<button id="id_formset_add_allocation_attribute_button" type="button" class="btn btn-primary">Add Allocation Attribute</button>'
                    ),
                    active=False,
                )
            ),
            FormActions(Submit("submit", "Allocation Search"), Reset("reset", "Reset")),
        )


class Formset(LayoutObject):
    """Custom layout object for rendering formsets in crispy forms."""

    template = "advanced_search/formset.html"

    def __init__(self, formset_context_name, helper_context_name=None, template=None, label=None):
        self.formset_context_name = formset_context_name
        self.helper_context_name = helper_context_name
        self.label = label

        # crispy_forms/layout.py:302 requires us to have a fields property
        self.fields = []

        # Overrides class variable with an instance level variable
        if template:
            self.template = template

    def render(self, form, context, **kwargs):
        formset = context.get(self.formset_context_name)
        helper = context.get(self.helper_context_name)
        # closes form prematurely if this isn't explicitly stated
        if helper:
            helper.form_tag = False

        context.update({"formset": formset, "helper": helper, "label": self.label})
        return render_to_string(self.template, context.flatten())
