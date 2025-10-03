from django.template import Library

register = Library()


@register.inclusion_tag('ajax/ajax_reload.html')
def ajax_reload():
 pass