from django import template
from django.utils.translation import ugettext_lazy as _
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
def good_err_icon( value ):
	if value:
		return mark_safe('<span class="is-good"/>')
	else:
		return mark_safe('<span class="is-err"/>')
