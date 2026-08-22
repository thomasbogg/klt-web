from django import template

register = template.Library()


@register.filter
def get_attr(obj, attr_name):
    """Dynamic attribute lookup - Django templates have no {{ obj.field_name }} equivalent when
    field_name is itself a variable. Used to loop over AMENITY_BOOLEAN_FIELDS instead of hand-
    writing ~30 near-identical checkbox blocks."""
    return getattr(obj, attr_name, '')
