from crispy_forms.bootstrap import FormActions, InlineRadios
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Div, Fieldset, Layout, LayoutObject, Reset, Row, Submit
from django import forms
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from coldfront.core.allocation.models import AllocationAttributeType, AllocationStatusChoice
from coldfront.core.project.models import ProjectAttributeType, ProjectStatusChoice, ProjectTypeChoice
from coldfront.core.resource.models import Resource, ResourceType
from coldfront.plugins.advanced_search.models import SavedSearch

User = get_user_model()


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
                *layout_elements,
                css_id=f"{attribute_type}-attribute-row",
            ),
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
            queryset = ProjectAttributeType.objects.select_related("attribute_type").all()

        self.fields["attribute__name"].queryset = queryset


class SearchForm(forms.Form):
    """Base search form with common display and filter fields."""

    prefix = "search"

    display__id = forms.BooleanField(required=False)
    display__url = forms.BooleanField(required=False)
    display__status__name = forms.BooleanField(required=False)
    display__created = forms.BooleanField(required=False)
    display__end_date = forms.BooleanField(required=False)
    display__users = forms.BooleanField(required=False, label="Display active users")
    display__total_users = forms.BooleanField(required=False, label="Display total active users")

    user_username = forms.CharField(label="Active Username Contains", max_length=25, required=False)
    status__name = forms.ModelMultipleChoiceField(queryset=None, required=False)
    created_after_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}),
        label="Created After Date",
        required=False,
        help_text="Includes date",
    )
    created_before_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}),
        label="Created Before Date",
        required=False,
        help_text="Does not include date",
    )
    end_date = forms.DateField(widget=forms.TextInput(attrs={"class": "datepicker"}), label="End Date", required=False)

    def __init__(self, *args, **kwargs):
        self.loaded_search = kwargs.pop("loaded_search", None)
        self.is_loaded_search_owner = kwargs.pop("is_loaded_search_owner", None)

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

    def create_save_search_botton(self, search_type):
        return HTML(f"""
            <button type="button" id="btn-save-search" class="btn btn-primary float-right">Save {search_type} Search</button>
        """)


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
        """Setup the form layout with section headers and columns."""
        self.helper = FormHelper(self)
        self.helper.use_custom_control = False
        self.helper.layout = Layout(
            Fieldset(
                "Projects",
                Div(
                    Div(
                        Row(
                            Column("title"),
                            Column("description"),
                        ),
                        Row(
                            Column("status__name"),
                            Column("type__name"),
                        ),
                        Row(
                            Column("pi__username"),
                            Column("requestor__username"),
                            Column("user_username"),
                        ),
                        Row(
                            Column("created_after_date"),
                            Column("created_before_date"),
                            Column("end_date"),
                        ),
                        Row(Column("projects_using_ai")),
                        HTML("<hr>"),
                        Div(
                            Row(Column(self.create_select_all_checkbox("project"))),
                            Row(
                                Column(
                                    "display__id",
                                    "display__url",
                                    "display__title",
                                    "display__description",
                                ),
                                Column(
                                    "display__pi__username",
                                    "display__requestor__username",
                                    "display__project_code",
                                    "display__status__name",
                                ),
                                Column(
                                    "display__type__name",
                                    "display__created",
                                    "display__end_date",
                                    "display__resources",
                                ),
                                Column(
                                    "display__users",
                                    "display__total_users",
                                ),
                            ),
                            css_id="project_search-project_displays",
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "Project Attributes",
                Div(
                    Div(
                        Formset("projectattribute_form", "projectattribute_helper", label="projectattribute_formset"),
                        HTML(
                            '<button id="id_formset_add_project_attribute_button" type="button" class="btn btn-primary">Add Project Attribute</button>'
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            FormActions(
                Submit("submit", "Project Search"),
                Reset("reset", "Reset"),
                self.create_save_search_botton("Project"),
                css_class="mb-0",
            ),
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
    type = forms.ChoiceField(initial="all", choices=USER_TYPE_CHOICE, widget=forms.RadioSelect, required=False)

    def __init__(self, *args, **kwargs):
        self.loaded_search = kwargs.pop("loaded_search", None)
        self.is_loaded_search_owner = kwargs.pop("is_loaded_search_owner", None)

        super().__init__(*args, **kwargs)
        self.setup_layout()

    def create_save_search_botton(self, search_type):
        return HTML(f"""
            <button type="button" id="btn-save-search" class="btn btn-primary float-right">Save {search_type} Search</button>
        """)

    def setup_layout(self):
        """Setup the form layout with section headers and columns."""
        self.helper = FormHelper(self)
        self.helper.use_custom_control = False
        self.helper.layout = Layout(
            Fieldset(
                "Users",
                Div(
                    Div(
                        Row(
                            Column("first_name"),
                            Column("last_name"),
                        ),
                        Row(Column("usernames")),
                        Row(Column(InlineRadios("type"))),
                        HTML("<hr>"),
                        Row(
                            Column("display__username"),
                            Column("display__first_name"),
                            Column("display__last_name"),
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "User Profiles",
                Div(
                    Div(
                        Row(
                            Column("userprofile__department"),
                            Column("userprofile__title"),
                        ),
                        Row(
                            Column("display__userprofile__department"),
                            Column("display__userprofile__title"),
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "Projects",
                Div(
                    Div(
                        Row(
                            Column("display__total_projects"),
                            Column("display__total_pi_projects"),
                            Column("display__total_manager_projects"),
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "Allocations",
                Div(
                    Div(
                        Row(
                            Column("display__total_allocations"),
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            FormActions(
                Submit("submit", "User Search"),
                Reset("reset", "Reset"),
                self.create_save_search_botton("User"),
                css_class="mb-0",
            ),
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
    display__project__total_users = forms.BooleanField(required=False, label="Display project total active users")
    display__project__project_code = forms.BooleanField(required=False)

    display__get_parent_resource__name = forms.BooleanField(required=False, label="Resource name")
    display__get_parent_resource__resource_type__name = forms.BooleanField(required=False, label="Resource type name")

    project__title = forms.CharField(label="Project Title Contains", max_length=100, required=False)
    project__description = forms.CharField(label="Project Description Contains", max_length=100, required=False)
    project__pi__username = forms.CharField(label="PI Username Contains", max_length=25, required=False)
    project__requestor__username = forms.CharField(label="Requestor Username Contains", max_length=25, required=False)
    project__user_username = forms.CharField(label="Active Username Contains", max_length=25, required=False)
    project__status__name = forms.ModelMultipleChoiceField(label="Project Status", queryset=None, required=False)
    project__type__name = forms.ModelMultipleChoiceField(label="Project Type", queryset=None, required=False)
    project__created_after_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}),
        label="Created After Date",
        required=False,
        help_text="Includes date",
    )
    project__created_before_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}),
        label="Created Before Date",
        required=False,
        help_text="Does not include date",
    )
    project__end_date = forms.DateField(
        widget=forms.TextInput(attrs={"class": "datepicker"}), label="End Date", required=False
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
        """Setup the form layout with section headers and columns."""
        self.helper = FormHelper(self)
        self.helper.use_custom_control = False
        self.helper.layout = Layout(
            Fieldset(
                "Projects",
                Div(
                    Div(
                        Row(
                            Column("project__title"),
                            Column("project__description"),
                        ),
                        Row(
                            Column("project__status__name"),
                            Column("project__type__name"),
                        ),
                        Row(
                            Column("project__pi__username"),
                            Column("project__requestor__username"),
                            Column("project__user_username"),
                        ),
                        Row(
                            Column("project__created_after_date"),
                            Column("project__created_before_date"),
                            Column("project__end_date"),
                        ),
                        HTML("<hr>"),
                        Div(
                            Row(Column(self.create_select_all_checkbox("project"))),
                            Row(
                                Column(
                                    "display__project__id",
                                    "display__project__url",
                                    "display__project__title",
                                    "display__project__description",
                                ),
                                Column(
                                    "display__project__pi__username",
                                    "display__project__requestor__username",
                                    "display__project__project_code",
                                    "display__project__status__name",
                                ),
                                Column(
                                    "display__project__type__name",
                                    "display__project__created",
                                    "display__project__end_date",
                                    "display__project__total_users",
                                ),
                            ),
                            css_id="allocation_search-project_displays",
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "Allocations",
                Div(
                    Div(
                        Row(
                            Column("user_username"),
                        ),
                        Row(
                            Column("status__name"),
                        ),
                        Row(
                            Column("created_after_date"),
                            Column("created_before_date"),
                        ),
                        HTML("<hr>"),
                        Div(
                            Row(Column(self.create_select_all_checkbox("allocation"))),
                            Row(
                                Column(
                                    "display__id",
                                    "display__url",
                                    "display__status__name",
                                ),
                                Column(
                                    "display__users",
                                    "display__total_users",
                                    "display__created",
                                ),
                            ),
                            css_id="allocation_search-allocation_displays",
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "Resources",
                Div(
                    Div(
                        Row(
                            Column("resources__name"),
                            Column("resources__resource_type__name"),
                        ),
                        Row(
                            Column("display__get_parent_resource__name"),
                            Column("display__get_parent_resource__resource_type__name"),
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            Fieldset(
                "Allocation Attributes",
                Div(
                    Div(
                        Formset(
                            "allocationattribute_form",
                            "allocationattribute_helper",
                            label="allocationattribute_formset",
                        ),
                        HTML(
                            '<button id="id_formset_add_allocation_attribute_button" type="button" class="btn btn-primary">Add Allocation Attribute</button>'
                        ),
                        css_class="card-body",
                    ),
                    css_class="card mb-3",
                ),
            ),
            FormActions(
                Submit("submit", "Allocation Search"),
                Reset("reset", "Reset"),
                self.create_save_search_botton("Allocation"),
                css_class="mb-0",
            ),
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


class SearchCreateForm(forms.ModelForm):
    class Meta:
        model = SavedSearch
        fields = ["name", "description", "shared_with_users", "shared_with_groups"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if user:
            self.fields["shared_with_users"].queryset = User.objects.filter(is_staff=True).exclude(pk=user.pk)
            self.fields["shared_with_groups"].queryset = user.groups.all()
            self.fields["shared_with_users"].help_text = "Users who can view and load this search."
            self.fields["shared_with_groups"].help_text = "Groups whose members can view and load this search."
