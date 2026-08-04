from django import template

from coldfront.plugins.advanced_search.models import SavedSearch

register = template.Library()


@register.filter(name="format_json")
def format_json(value):
    return SavedSearch.format_query_data(value)
