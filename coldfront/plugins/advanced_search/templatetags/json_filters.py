from django import template

from coldfront.plugins.advanced_search.utils import format_json_query_data

register = template.Library()


@register.filter(name="format_json")
def format_json(value):
    return format_json_query_data(value)
