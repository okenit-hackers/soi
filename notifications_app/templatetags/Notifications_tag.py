from django.template import Library

register = Library()


@register.inclusion_tag('notification/nav_link.html')
def base_notifications():
 pass


@register.inclusion_tag('notification/popup.html')
def popup_notifications():
 pass


@register.inclusion_tag('notification/styles.html')
def base_css_notifications():
 pass