from django import template

register = template.Library()

@register.filter
def split(value, arg):
    """
    Splits the string by the given separator and returns a list.
    """
    if value:
        return value.split(arg)
    return []
