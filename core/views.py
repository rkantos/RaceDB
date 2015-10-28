import re
import os
import locale

try:
	locale.setlocale(locale.LC_ALL, "")
except Exception as e:
	print 'Error: locale.setlocale(locale.LC_ALL, "") fails with "{}".'.format(e)

import json
import datetime
import string
import pprint
import StringIO
from init_prereg import init_prereg

import utils
from WriteLog import logCall

from models import *

from django.db.models import Q
from django.db import transaction, IntegrityError
from django.utils.translation import string_concat

from django.http import HttpResponse, HttpResponseRedirect
from django.template import Template, Context, RequestContext
from django.shortcuts import render_to_response, get_object_or_404
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required, user_passes_test

from django import forms
from django.forms import ModelForm, Form
from django.forms.models import inlineformset_factory
from django.forms.formsets import formset_factory

from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Submit, HTML, Button, Row
from crispy_forms.layout import Fieldset, Field, MultiField, ButtonHolder
from crispy_forms.bootstrap import AppendedText, PrependedText, FormActions

from get_crossmgr_excel import get_crossmgr_excel, get_crossmgr_excel_tt
from get_seasons_pass_excel import get_seasons_pass_excel
from get_number_set_excel import get_number_set_excel
from get_start_list_excel import get_start_list_excel
from get_license_holder_excel import get_license_holder_excel
from get_participant_excel import get_participant_excel
from participation_excel import participation_excel
from participation_data import participation_data
from year_on_year_data import year_on_year_data
from license_holder_import_excel import license_holder_import_excel

from participant_key_filter import participant_key_filter
from autostrip import autostrip

from ReadWriteTag import ReadTag, WriteTag
from FinishLynx import FinishLynxExport
from AnalyzeLog import AnalyzeLog

#---------------------------------------------------------------------
from context_processors import getContext

import create_users

from django.views.decorators.cache import patch_cache_control
from functools import wraps
def external_access(decorated_func):
	decorated_func = logCall(login_required(decorated_func))
	
	@wraps( decorated_func )
	def wrap( *args, **kwargs ):
		response = decorated_func( *args, **kwargs )
		patch_cache_control(
			response,
			no_cache=True,
			no_store=True,
			must_revalidate=True,
			proxy_revalidate=True,
			max_age=0,
		)
		response['Pragma'] = 'no-cache'
		return response
		
	return wrap

# Maximum return for large queries.
MaxReturn = 200

def pushUrl( request, name, *args, **kwargs ):
	cancelUrl = kwargs.pop( 'cancelUrl', False )
	while name.endswith('/'):
		name = name[:-1]
	target = getContext(request, 'cancelUrl' if cancelUrl else 'path')
	if args:
		url = u'{}{}/{}/'.format( target, name, u'/'.join( unicode(a) for a in args ) )
	else:
		url = u'{}{}/'.format( target, name )
	return url

def setFormReadOnly( form ):
	instance = getattr( form, 'instance', None )
	if instance:
		for name, field in form.fields.iteritems():
			field.required = False
			field.widget.attrs['readonly'] = True
			field.widget.attrs['disabled'] = True
	return form
	
def getQuery( filter_name, request, queryName = 'q' ):
	if queryName in request.GET:
		query = request.GET[queryName]
	else:
		query = request.session.get(filter_name, '')
	request.session[filter_name] = query
	return query

def applyFilter( query, objects, keyFunc = unicode, doSort = True, reverse = False ):
	query = utils.normalizeSearch( query )
	if query:
		qFields = query.split()
		ret = (obj for obj in objects if all(q in utils.normalizeSearch(keyFunc(obj)) for q in qFields) )
	else:
		ret = objects
	if doSort:
		ret = sorted( ret, reverse = reverse, key = lambda obj: utils.normalizeSearch(keyFunc(obj)) )
	return ret
	
def copyFormFields( obj, form ):
	for f in form:
		setattr(obj, f.name, form.cleaned_data[f.name])
	return obj
	
def formChangedObject( obj_original, obj, form ):
	for f in form:
		if getattr(obj_original, f.name, None) != getattr(obj, f.name, None):
			return True
	return False

@logCall
@external_access
def home( request, rfid_antenna=None ):
	if rfid_antenna is not None:
		try:
			request.session['rfid_antenna'] = int(rfid_antenna)
		except Exception as e:
			pass
	return render_to_response( 'home.html', RequestContext(request, locals()) )
	
#--------------------------------------------------------------------------------------------
def Container( *args ):
	return Div( *args, css_class = 'container' )

def Row( *args ):
	return Div( *args, css_class = 'row' )

def Col( field, cols=1 ):
	return Div( field, css_class = 'col-md-{}'.format(cols) )

@autostrip
class SearchForm( Form ):
	search_text = forms.CharField( required = False )
	
	def __init__(self, additional_buttons, *args, **kwargs):
		self.additional_buttons = additional_buttons
		hide_cancel_button = kwargs.pop( 'hide_cancel_button', False )
		
		super(SearchForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		button_args = [
			Submit( 'search-submit', 'Search', css_class = 'btn btn-primary hidden-print' ),
		]
		
		# Add additional search buttons.
		for ab in additional_buttons:
			name, value, cls = ab[:3]
			if len(ab) == 4:
				button_args.append( Submit( name, value, css_class = cls + " hidden-print") )
				
		# Add cancel button.
		if not hide_cancel_button:
			button_args.append( Submit( 'cancel-submit', 'Cancel', css_class = 'btn btn-warning hidden-print' ) )
			
		# Add non-search buttons.
		if additional_buttons:
			button_args.append( HTML('&nbsp;' * 8) )
			for ab in additional_buttons:
				name, value, cls = ab[:3]
				if len(ab) == 3:
					button_args.append( Submit( name, value, css_class = cls + " hidden-print") )
		
		Layout(
			Row(
				Col( Field('search_text', size=80), 6 ),
			)
		)
		self.helper.layout.append(  HTML( '{{ form.errors }} {{ form.non_field_errors }}' ) )
		
		self.helper.layout.extend( [
				Row( *button_args ),
			]
		)

#--------------------------------------------------------------------------------------------

SAVE_BUTTON		= (1<<0)
OK_BUTTON		= (1<<1)
CANCEL_BUTTON	= (1<<2)
NEW_BUTTONS		= OK_BUTTON | SAVE_BUTTON | CANCEL_BUTTON
DELETE_BUTTONS	= OK_BUTTON | CANCEL_BUTTON
EDIT_BUTTONS	= 0xFFFF

def addFormButtons( form, button_mask=EDIT_BUTTONS, additional_buttons=None, print_button=None, cancel_alias=None ):
	btns = []
	if button_mask & SAVE_BUTTON != 0:
		btns.append( Submit('save-submit', _('Save'), css_class='btn btn-primary hidden-print') )
	if button_mask & OK_BUTTON:
		btns.append( Submit('ok-submit', _('OK'), css_class='btn btn-primary hidden-print') )
	if button_mask & CANCEL_BUTTON:
		btns.append( Submit('cancel-submit', (cancel_alias or _('Cancel')), css_class='btn btn-warning hidden-print') )
	
	if additional_buttons:
		btns.append( HTML(u'&nbsp;' * 8) )
		for ab in additional_buttons:
			name, value, cls = ab[:3]
			btns.append( Submit(name, value, css_class = cls + ' hidden-print') )
		
	if print_button:
		btns.append( HTML(u'&nbsp;' * 8) )
		btns.append( HTML(string_concat(
					u'<button class="btn btn-primary hidden-print" onClick="window.print()">',
					print_button,
					'</button>'
				)
			)
		)
	
	form.helper.layout.append( Div(*btns, css_class = 'row') )
	#form.helper.layout.append( Div( HTML( '{{ form.errors }} {{ form.non_field_errors }}' ), css_class = 'row') )

	
def GenericModelForm( ModelClass ):
	@autostrip
	class GMForm( ModelForm ):
		class Meta:
			model = ModelClass
			fields = '__all__'
			
		def __init__( self, *args, **kwargs ):
			self.button_mask = kwargs.pop( 'button_mask', [] )
			
			super(GMForm, self).__init__(*args, **kwargs)
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'form-inline'
			
			self.additional_buttons = []
			addFormButtons( self, self.button_mask, self.additional_buttons )
			
	return GMForm

def GenericNew( ModelClass, request, ModelFormClass=None, template=None, additional_context={}, instance_fields={} ):
	title = string_concat(_('New'), ' ', ModelClass._meta.verbose_name.title())
	
	ModelFormClass = ModelFormClass or GenericModelForm(ModelClass)
	isEdit = False
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = ModelFormClass( request.POST, button_mask=NEW_BUTTONS )
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'cancelUrl'))
				
			if 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request, '{}Edit'.format(ModelClass.__name__), instance.id, cancelUrl=True) )
	else:
		instance = ModelClass( **instance_fields )
		form = ModelFormClass( instance=instance, button_mask=NEW_BUTTONS )
	
	form_context = {}
	form_context.update( locals() )
	form_context.update( additional_context )
	return render_to_response( template or 'generic_form.html', RequestContext(request, form_context) )
	
def GenericEdit( ModelClass, request, instanceId, ModelFormClass = None, template = None, additional_context = {} ):
	instance = get_object_or_404( ModelClass, pk=instanceId )
	
	ModelFormClass = ModelFormClass or GenericModelForm(ModelClass)
	isEdit = True
	
	title = unicode(_('Edit {}')).format(ModelClass._meta.verbose_name.title())
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			print 'cancel-submit'
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = ModelFormClass( request.POST, button_mask=EDIT_BUTTONS, instance=instance )
		if form.is_valid():
			formInstance = form.save( commit = False )
			formInstance.save()
			try:
				form.save_m2m()
			except Exception as e:
				pass
			if 'ok-submit' in request.POST:
				print 'ok-submit'
				return HttpResponseRedirect(getContext(request,'cancelUrl'))
				
			for ab in getattr(form, 'additional_buttons', []):
				if ab[3:] and ab[0] in request.POST:
					return ab[3]( request, instance )
	else:
		form = ModelFormClass( instance=instance, button_mask=EDIT_BUTTONS )
		
	form_context = {}
	form_context.update( locals() )
	form_context.update( additional_context )
	return render_to_response( template or 'generic_form.html', RequestContext(request, form_context) )

def GenericDelete( ModelClass, request, instanceId, ModelFormClass = None, template = None, additional_context = {} ):
	instance = get_object_or_404( ModelClass, pk=instanceId )
	
	ModelFormClass = ModelFormClass or GenericModelForm(ModelClass)
	isEdit = False
	
	title = unicode(_('Delete {}')).format(ModelClass._meta.verbose_name.title())
	if request.method == 'POST':
		if 'cancel-submit' not in request.POST:
			instance.delete()
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = setFormReadOnly( ModelFormClass(instance = instance, button_mask=DELETE_BUTTONS) )
		
	form_context = {}
	form_context.update( locals() )
	form_context.update( additional_context )
	return render_to_response( template or 'generic_form.html', RequestContext(request, form_context) )

#--------------------------------------------------------------------------------------------
@autostrip
class LicenseHolderTagForm( Form ):
	tag = forms.CharField( required = False, label = _('Tag') )
	rfid_antenna = forms.ChoiceField( choices = ((0,_('None')), (1,'1'), (2,'2'), (3,'3'), (4,'4') ), label = _('RFID Antenna to Write Tag') )
	
	def __init__(self, *args, **kwargs):
		super(LicenseHolderTagForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'navbar-form navbar-left'
		
		button_args = [
			Submit( 'ok-submit', _('Update Tag in Database'), css_class = 'btn btn-primary' ),
			Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
			Submit( 'auto-generate-tag-submit', _('Auto Generate Tag Only - Do Not Write'), css_class = 'btn btn-primary' ),
			Submit( 'write-tag-submit', _('Write Existing Tag'), css_class = 'btn btn-primary' ),
			Submit( 'read-validate-tag-submit', _('Read / Validate Tag'), css_class = 'btn btn-primary' ),
			Submit( 'auto-generate-and-write-tag-submit', _('Auto Generate and Write Tag'), css_class='btn btn-success' ),
		]
		
		self.helper.layout = Layout(
			Row(
				Col( Field('tag', cols = '60'), 4 ),
				Col( HTML('&nbsp;' * 20 ),4 ),
				Col( Field('rfid_antenna'), 4 ),
			),
			HTML( '<br/>' ),
			Row(
				button_args[5],
				HTML( '&nbsp;' * 5 ),
				button_args[3],
				HTML( '&nbsp;' * 5 ),
				button_args[4],
			),
			HTML( '<br/>' * 2 ),
			Row(
				button_args[2],
			),
			HTML( '<br/>' * 2 ),
			Row( 
				button_args[0],
				HTML( '&nbsp;' * 5 ),
				button_args[1],
			),
		)


@external_access
def LicenseHolderTagChange( request, licenseHolderId ):
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	rfid_antenna = int(request.session.get('rfid_antenna', 0))
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
		
		form = LicenseHolderTagForm( request.POST )
		if form.is_valid():
			status = True
			status_entries = []

			tag = form.cleaned_data['tag'].strip().upper()
			rfid_antenna = request.session['rfid_antenna'] = int(form.cleaned_data['rfid_antenna'])
			
			if 'auto-generate-tag-submit' in request.POST or 'auto-generate-and-write-tag-submit' in request.POST:
				tag = license_holder.get_unique_tag()
			
			license_holder.existing_tag = tag
			try:
				license_holder.save()
			except Exception as e:
				# Report the error - probably a non-unique field.
				status = False
				status_entries.append(
					(_('LicenseHolder') + u': ' + _('Existing Tag Save Exception:'), (
						unicode(e),
					)),
				)
				return render_to_response( 'rfid_write_status.html', RequestContext(request, locals()) )
			
			# Check for tag actions.
			if any(submit_btn in request.POST for submit_btn in ('read-validate-tag-submit','write-tag-submit','auto-generate-and-write-tag-submit') ):
			
				# Check for valid antenna.
				if not rfid_antenna:
					status = False
					status_entries.append(
						(_('RFID Antenna Configuration'), (
							_('RFID Antenna for Tag Write must be specified.'),
							_('Please specify the RFID Antenna.'),
						)),
					)
				
				# Check for missing tag.
				if not tag:
					status = False
					status_entries.append(
						(_('Empty Tag'), (
							_('Cannot write/validate an empty Tag.'),
							_('Please specify a Tag or press Cancel.'),
						)),
					)
				
				# Check for valid tag.
				elif not utils.allHex(tag):
					status = False
					status_entries.append(
						(_('Non-Hex Characters in Tag'), (
							_('All Tag characters must be hexadecimal ("0123456789ABCDEF").'),
							_('Please change the Tag to all hexadecimal.'),
						)),
					)
				
				if status:
					if 'read-validate-tag-submit' in request.POST:
						# Handle reading/validating an existing tag.
						status, response = ReadTag(rfid_antenna)
						tagRead = ''
						# DEBUG DEBUG
						#status, response = True, {'tags': ['E26D00061114']}
						if not status:
							status_entries.append(
								(_('Tag Read Failure'), response.get('errors',[]) ),
							)
						else:
							tags = response.get('tags', [])
							try:
								tagRead = tags[0]
							except (AttributeError, IndexError) as e:
								status = False
								status_entries.append(
									(_('Tag Read Failure'), [e] ),
								)
						
						if tagRead and len(tags) > 1:
							status = False
							status_entries.append(
								(_('Multiple Tags Read'), tags ),
							)
						elif status:
							if tagRead != tag:
								try:
									license_holder_other = LicenseHolder.objects.get(existing_tag=tagRead)
									additional_message = u'{} != {} ({})'.format(tag, tagRead, license_holder_other.full_name())
								except  (LicenseHolder.DoesNotExist, LicenseHolder.MultipleObjectsReturned) as e:
									additional_message = u'{} != {}'.format(tag, tagRead)
								status = False
								status_entries.append(
									(_('Tag read does NOT match rider tag'), [additional_message] ),
								)
							else:
								status_entries.append(
									(_('Tag read matches rider tag'), [u'{} = {}'.format(tag, tagRead)] ),
								)
						return render_to_response( 'rfid_validate.html', RequestContext(request, locals()) )
					else:
						# Handle writing the tag.
						status, response = WriteTag(tag, rfid_antenna)
						if not status:
							status_entries = [
								(_('Tag Write Failure'), response.get('errors',[]) ),
							]
				
				if not status:
					return render_to_response( 'rfid_write_status.html', RequestContext(request, locals()) )
				# if status: fall through to ok-submit case.
			
			# ok-submit
			if 'auto-generate-tag-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'path'))
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = LicenseHolderTagForm( initial = dict(tag=license_holder.existing_tag, rfid_antenna=rfid_antenna) )
		
	return render_to_response( 'license_holder_tag_change.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------------------------

@autostrip
class LicenseHolderForm( ModelForm ):
	class Meta:
		model = LicenseHolder
		fields = '__all__'
		
	def manageTag( self, request, license_holder ):
		return HttpResponseRedirect( pushUrl(request, 'LicenseHolderTagChange', license_holder.id) )
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
	
		super(LicenseHolderForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			#Field( 'id', type='hidden' ),
			Row(
				Col(Field('last_name', size=40), 4),
				Col(Field('first_name', size=40), 4),
				Col('gender', 2),
				Col(Field('date_of_birth', size=10), 2),
			),
			Row(
				Col(Field('city', size=40), 3),
				Col(Field('state_prov', size=40), 3),
				Col(Field('nationality', size=40), 3),
				Col(Field('zip_postal', size=10), 3),
			),
			Row(
				Col(Field('email', size=50), 4),
				Col(Field('phone', size=50), 4),
			),
			Row(
				Col(Field('license_code'), 3),
				Col(Field('uci_code'), 9),
			),
			Row(
				Col('existing_tag', 3),
				Col('existing_tag2', 3),
				Col('active', 3),
			),
			Row(
				Col(Field('emergency_contact_name', size=50), 4),
				Col(Field('emergency_contact_phone'), 4),
			),
			Row(
				Field('note', cols=80, rows=4),
			),
		)
		
		self.additional_buttons = []
		if button_mask == EDIT_BUTTONS:
			self.additional_buttons.append( ('manage-tag-submit', _('Manage Chip Tag'), 'btn btn-success', self.manageTag), )
		
		addFormButtons( self, button_mask, additional_buttons=self.additional_buttons )

reUCICode = re.compile( '^[A-Z]{3}[0-9]{8}$', re.IGNORECASE )
def license_holders_from_search_text( search_text ):
	search_text = utils.normalizeSearch(search_text)
	license_holders = []
	while True:
		if search_text.startswith( 'rfid=' ):
			try:
				arg = search_text.split('=',1)[1].strip().upper().lstrip('0')
				license_holders = [LicenseHolder.objects.get(Q(existing_tag=arg) | Q(existing_tag2=arg))]
				break
			except (IndexError, LicenseHolder.DoesNotExist):
				break
			except LicenseHolder.MultipleObjectsReturned:
				pass
			
		if search_text.startswith( 'scan=' ):
			try:
				arg = search_text.split('=',1)[1].strip().upper().lstrip('0')
				license_holders = [LicenseHolder.objects.get(Q(license_code=arg) | Q(uci_code=arg) | Q(existing_tag=arg) | Q(existing_tag2=arg))]
				break
			except (IndexError, LicenseHolder.DoesNotExist):
				break
			except LicenseHolder.MultipleObjectsReturned:
				pass
	
		if reUCICode.match(search_text):
			try:
				license_holders = [LicenseHolder.objects.get(uci_code = search_text.upper())]
				break
			except (LicenseHolder.DoesNotExist, LicenseHolder.MultipleObjectsReturned) as e:
				pass
		
		if search_text[-4:].isdigit():
			try:
				license_holders = [LicenseHolder.objects.get(license_code = search_text.upper().lstrip('0'))]
				break
			except (LicenseHolder.DoesNotExist, LicenseHolder.MultipleObjectsReturned) as e:
				pass
		
		q = Q()
		for n in search_text.split():
			q &= Q( search_text__contains = n )
		license_holders = LicenseHolder.objects.filter(q)[:MaxReturn]
		break
	return license_holders
	
@external_access
def LicenseHoldersDisplay( request ):

	fix_bad_license_codes()

	search_text = request.session.get('license_holder_filter', '')
	btns = [
		('search-by-barcode-submit', _('Barcode Search'), 'btn btn-primary', True),
		('search-by-tag-submit', _('RFID Search'), 'btn btn-primary', True),
		('clear-search-submit', _('Clear Search'), 'btn btn-primary', True),
		('new-submit', _('New LicenseHolder'), 'btn btn-success'),
		('manage-duplicates-submit', _('Manage Duplicates'), 'btn btn-primary'),
		('export-excel-submit', _('Export to Excel'), 'btn btn-primary'),
		('import-excel-submit', _('Import from Excel'), 'btn btn-primary'),
	]
	if request.method == 'POST':
	
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'search-by-barcode-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHolderBarcodeScan') )
			
		if 'search-by-tag-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHolderRfidScan') )
			
		if 'clear-search-submit' in request.POST:
			request.session['license_holder_filter'] = ''
			return HttpResponseRedirect( '.' )
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHolderNew') )
			
		if 'manage-duplicates-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHoldersManageDuplicates') )
			
		if 'import-excel-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHoldersImportExcel') )
			
		form = SearchForm( btns, request.POST )
		if form.is_valid():
			search_text = form.cleaned_data['search_text']
			request.session['license_holder_filter'] = search_text
			
			if 'export-excel-submit' in request.POST:
				q = Q()
				for n in search_text.split():
					q &= Q( search_text__contains = n )
				xl = get_license_holder_excel( q )
				response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
				response['Content-Disposition'] = 'attachment; filename=RaceDB-LicenseHolders-{}.xlsx'.format(
					datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S'),
				)
				return response
	else:
		form = SearchForm( btns, initial = {'search_text': search_text} )
	
	license_holders = license_holders_from_search_text( search_text )
	isEdit = True
	return render_to_response( 'license_holder_list.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------
@autostrip
class BarcodeScanForm( Form ):
	scan = forms.CharField( required = False, label = _('Barcode (License Code or RFID Tag)') )
	
	def __init__(self, *args, **kwargs):
		hide_cancel_button = kwargs.pop('hide_cancel_button', None)
		super(BarcodeScanForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'navbar-form navbar-left'
		
		button_args = [
			Submit( 'search-submit', _('Search'), css_class = 'btn btn-primary' ),
			Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
		]
		if hide_cancel_button:
			button_args = button_args[:-1]
		
		self.helper.layout = Layout(
			Row(
				Field('scan', size=40),
			),
			Row( *button_args ),
		)

@external_access
def LicenseHolderBarcodeScan( request ):
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = BarcodeScanForm( request.POST )
		if form.is_valid():
			scan = form.cleaned_data['scan'].strip()
			if not scan:
				return HttpResponseRedirect(getContext(request,'path'))
				
			request.session['license_holder_filter'] = u'scan={}'.format(scan.lstrip('0'))
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = BarcodeScanForm()
		
	return render_to_response( 'license_holder_scan_form.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------

@autostrip
class RfidScanForm( Form ):
	rfid_antenna = forms.ChoiceField( choices = ((0,_('None')), (1,'1'), (2,'2'), (3,'3'), (4,'4') ), label=_('RFID Antenna to Read Tag') )
	
	def __init__(self, *args, **kwargs):
		hide_cancel_button = kwargs.pop( 'hide_cancel_button', None )
		super(RfidScanForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'navbar-form navbar-left'
		
		button_args = [
			Submit( 'read-tag-submit', _('Read Tag'), css_class = 'btn btn-primary  btn-lg', id='focus' ),
			Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
		]
		if hide_cancel_button:
			button_args = button_args[:-1]
			
		self.helper.layout = Layout(
			Row( *button_args ),
			HTML('<br/>' * 12),
			Row(
				Col(Field('rfid_antenna'), 6 ),
			),
		)

#--------------------------------------------------------------------------
@external_access
def LicenseHolderRfidScan( request ):
	rfid_antenna = int(request.session.get('rfid_antenna', 0))
	
	status = True
	status_entries = []
	tag = None
	tags = []
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
		
		form = RfidScanForm( request.POST )
		if form.is_valid():
		
			request.session['rfid_antenna'] = rfid_antenna = int(form.cleaned_data['rfid_antenna'])
			
			if not rfid_antenna:
				status = False
				status_entries.append(
					(_('RFID Antenna Configuration'), (
						_('RFID Antenna for Tag Read must be specified.'),
						_('Please specify the RFID Antenna.'),
					)),
				)
			else:
				status, response = ReadTag(rfid_antenna)
				# DEBUG DEBUG
				#status, response = True, {'tags': ['A7A2102303']}
				if not status:
					status_entries.append(
						(_('Tag Read Failure'), response.get('errors',[]) ),
					)
				else:
					tags = response.get('tags', [])
					try:
						tag = tags[0]
					except (AttributeError, IndexError) as e:
						status = False
						status_entries.append(
							(_('Tag Read Failure'), [e] ),
						)
				
				if tag and len(tags) > 1:
					status = False
					status_entries.append(
						(_('Multiple Tags Read'), tags ),
					)
			
			if not status:
				return render_to_response( 'license_holder_scan_rfid.html', RequestContext(request, locals()) )
			
			request.session['license_holder_filter'] = u'rfid={}'.format(tag.lstrip('0'))
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = RfidScanForm( initial=dict(rfid_antenna=rfid_antenna) )
		
	return render_to_response( 'license_holder_scan_rfid.html', RequestContext(request, locals()) )

#------------------------------------------------------------------------------------------------------
@external_access
def LicenseHoldersManageDuplicates( request ):
	duplicates = LicenseHolder.get_duplicates()
	return render_to_response( 'license_holder_duplicate_list.html', RequestContext(request, locals()) )

def GetLicenseHolderSelectDuplicatesForm( duplicates ):
	choices = [(lh.pk, u'{last_name}, {first_name} - {gender} - {date_of_birth} - {city} - {state_prov} - {nationality} - {license}'.format(
		last_name=lh.last_name,
		first_name=lh.first_name,
		gender=lh.get_gender_display(),
		date_of_birth=lh.date_of_birth,
		state_prov=lh.state_prov,
		city=lh.city,
		nationality=lh.nationality,
		license=lh.license_code, )) for lh in duplicates]
	
	@autostrip
	class LicenseHolderSelectDuplicatesForm( Form ):
		pks = forms.MultipleChoiceField( required = False, label = _('Potential Duplicates'), choices=choices )
		
		def __init__(self, *args, **kwargs):
			super(LicenseHolderSelectDuplicatesForm, self).__init__(*args, **kwargs)
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'navbar-form navbar-left'
			
			button_args = [
				Submit( 'ok-submit', _('OK'), css_class = 'btn btn-primary' ),
				Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
			]
			
			self.helper.layout = Layout(
				Row(
					Field('pks', css_class = 'form-control', size = '10'),
				),
				Row(
					button_args[0],
					button_args[1],
				)
			)
	
	return LicenseHolderSelectDuplicatesForm
	
@external_access
def LicenseHoldersSelectDuplicates( request, duplicateIds ):
	pks = [int(pk) for pk in duplicateIds.split(',')]
	duplicates = LicenseHolder.objects.filter(pk__in=pks).order_by('search_text')
	if duplicates.count() != len(pks):
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
		
	if request.method == 'POST':
	
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = GetLicenseHolderSelectDuplicatesForm( duplicates )( request.POST )
		if form.is_valid():
			pks = form.cleaned_data['pks']
			return HttpResponseRedirect( '{}LicenseHoldersSelectMergeDuplicate/{}/'.format(getContext(request,'cancelUrl'), ','.join('{}'.format(pk) for pk in pks)) )
	else:
		form = GetLicenseHolderSelectDuplicatesForm( duplicates )( initial=dict(pks=pks) )
	return render_to_response( 'license_holder_select_duplicates.html', RequestContext(request, locals()) )
	
@external_access
def LicenseHoldersSelectMergeDuplicate( request, duplicateIds ):
	pks = [int(pk) for pk in duplicateIds.split(',')]
	duplicates = LicenseHolder.objects.filter(pk__in=pks).order_by('search_text')
	if duplicates.count() != len(pks):
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
	return render_to_response( 'license_holder_select_merge_duplicate.html', RequestContext(request, locals()) )

@external_access
def LicenseHoldersMergeDuplicates( request, mergeId, duplicateIds ):
	license_holder_merge = get_object_or_404( LicenseHolder, pk=mergeId )
	pks = [int(pk) for pk in duplicateIds.split(',')]
	duplicates = LicenseHolder.objects.filter(pk__in=pks).order_by('search_text')
	if duplicates.count() != len(pks):
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
	return render_to_response( 'license_holder_select_merge_duplicate_confirm.html', RequestContext(request, locals()) )

def LicenseHoldersMergeDuplicatesOK( request, mergeId, duplicateIds ):
	license_holder_merge = get_object_or_404( LicenseHolder, pk=mergeId )
	pks = [int(pk) for pk in duplicateIds.split(',')]
	pks = [pk for pk in pks if pk != license_holder_merge.pk]
	duplicates = LicenseHolder.objects.filter(pk__in=pks).order_by('search_text')
	if duplicates.count() != len(pks):
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
	license_holder_merge_duplicates( license_holder_merge, duplicates )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#------------------------------------------------------------------------------------------------------
@external_access
def LicenseHolderNew( request ):
	return GenericNew( LicenseHolder, request, LicenseHolderForm,
		instance_fields={'license_code': 'TEMP'}
	)
	
@external_access
def LicenseHolderEdit( request, licenseHolderId ):
	return GenericEdit( LicenseHolder, request,
		licenseHolderId,
		LicenseHolderForm,
		template='license_holder_form.html',
	)
	
@external_access
def LicenseHolderDelete( request, licenseHolderId ):
	return GenericDelete( LicenseHolder, request,
		licenseHolderId,
		LicenseHolderForm,
		template='license_holder_form.html',
	)
	
#--------------------------------------------------------------------------------------------
@autostrip
class TeamForm( ModelForm ):
	class Meta:
		model = Team
		fields = '__all__'
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
		
		super(TeamForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
				Col(Field('team_code', size=4), 2),
				Col('team_type', 2),
				Col('nation_code', 2),
			),
		)
		addFormButtons( self, button_mask )
		
@external_access
def TeamsDisplay( request ):
	search_text = request.session.get('teams_filter', '')
	btns = [('new-submit', _('New Team'), 'btn btn-success')]
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'TeamNew') )
			
		form = SearchForm( btns, request.POST )
		if form.is_valid():
			search_text = form.cleaned_data['search_text']
			request.session['teams_filter'] = search_text
	else:
		form = SearchForm( btns, initial = {'search_text': search_text} )
		
	search_text = utils.normalizeSearch(search_text)
	q = Q()
	for n in search_text.split():
		q &= Q( search_text__contains = n )
	teams = Team.objects.filter(q)[:MaxReturn]
	return render_to_response( 'team_list.html', RequestContext(request, locals()) )

@external_access
def TeamNew( request ):
	return GenericNew( Team, request, TeamForm )
	
@external_access
def TeamEdit( request, teamId ):
	return GenericEdit( Team, request, teamId, TeamForm )
	
@external_access
def TeamDelete( request, teamId ):
	return GenericDelete( Team, request, teamId, TeamForm )

#--------------------------------------------------------------------------------------------

@transaction.atomic
def NormalizeSequence( objs ):
	for sequence, o in enumerate(objs):
		o.sequence = sequence + 1
		o.save()
	
def SwapAdjacentSequence( Class, obj, swapBefore ):
	NormalizeSequence( Class.objects.all() )
	try:
		objAdjacent = Class.objects.get( sequence=obj.sequence + (-1 if swapBefore else 1) )
	except Class.DoesNotExist:
		return
		
	objAdjacent.sequence, obj.sequence = obj.sequence, objAdjacent.sequence
	objAdjacent.save()
	obj.save()

@transaction.atomic
def MoveSequence( Class, obj, toTop ):
	if toTop:
		for o in Class.objects.filter( sequence__lt = obj.sequence ):
			o.sequence = o.sequence + 1
			o.save()
		obj.sequence = 0
	else:
		for o in Class.objects.filter( sequence__gt = obj.sequence ):
			o.sequence = o.sequence - 1
			o.save()
		obj.sequence = Class.objects.count() - 1
	
	obj.save()	

#--------------------------------------------------------------------------------------------

@autostrip
class RaceClassDisplayForm( Form ):
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(RaceClassDisplayForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		btns = [('new-submit', _('New Race Class'), 'btn btn-success')]
		addFormButtons( self, button_mask, btns )

@autostrip
class RaceClassForm( ModelForm ):
	class Meta:
		model = RaceClass
		fields = '__all__'
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(RaceClassForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
			),
			Field( 'sequence', type='hidden' ),
		)
		addFormButtons( self, button_mask )
		
@external_access
def RaceClassesDisplay( request ):
	NormalizeSequence( RaceClass.objects.all() )
	if request.method == 'POST':
	
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'RaceClassNew') )
			
		form = RaceClassDisplayForm( request.POST )
	else:
		form = RaceClassDisplayForm()
		
	race_classes = RaceClass.objects.all()
	return render_to_response( 'race_class_list.html', RequestContext(request, locals()) )

@external_access
def RaceClassNew( request ):
	return GenericNew( RaceClass, request, RaceClassForm )

@external_access
def RaceClassEdit( request, raceClassId ):
	return GenericEdit( RaceClass, request, raceClassId, RaceClassForm )
	
@external_access
def RaceClassDelete( request, raceClassId ):
	return GenericDelete( RaceClass, request, raceClassId, RaceClassForm )

@external_access
def RaceClassDown( request, raceClassId ):
	raceClass = get_object_or_404( RaceClass, pk=raceClassId )
	SwapAdjacentSequence( RaceClass, raceClass, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def RaceClassUp( request, raceClassId ):
	raceClass = get_object_or_404( RaceClass, pk=raceClassId )
	SwapAdjacentSequence( RaceClass, raceClass, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def RaceClassBottom( request, raceClassId ):
	raceClass = get_object_or_404( RaceClass, pk=raceClassId )
	NormalizeSequence( RaceClass.objects.all() )
	MoveSequence( RaceClass, raceClass, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def RaceClassTop( request, raceClassId ):
	raceClass = get_object_or_404( RaceClass, pk=raceClassId )
	NormalizeSequence( RaceClass.objects.all() )
	MoveSequence( RaceClass, raceClass, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#--------------------------------------------------------------------------------------------

@autostrip
class DisciplineDisplayForm( Form ):
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(DisciplineDisplayForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		btns = [('new-submit', _('New Discipline'), 'btn btn-success')]
		addFormButtons( self, button_mask, btns )

@autostrip
class DisciplineForm( ModelForm ):
	class Meta:
		model = Discipline
		fields = '__all__'
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(DisciplineForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
			),
			Field( 'sequence', type='hidden' ),
		)
		addFormButtons( self, button_mask )
		
@external_access
def DisciplinesDisplay( request ):
	NormalizeSequence( Discipline.objects.all() )
	if request.method == 'POST':
	
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'DisciplineNew') )
			
		form = DisciplineDisplayForm( request.POST )
	else:
		form = DisciplineDisplayForm()
		
	disciplines = Discipline.objects.all()
	return render_to_response( 'discipline_list.html', RequestContext(request, locals()) )

@external_access
def DisciplineNew( request ):
	return GenericNew( Discipline, request, DisciplineForm )

@external_access
def DisciplineEdit( request, disciplineId ):
	return GenericEdit( Discipline, request, disciplineId, DisciplineForm )
	
@external_access
def DisciplineDelete( request, disciplineId ):
	return GenericDelete( Discipline, request, disciplineId, DisciplineForm )

@external_access
def DisciplineDown( request, disciplineId ):
	discipline = get_object_or_404( Discipline, pk=disciplineId )
	SwapAdjacentSequence( Discipline, discipline, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def DisciplineUp( request, disciplineId ):
	discipline = get_object_or_404( Discipline, pk=disciplineId )
	SwapAdjacentSequence( Discipline, discipline, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def DisciplineBottom( request, disciplineId ):
	discipline = get_object_or_404( Discipline, pk=disciplineId )
	NormalizeSequence( Discipline.objects.all() )
	MoveSequence( Discipline, discipline, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def DisciplineTop( request, disciplineId ):
	discipline = get_object_or_404( Discipline, pk=disciplineId )
	NormalizeSequence( Discipline.objects.all() )
	MoveSequence( Discipline, discipline, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#--------------------------------------------------------------------------------------------
@autostrip
class NumberSetDisplayForm( Form ):
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(NumberSetDisplayForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		btns = [('new-submit', _('New NumberSet'), 'btn btn-success')]
		addFormButtons( self, button_mask, btns )

@autostrip
class NumberSetForm( ModelForm ):
	class Meta:
		model = NumberSet
		fields = '__all__'
		
	def manageCB( self, request, numberSet ):
		return HttpResponseRedirect( pushUrl(request,'NumberSetManage', numberSet.id) )
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(NumberSetForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
			),
			Field( 'sequence', type='hidden' ),
		)
		
		self.additional_buttons = []
		if button_mask == EDIT_BUTTONS:
			self.additional_buttons.extend( [
					( 'manage-submit', _("Manage"), 'btn btn-primary', self.manageCB ),
			])
		
		addFormButtons( self, button_mask, self.additional_buttons )

@external_access
def NumberSetsDisplay( request ):
	NormalizeSequence( NumberSet.objects.all() )
	if request.method == 'POST':
	
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'NumberSetNew') )
			
		form = NumberSetDisplayForm( request.POST )
	else:
		form = NumberSetDisplayForm()
		
	number_sets = NumberSet.objects.all()
	return render_to_response( 'number_set_list.html', RequestContext(request, locals()) )

@external_access
def NumberSetNew( request ):
	return GenericNew( NumberSet, request, NumberSetForm )

@external_access
def NumberSetDelete( request, numberSetId ):
	return GenericDelete( NumberSet, request, numberSetId, NumberSetForm )

@external_access
def NumberSetDown( request, numberSetId ):
	number_set = get_object_or_404( NumberSet, pk=numberSetId )
	SwapAdjacentSequence( NumberSet, number_set, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def NumberSetUp( request, numberSetId ):
	number_set = get_object_or_404( NumberSet, pk=numberSetId )
	SwapAdjacentSequence( NumberSet, number_set, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def NumberSetBottom( request, numberSetId ):
	number_set = get_object_or_404( NumberSet, pk=numberSetId )
	NormalizeSequence( NumberSet.objects.all() )
	MoveSequence( NumberSet, number_set, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def NumberSetTop( request, numberSetId ):
	number_set = get_object_or_404( NumberSet, pk=numberSetId )
	NormalizeSequence( NumberSet.objects.all() )
	MoveSequence( NumberSet, number_set, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def BibReturn( request, numberSetEntryId, confirmed=False ):
	nse = get_object_or_404( NumberSetEntry, pk=numberSetEntryId )
	if confirmed:
		nse.number_set.return_to_pool( nse.bib )
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
	page_title = _('Return Bib to NumberSet')
	message = string_concat('<strong>', unicode(nse.bib), u'</strong>: ', nse.license_holder.full_name(), u'<br/><br/>',
		_('Return Bib to the NumberSet so it can be used again.'))
	cancel_target = getContext(request,'popUrl')
	target = getContext(request,'popUrl') + 'BibReturn/{}/{}/'.format(numberSetEntryId,1)
	return render_to_response( 'are_you_sure.html', RequestContext(request, locals()) )

@external_access
def NumberSetEdit( request, numberSetId ):
	number_set = get_object_or_404( NumberSet, pk=numberSetId )
	return GenericEdit( NumberSet, request, numberSetId, NumberSetForm )

@autostrip
class NumberSetManageForm( Form ):
	search_text = forms.CharField( required=False, label=_("Search Text") )
	search_bib = forms.IntegerField( required=False, label=_("Search Bib"), min_value=1, max_value=999999 )
	
	def __init__( self, *args, **kwargs ):
		super(NumberSetManageForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('search_text', size=50), 4),
				Col(Field('search_bib', size=50), 4),
			),
		)
		
		self.additional_buttons = [
				( 'search-submit', _("Search"), 'btn btn-success' ),
				( 'ok-submit', _("OK"), 'btn btn-primary' ),
				( 'excel-export-submit', _("Export to Excel"), 'btn btn-primary' ),
		]
		
		addFormButtons( self, 0, self.additional_buttons )

@external_access
def NumberSetManage( request, numberSetId ):
	number_set = get_object_or_404( NumberSet, pk=numberSetId )
	search_fields = request.session.get('number_set_manage_filter', {})
	
	def getData( search_fields ):
		q = Q()
		
		search_text = utils.normalizeSearch( search_fields.get('search_text', '') )
		for n in search_text.split():
			q &= Q( license_holder__search_text__contains = n )
			
		search_bib = search_fields.get('search_bib', 0)
		if search_bib:
			q &= Q( bib=search_bib )
		
		license_holders = defaultdict( list )
		
		for nse in NumberSetEntry.objects.select_related('license_holder').filter(number_set=number_set, date_lost=None).filter(q).order_by('bib'):
			license_holders[nse.license_holder].append( nse )
		for lh, nses in license_holders.iteritems():
			lh.nses = nses
		license_holders = sorted( (license_holders.iterkeys()), key = lambda lh: lh.search_text )
		number_set_lost = NumberSetEntry.objects.select_related('license_holder').filter(number_set=number_set).exclude(date_lost=None).filter(q).order_by('bib')
		
		return license_holders, number_set_lost
	
	if request.method == 'POST':
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
				
		form = NumberSetManageForm( request.POST )
		if form.is_valid():
			search_fields = {
				'search_text': form.cleaned_data['search_text'],
				'search_bib': form.cleaned_data['search_bib'],
			}
			request.session['number_set_manage_filter'] = search_fields
		
			if 'excel-export-submit' in request.POST:
				xl = get_number_set_excel( *getData(search_fields) )
				response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
				response['Content-Disposition'] = 'attachment; filename=RaceDB-NumberSet-{}-{}.xlsx'.format(
					utils.removeDiacritic(number_set.name),
					datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S'),
				)
				return response
	else:
		form = NumberSetManageForm( initial = search_fields )
	
	license_holders, number_set_lost = getData( search_fields )
	allocated_bibs_count = sum( len(lh.nses) for lh in license_holders )
	return render_to_response( 'number_set_manage.html', RequestContext(request, locals()) )	

#--------------------------------------------------------------------------------------------
@autostrip
class ReportLabelDisplayForm( Form ):
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(ReportLabelDisplayForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		btns = [('new-submit', _('New ReportLabel'), 'btn btn-success')]
		addFormButtons( self, button_mask, btns )

@autostrip
class ReportLabelForm( ModelForm ):
	class Meta:
		model = ReportLabel
		fields = '__all__'
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(ReportLabelForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
			),
			Field( 'sequence', type='hidden' ),
		)
		
		self.additional_buttons = []
		
		addFormButtons( self, button_mask, self.additional_buttons )

@external_access
def ReportLabelsDisplay( request ):
	NormalizeSequence( ReportLabel.objects.all() )
	if request.method == 'POST':
	
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'ReportLabelNew') )
			
		form = ReportLabelDisplayForm( request.POST )
	else:
		form = ReportLabelDisplayForm()
		
	report_labels = ReportLabel.objects.all()
	return render_to_response( 'report_label_list.html', RequestContext(request, locals()) )

@external_access
def ReportLabelNew( request ):
	return GenericNew( ReportLabel, request, ReportLabelForm )

@external_access
def ReportLabelEdit( request, reportLabelId ):
	report_label = get_object_or_404( ReportLabel, pk=reportLabelId )
	return GenericEdit(
		ReportLabel, request, reportLabelId, ReportLabelForm,
	)
	
@external_access
def ReportLabelDelete( request, reportLabelId ):
	return GenericDelete( ReportLabel, request, reportLabelId, ReportLabelForm )

@external_access
def ReportLabelDown( request, reportLabelId ):
	report_label = get_object_or_404( ReportLabel, pk=reportLabelId )
	SwapAdjacentSequence( ReportLabel, report_label, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def ReportLabelUp( request, reportLabelId ):
	report_label = get_object_or_404( ReportLabel, pk=reportLabelId )
	SwapAdjacentSequence( ReportLabel, report_label, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def ReportLabelBottom( request, reportLabelId ):
	report_label = get_object_or_404( ReportLabel, pk=reportLabelId )
	NormalizeSequence( ReportLabel.objects.all() )
	MoveSequence( ReportLabel, report_label, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def ReportLabelTop( request, reportLabelId ):
	report_label = get_object_or_404( ReportLabel, pk=reportLabelId )
	NormalizeSequence( ReportLabel.objects.all() )
	MoveSequence( ReportLabel, report_label, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#--------------------------------------------------------------------------------------------
@autostrip
class SeasonsPassDisplayForm( Form ):
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(SeasonsPassDisplayForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		btns = [('new-submit', _("New Season's Pass"), 'btn btn-success')]
		addFormButtons( self, button_mask, btns )

@autostrip
class SeasonsPassForm( ModelForm ):
	class Meta:
		model = SeasonsPass
		fields = '__all__'
		
	def addSeasonsPassHolderDB( self, request, seasonsPass ):
		return HttpResponseRedirect( pushUrl(request, 'SeasonsPassHolderAdd', seasonsPass.id) )
		
	def exportToExcelCB( self, request, seasonsPass ):
		xl = get_seasons_pass_excel( seasonsPass )
		response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
		response['Content-Disposition'] = 'attachment; filename=RaceDB-SeasonsPassHolders-{}-{}.xlsx'.format(
			utils.removeDiacritic(seasonsPass.name),
			datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S'),
		)
		return response
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', OK_BUTTON )
		
		super(SeasonsPassForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
			),
			Field( 'sequence', type='hidden' ),
		)
		
		self.additional_buttons = []
		if button_mask == EDIT_BUTTONS:
			self.additional_buttons.extend( [
					( 'add-seasons-pass-holder-submit', _("Add Season's Pass Holders"), 'btn btn-success', self.addSeasonsPassHolderDB ),
					( 'excel-export-submit', _("Export to Excel"), 'btn btn-primary', self.exportToExcelCB ),
			])
		
		addFormButtons( self, button_mask, self.additional_buttons )

@external_access
def SeasonsPassesDisplay( request ):
	NormalizeSequence( SeasonsPass.objects.all() )
	if request.method == 'POST':
	
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'SeasonsPassNew') )
			
		form = SeasonsPassDisplayForm( request.POST )
	else:
		form = SeasonsPassDisplayForm()
	
	seasons_passes = SeasonsPass.objects.all()
	return render_to_response( 'seasons_pass_list.html', RequestContext(request, locals()) )

@external_access
def SeasonsPassNew( request ):
	return GenericNew( SeasonsPass, request, SeasonsPassForm )

@external_access
def SeasonsPassEdit( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	return GenericEdit(
		SeasonsPass, request, seasonsPassId, SeasonsPassForm,
		template='seasons_pass_edit.html',
		additional_context={'seasons_pass_entries':SeasonsPassHolder.objects.select_related('license_holder').filter(seasons_pass=seasons_pass)}
	)
	
@external_access
def SeasonsPassDelete( request, seasonsPassId ):
	return GenericDelete( SeasonsPass, request, seasonsPassId, SeasonsPassForm )

@external_access
def SeasonsPassCopy( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	seasons_pass.clone()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def SeasonsPassDown( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	SwapAdjacentSequence( SeasonsPass, seasons_pass, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def SeasonsPassUp( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	SwapAdjacentSequence( SeasonsPass, seasons_pass, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def SeasonsPassBottom( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	NormalizeSequence( SeasonsPass.objects.all() )
	MoveSequence( SeasonsPass, seasons_pass, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def SeasonsPassTop( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	NormalizeSequence( SeasonsPass.objects.all() )
	MoveSequence( SeasonsPass, seasons_pass, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

#--------------------------------------------------------------------------------------------

@external_access
def SeasonsPassHolderAdd( request, seasonsPassId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	
	search_text = request.session.get('license_holder_filter', '')
	btns = [
		('license-holder-new-submit', _('Create New License Holder'), 'btn btn-warning'),
		('ok-submit', _('OK'), 'btn btn-success'),
	]
	if request.method == 'POST':
	
		if 'ok-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'license-holder-new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHolderNew') )
			
		form = SearchForm( btns, request.POST, hide_cancel_button=True )
		if form.is_valid():
			search_text = form.cleaned_data['search_text']
			request.session['license_holder_filter'] = search_text
	else:
		form = SearchForm( btns, initial = {'search_text': search_text}, hide_cancel_button=True )
	
	existing_seasons_pass_holders = set(spe.license_holder.id for spe in SeasonsPassHolder.objects.filter(seasons_pass=seasons_pass))
	
	# Analyse the search_text to try to use an indexed field.
	search_text = utils.normalizeSearch(search_text)
	license_holders = license_holders_from_search_text( search_text )
	return render_to_response( 'license_holder_seasons_pass_list.html', RequestContext(request, locals()) )

@external_access
def SeasonsPassLicenseHolderAdd( request, seasonsPassId, licenseHolderId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	try:
		SeasonsPassHolder( seasons_pass=seasons_pass, license_holder=license_holder ).save()
	except Exception as e:
		print e
		pass
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def SeasonsPassLicenseHolderRemove( request, seasonsPassId, licenseHolderId ):
	seasons_pass = get_object_or_404( SeasonsPass, pk=seasonsPassId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	try:
		SeasonsPassHolder.objects.get( seasons_pass=seasons_pass, license_holder=license_holder ).delete()
	except Exception as e:
		print e
		pass
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

@external_access
def SeasonsPassHolderRemove( request, seasonsPassHolderId ):
	seasons_pass_holder = get_object_or_404( SeasonsPassHolder, pk=seasonsPassHolderId )
	seasons_pass_holder.delete()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

#--------------------------------------------------------------------------------------------
@autostrip
class CategoryFormatForm( ModelForm ):
	class Meta:
		model = CategoryFormat
		fields = '__all__'
		
	def newCategoryCB( self, request, categoryFormat ):
		return HttpResponseRedirect( pushUrl(request, 'CategoryNew', categoryFormat.id) )
	
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop( 'button_mask', EDIT_BUTTONS )
		
		super(CategoryFormatForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('name', size=50), 4),
				Col(Field('description', size=100), 8),
			),
		)
		self.additional_buttons = []
		if button_mask == EDIT_BUTTONS:
			self.additional_buttons.extend( [
					( 'new-category-submit', _('New Category'), 'btn btn-success', self.newCategoryCB ),
			])
			
		addFormButtons( self, button_mask, self.additional_buttons )
		
def CategoryFormatsDisplay( request ):
	search_text = request.session.get('categoryFormat_filter', '')
	btns = [('new-submit', _('New Category'), 'btn btn-success')]
	
	if request.method == 'POST':
	
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'CategoryFormatNew') )
			
		form = SearchForm( btns, request.POST )
		if form.is_valid():
			search_text = form.cleaned_data['search_text']
			request.session['categoryFormat_filter'] = search_text
	else:
		form = SearchForm( btns, initial = {'search_text': search_text} )
		
	category_formats = applyFilter( search_text, CategoryFormat.objects.all(), CategoryFormat.get_search_text )
	return render_to_response( 'category_format_list.html', RequestContext(request, locals()) )

@external_access
def CategoryFormatNew( request ):
	return GenericNew( CategoryFormat, request, CategoryFormatForm, template = 'category_format_form.html' )

@external_access
def CategoryFormatEdit( request, categoryFormatId ):
	return GenericEdit( CategoryFormat, request, categoryFormatId, CategoryFormatForm, template = 'category_format_form.html' )
	
@external_access
def CategoryFormatCopy( request, categoryFormatId ):
	category_format = get_object_or_404( CategoryFormat, pk=categoryFormatId )
	category_format_new = category_format.make_copy()
	category_format_new.name = getCopyName( CategoryFormat, category_format.name )
	category_format.save()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryFormatDelete( request, categoryFormatId ):
	return GenericDelete( CategoryFormat, request, categoryFormatId, template = 'category_format_form.html' )

@transaction.atomic
def CategorySwapAdjacent( category, swapBefore ):
	NormalizeSequence( Category.objects.filter(format=category.format) )
	try:
		categoryAdjacent = Category.objects.get(format=category.format, sequence=category.sequence + (-1 if swapBefore else 1) )
	except Category.DoesNotExist:
		return
		
	categoryAdjacent.sequence, category.sequence = category.sequence, categoryAdjacent.sequence
	categoryAdjacent.save()
	category.save()
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryDown( request, categoryId ):
	category = get_object_or_404( Category, pk=categoryId )
	CategorySwapAdjacent( category, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryUp( request, categoryId ):
	category = get_object_or_404( Category, pk=categoryId )
	CategorySwapAdjacent( category, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#--------------------------------------------------------------------------------------------

@autostrip
class CategoryForm( ModelForm ):
	class Meta:
		model = Category
		fields = '__all__'
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
		
		super(CategoryForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('code', size=10), 2),
				Col(Field('gender'), 2),
				Col(Field('description', size=100), 8),
			),
			Field( 'sequence', type='hidden' ),
			Field( 'format', type='hidden' ),
		)
		addFormButtons( self, button_mask )

		
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryNew( request, categoryFormatId ):
	category_format = get_object_or_404( CategoryFormat, pk=categoryFormatId )

	title = unicode(_('New {}')).format(Category._meta.verbose_name.title())
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = CategoryForm( request.POST )
		if form.is_valid():
			category = form.save( commit = False )
			category.format = category_format
			category.sequence = category_format.next_category_seq
			category.save()
			
			if 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request,'CategoryEdit', category.id, cancelUrl = True) )
			
			if 'ok-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		category = Category( format = category_format ,sequence = 0 )
		form = CategoryForm( instance = category )
	
	return render_to_response( 'category_form.html', RequestContext(request, locals()) )

@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryEdit( request, categoryId ):
	return GenericEdit( Category, request, categoryId, CategoryForm, template = 'category_form.html' )
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryDelete( request, categoryId ):
	return GenericDelete( Category, request, categoryId, CategoryForm )

#--------------------------------------------------------------------------------------------

def GetFinishLynxResponse( competition ):
	zip = FinishLynxExport( competition )
	response = HttpResponse(zip, content_type="application/zip")
	response['Content-Disposition'] = 'attachment; filename={}-FinishLynx.zip'.format(
		utils.removeDiacritic(competition.name),
	)
	return response	

def ApplyNumberSet( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	participants_changed = competition.apply_number_set()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

def InitializeNumberSet( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	competition.initialize_number_set()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
def GetCompetitionForm( competition_cur = None ):
	@autostrip
	class CompetitionForm( ModelForm ):
		class Meta:
			model = Competition
			fields = '__all__'
			widgets = {
				'ftp_password': forms.PasswordInput(render_value=True),
			}

		def uploadPrereg( self, request, competition ):
			return HttpResponseRedirect( pushUrl(request, 'UploadPrereg', competition.id) )
		
		def autoGenerateMissingTags( self, request, competition ):
			participants_changed = competition.auto_generate_missing_tags()
			participants_changed_count = len(participants_changed)
			title = _('Tags Updated')
			return render_to_response( 'participants_changed.html', RequestContext(request, locals()) )
			
		def applyNumberSet( self, request, competition ):
			page_title = _('Apply Number Set')
			message = _('This will overwrite participant bibs from the NumberSet.')
			target = pushUrl(request, 'ApplyNumberSet', competition.id)
			return render_to_response( 'are_you_sure.html', RequestContext(request, locals()) )
			
		def initializeNumberSet( self, request, competition ):
			page_title = _('Initialize Number Set')
			message = _('This will initialize and replace the NumberSet using the Participant Bib Numbers from this Competition.')
			target = pushUrl(request, 'InitializeNumberSet', competition.id)
			return render_to_response( 'are_you_sure.html', RequestContext(request, locals()) )
			
		def editReportTags( self, request, competition ):
			return HttpResponseRedirect( pushUrl(request,'ReportLabels') )
		
		def __init__( self, *args, **kwargs ):
			button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
			
			super(CompetitionForm, self).__init__(*args, **kwargs)
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'form-inline'
			
			self.helper.layout = Layout(
				Row(
					Col(Field('name', size=40), 4),
					Col(Field('description', size=40), 4),
					Col('category_format', 4),
				),
				Row(
					Col(Field('city', size=40), 4),
					Col(Field('stateProv', size=40), 4),
					Col(Field('country', size=40), 4),
				),
				Row(
					Col(Field('organizer', size=40), 3),
					Col(Field('organizer_contact', size=40), 3),
					Col(Field('organizer_email', size=40), 3),
					Col(Field('organizer_phone', size=20), 3),
				),
				Row(
					Col('discipline', 2),
					Col('race_class', 2),
				),
				Row(
					Col('start_date', 2),
					Col('number_of_days', 2),
					Col('distance_unit', 2),
					Col('number_set', 3),
					Col('seasons_pass', 3),
				),
				Row( HTML('<hr/>') ),
				Row(
					Col('using_tags', 3),
					Col('use_existing_tags', 3),
				),
				Row( HTML('<hr/>') ),
				Row(
					Col('ftp_host', 2),
					Col('ftp_user', 2),
					Col('ftp_password', 2),
					Col(Field('ftp_path', size=80), 6),
				),
				Row(
					Col('ftp_upload_during_race', 4),
				),
				Row( HTML('<hr/>') ),
				Row(
					Col(Field('report_labels',size=8), 2),
					Col('ga_tracking_id', 4),
					Col(Field('show_signature'),4),
				),
				Row( HTML('<hr/>') ),
			)
			
			self.additional_buttons = []
			if button_mask == EDIT_BUTTONS:
				self.additional_buttons.append(
					('upload-prereg-list-submit', _('Upload Prereg List'), 'btn btn-success', self.uploadPrereg),
				)
				self.additional_buttons.append(
					('edit-report-labels-submit', _('Edit Report Labels'), 'btn btn-primary', self.editReportTags),
				)
				if competition_cur and competition_cur.using_tags:
					self.additional_buttons.append(
						('auto-generate-missing-tags-submit', _('Auto Generate Missing Tags for Existing Participants'), 'btn btn-primary', self.autoGenerateMissingTags),
					)
				if competition_cur and competition_cur.number_set:
					self.additional_buttons.append(
						('apply-number-set-submit', _('Reapply NumberSet to Existing Participants'), 'btn btn-primary', self.applyNumberSet),
					)
					self.additional_buttons.append(
						('initialize-number-set-submit', _('Initialize NumberSet from Existing Participants'), 'btn btn-primary', self.initializeNumberSet),
					)
			
			addFormButtons( self, button_mask, additional_buttons=self.additional_buttons )
	
	return CompetitionForm
			
@external_access
def CompetitionsDisplay( request ):
	form = None
	search_text = request.session.get('competition_filter', '')
	btns = [('new-submit', _('New Competition'), 'btn btn-success')] if request.user.is_superuser else []
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'CompetitionNew') )
		
		if request.user.is_superuser:
			form = SearchForm( btns, request.POST )
			if form.is_valid():
				search_text = form.cleaned_data['search_text']
				request.session['competition_filter'] = search_text
	else:
		if request.user.is_superuser:
			form = SearchForm( btns, initial = {'search_text': search_text} )
	
	if form:
		competitions = applyFilter( search_text, Competition.objects.all(), Competition.get_search_text )
	else:
		competitions = Competition.objects.all()
	
	# If not super user, only show the competitions for today.
	just_for_today = (not request.user.is_superuser)
	if just_for_today:
		today = datetime.date.today()
		competitions = [x for x in competitions if x.start_date <= today <= x.finish_date]
		
	competitions = sorted( competitions, key = lambda x: x.start_date, reverse = True )
	return render_to_response( 'competition_list.html', RequestContext(request, locals()) )

@external_access
@user_passes_test( lambda u: u.is_superuser )
def CompetitionNew( request ):
	missing = []
	if not CategoryFormat.objects.count():
		missing.append( (_('No Category Formats defined.'), pushUrl(request,'CategoryFormats')) )
	if missing:
		return render_to_response( 'missing_elements.html', RequestContext(request, locals()) )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = GetCompetitionForm()(request.POST, button_mask = NEW_BUTTONS)
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST:
				return HttpResponseRedirect( '{}CompetitionDashboard/{}/'.format(getContext(request,'cancelUrl'), instance.id) )
				
			if 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request, 'CompetitionEdit', instance.id, cancelUrl = True) )
	else:
		competition = Competition()
		# Initialize the category format to the first one.
		for category_format in CategoryFormat.objects.all():
			competition.category_format = category_format
			break
		form = GetCompetitionForm(competition)( instance = competition, button_mask = NEW_BUTTONS )
	
	return render_to_response( 'competition_form.html', RequestContext(request, locals()) )
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CompetitionEdit( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return GenericEdit(
		Competition, request, competitionId, GetCompetitionForm(competition),
		template = 'competition_form.html',
		additional_context = dict(events=competition.get_events(), category_numbers=competition.categorynumbers_set.all()),
	)
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CompetitionCopy( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	competition_new = competition.make_copy()
	competition_new.name = getCopyName( Competition, competition.name )
	competition_new.save()
	return HttpResponseRedirect(getContext(request, 'cancelUrl'))
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CompetitionDelete( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return GenericDelete(
		Competition, request, competitionId, GetCompetitionForm(competition),
		template = 'competition_form.html',
		additional_context = dict(events=competition.get_events()),
	)

@external_access
def CompetitionDashboard( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	events_mass_start = competition.get_events_mass_start()
	events_tt = competition.get_events_tt()
	category_numbers=competition.categorynumbers_set.all()
	return render_to_response( 'competition_dashboard.html', RequestContext(request, locals()) )

def GetRegAnalyticsForm( competition ):
	class RegAnalyticsForm( Form ):
		dates = [d.strftime('%Y-%m-%d') for d in sorted( set(e.date_time.date() for e in competition.get_events()) )]
		
		day = forms.ChoiceField( required = True, choices=((d, d) for d in dates) )
		
		def __init__(self, *args, **kwargs):
			super(RegAnalyticsForm, self).__init__(*args, **kwargs)
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'navbar-form navbar-left'
			
			button_args = [
				Submit( 'ok-submit', _('OK'), css_class = 'btn btn-primary' ),
				Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
			]
			
			self.helper.layout = Layout(
				Row(
					Field('day', cols = '60'),
				),
				Row(
					button_args[0],
					HTML( '&nbsp;' * 5 ),
					button_args[1],
				),
			)
	return RegAnalyticsForm
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CompetitionRegAnalytics( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = GetRegAnalyticsForm(competition)(request.POST)
		if form.is_valid():
			start = datetime.datetime( *[int(v) for v in form.cleaned_data['day'].replace('-', ' ').split()] )
	else:
		form = GetRegAnalyticsForm(competition)()
		start = datetime.datetime( *[int(v) for v in form.dates[0].replace('-', ' ').split()] )
	
	payload = AnalyzeLog( start=start, end=start + datetime.timedelta(hours=24) ) or {}
	payload['valid'] = bool(payload)
	payload['participant_total'] = Participant.objects.filter(competition=competition, role=Participant.Competitor,).count()
	payload['participant_prereg_total'] = Participant.objects.filter(competition=competition, role=Participant.Competitor, preregistered=True).count()
	payload['license_holder_total'] = len( set(Participant.objects.filter(competition=competition).values_list('license_holder__pk', flat=True)) )
	try:
		payload['transactionPeak'][0] = payload['transactionPeak'][0].strftime('%H:%M').lstrip('0')
	except:
		pass
	payload_json = json.dumps(payload, separators=(',',':'))
	return render_to_response( 'reg_analytics.html', RequestContext(request, locals()) )

@external_access
def TeamsShow( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	team_info = [ {
			'team':team,
			'team_name':team.name,
			'staff':Participant.objects.filter(competition=competition, team=team).exclude(role=Participant.Competitor).order_by('role'),
			'competitor_count':Participant.objects.filter(competition=competition, team=team, role=Participant.Competitor).count(),
			'competitors':Participant.objects.filter(competition=competition, team=team, role=Participant.Competitor).order_by('bib'),
		} for team in competition.get_teams() ]
	team_info.append(
		{
			'team_name':unicode(_('<<No Team>>')),
			'staff':[],
			'competitor_count':Participant.objects.filter(competition=competition, team__isnull=True, role=Participant.Competitor).count(),
			'competitors':Participant.objects.filter(competition=competition, team__isnull=True, role=Participant.Competitor).order_by('bib'),
		}
	)
	
	def getCategorySeq( p ):
		return p.category.sequence if p.category else -1
	
	for ti in team_info:
		ti['competitors'] = competitors = sorted(
			list(ti['competitors']),
			key=lambda p: (getCategorySeq(p), p.license_holder.search_text),
		)
		for i in xrange( 1, len(competitors) ):
			if getCategorySeq(competitors[i-1]) != getCategorySeq(competitors[i]):
				competitors[i].different_category = True
	
	num_teams = len(team_info)
	return render_to_response( 'teams_show.html', RequestContext(request, locals()) )

@external_access
def FinishLynx( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return GetFinishLynxResponse( competition )

#--------------------------------------------------------------------------------------------
@external_access
def CompetitionEventParticipationSummary( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )

#--------------------------------------------------------------------------------------------
@external_access
def StartLists( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	events = competition.get_events()
	return render_to_response( 'start_lists.html', RequestContext(request, locals()) )
	
@external_access
def StartList( request, eventId ):
	instance = get_object_or_404( EventMassStart, pk=eventId )
	time_stamp = datetime.datetime.now()
	page_title = u'{} - {}'.format( instance.competition.name, instance.name )
	return render_to_response( 'mass_start_start_list.html', RequestContext(request, locals()) )
	
@external_access
def StartListTT( request, eventTTId ):
	instance = get_object_or_404( EventTT, pk=eventTTId )
	time_stamp = datetime.datetime.now()
	page_title = u'{} - {}'.format( instance.competition.name, instance.name )
	return render_to_response( 'tt_start_list.html', RequestContext(request, locals()) )

def StartListExcelDownload( request, eventId, eventType ):
	eventType = int(eventType)
	if eventType == 0:
		event = get_object_or_404( EventMassStart, pk=eventId )
	elif eventType == 1:
		event = get_object_or_404( EventTT, pk=eventId )
	else:
		assert False, 'unknown event type: {}'.format(eventType)
	
	xl = get_start_list_excel( event )
	response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	response['Content-Disposition'] = 'attachment; filename=RaceDB-{}-{}_{}-{}.xlsx'.format(
		utils.removeDiacritic(event.competition.name),	
		utils.removeDiacritic(event.name),
		event.date_time.strftime('%Y-%m-%d-%H%M%S'),
		datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S'),
	)
	return response

#--------------------------------------------------------------------------------------------
@autostrip
class UploadPreregForm( Form ):
	excel_file = forms.FileField( required=True, label=_('Excel Spreadsheet (*.xlsx, *.xls)') )
	clear_existing = forms.BooleanField( required=False, label=_('Clear All Participants First'), help_text=_("Removes all existing Participants from the Competition before the Upload.  Use with Caution.") )
	
	def __init__( self, *args, **kwargs ):
		super( UploadPreregForm, self ).__init__( *args, **kwargs )
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col( Field('excel_file', accept=".xls,.xlsx"), 8),
				Col( Field('clear_existing'), 4 ),
			),
		)
		
		addFormButtons( self, OK_BUTTON | CANCEL_BUTTON, cancel_alias=_('Done') )

def handle_upload_prereg( competitionId, excel_contents, clear_existing ):
	worksheet_contents = excel_contents.read()
	message_stream = StringIO.StringIO()
	init_prereg(
		competitionId=competitionId,
		worksheet_contents=worksheet_contents,
		message_stream=message_stream,
		clear_existing=clear_existing,
	)
	results_str = message_stream.getvalue()
	return results_str

@external_access
@user_passes_test( lambda u: u.is_superuser )
def UploadPrereg( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = UploadPreregForm(request.POST, request.FILES)
		if form.is_valid():
			results_str = handle_upload_prereg( competitionId, request.FILES['excel_file'], form.cleaned_data['clear_existing'] )
			del request.FILES['excel_file']
			return render_to_response( 'upload_prereg.html', RequestContext(request, locals()) )
	else:
		form = UploadPreregForm()
	
	return render_to_response( 'upload_prereg.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------------------------
@autostrip
class ImportExcelForm( Form ):
	excel_file = forms.FileField( required=True, label=_('Excel Spreadsheet (*.xlsx, *.xls)') )
	update_license_codes = forms.BooleanField( required=False, label=_('Update License Codes based on First Name, Last Name, Date of Birth match'),
			help_text=_('WARNING: Only check this if you wish to replace the License codes with new ones.  MAKE A BACKUP FIRST.  Be Careful!') )
	
	def __init__( self, *args, **kwargs ):
		super( ImportExcelForm, self ).__init__( *args, **kwargs )
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Field('excel_file', accept=".xls,.xlsx"),
			),
			Row(
				Field('update_license_codes'),
			),
		)

		addFormButtons( self, OK_BUTTON | CANCEL_BUTTON, cancel_alias=_('Done') )

def handle_license_holder_import_excel( excel_contents, update_license_codes ):
	worksheet_contents = excel_contents.read()
	message_stream = StringIO.StringIO()
	license_holder_import_excel(
		worksheet_contents=worksheet_contents,
		message_stream=message_stream,
		update_license_codes=update_license_codes,
	)
	results_str = message_stream.getvalue()
	return results_str
		
@external_access
@user_passes_test( lambda u: u.is_superuser )
def LicenseHoldersImportExcel( request ):
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = ImportExcelForm(request.POST, request.FILES)
		if form.is_valid():
			results_str = handle_license_holder_import_excel( request.FILES['excel_file'], form.cleaned_data['update_license_codes'] )
			del request.FILES['excel_file']
			return render_to_response( 'license_holder_import_excel.html', RequestContext(request, locals()) )
	else:
		form = ImportExcelForm()
	
	return render_to_response( 'license_holder_import_excel.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------------------------
@autostrip
class AdjustmentForm( Form ):
	est_speed = forms.CharField( max_length=6, required=False, widget=forms.TextInput(attrs={'class':'est_speed'}) )
	seed_early = forms.BooleanField( required = False )
	adjustment = forms.CharField( max_length=6, required=False, widget=forms.TextInput(attrs={'class':'adjustment'}) )
	entry_tt_pk = forms.CharField( widget=forms.HiddenInput() )

class AdjustmentFormSet( formset_factory(AdjustmentForm, extra=0, max_num=100000) ):
	def __init__( self, *args, **kwargs ):
		if 'entry_tts' in kwargs:
			entry_tts = list( kwargs['entry_tts'] )
			del kwargs['entry_tts']
			super( AdjustmentFormSet, self ).__init__(
				initial=[{
					'est_speed':u'{:.3f}'.format(e.participant.competition.to_local_speed(e.participant.est_kmh)),
					'seed_early': e.participant.seed_early,
					'adjustment': '',
					'entry_tt_pk': unicode(e.pk),
				} for e in entry_tts]
			)
			
			# Get the waves for each participant.
			participant_wave = {}
			for event in set( ett.event for ett in entry_tts ):
				for wave in event.get_wave_set().all():
					for p in wave.get_participants_unsorted():
						participant_wave[p.pk] = wave
			
			# Add the entry_tt to each form to support the display.
			# Also add the wave.
			# Also add a flag when the time gap changes.
			tDelta = datetime.timedelta( seconds = 0 )
			for i, form in enumerate(self):
				form.entry_tt = entry_tts[i]
				form.wave = participant_wave.get(entry_tts[i].participant.pk, None)
				if i > 0:
					tDeltaCur = entry_tts[i].start_time - entry_tts[i-1].start_time
					if tDeltaCur != tDelta:
						if i > 1:
							form.gap_change = True
						tDelta = tDeltaCur
		else:
			super( AdjustmentFormSet, self ).__init__( *args, **kwargs )

def SeedingEdit( request, eventTTId ):
	instance = get_object_or_404( EventTT, pk=eventTTId )
	if request.method == 'POST':
		adjustment_formset = AdjustmentFormSet( request.POST )
		reNum = re.compile( '[^0-9]' )
		if adjustment_formset.is_valid():
			def get_eda():
				''' Get the entry_tt, direction and adjustment for each line in the form. '''
				''' Also commit the est_speed to the entry. '''
				eda = []
				for d in adjustment_formset.cleaned_data:
					pk = d['entry_tt_pk']
					try:
						pk = int(pk)
					except ValueError:
						pass
					
					try:
						entry_tt = EntryTT.objects.get( pk=pk )
					except EntryTT.DoesNotExist:
						continue
					
					participant_changed = False
					
					try:
						est_kmh = entry_tt.participant.competition.to_kmh(float(d['est_speed']))
						if entry_tt.participant.est_kmh != est_kmh:
							entry_tt.participant.est_kmh = est_kmh
							participant_changed = True
					except ValueError:
						pass
					
					try:
						seed_early = d['seed_early']
						if entry_tt.participant.seed_early != seed_early:
							entry_tt.participant.seed_early = seed_early
							participant_changed = True
					except ValueError:
						pass
					
					if participant_changed:
						entry_tt.participant.save()
					
					adjustment = d['adjustment'].strip()
					if not adjustment:
						continue

					direction = adjustment[0] if adjustment[0] in ('+','-') else None
						
					try:
						adjustment = int( reNum.sub(u'', adjustment) )
					except ValueError:
						continue
					
					eda.append( (entry_tt, direction, adjustment) )
				return eda
			
			with transaction.atomic():
				eda = get_eda()
				if "apply_adjustments" in request.POST:
					for entry_tt, direction, adjustment in eda:
						if direction == '-':
							entry_tt.move_to( entry_tt.start_sequence - adjustment )
					for entry_tt, direction, adjustment in reversed(eda):
						if direction == '+':
							entry_tt.move_to( entry_tt.start_sequence + adjustment )
					
					eda.sort( key = lambda v: (v[2], v[0].start_sequence) )
					
					for entry_tt, direction, adjustment in eda:
						if direction is None and adjustment < entry_tt.start_sequence:
							entry_tt.move_to( adjustment )
					for entry_tt, direction, adjustment in reversed(eda):
						if direction is None and adjustment > entry_tt.start_sequence:
							entry_tt.move_to( adjustment )
				if "regenerate_start_times" in request.POST:
					instance.create_initial_seeding()
	
	entry_tts=list(instance.entrytt_set.all())
	for e in entry_tts:
		e.clock_time = instance.date_time + e.start_time
	adjustment_formset = AdjustmentFormSet( entry_tts=entry_tts )
	return render_to_response( 'seeding_edit.html', RequestContext(request, locals()) )

def GenerateStartTimes( request, eventTTId ):
	instance = get_object_or_404( EventTT, pk=eventTTId )
	instance.create_initial_seeding()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

#--------------------------------------------------------------------------------------------
def GetCategoryNumbersForm( competition, category_numbers = None ):
	class CategoryNumbersForm( GenericModelForm(CategoryNumbers) ):
		def __init__( self, *args, **kwargs ):
			super(CategoryNumbersForm, self).__init__(*args, **kwargs)
			categories_field = self.fields['categories']
			
			category_list = competition.get_categories_without_numbers()
			
			if category_numbers is not None:
				category_list.extend( category_numbers.get_category_list() )
				category_list.sort( key = lambda c: c.sequence )
				
			categories_field.choices = [(category.id, category.full_name()) for category in category_list]
			categories_field.label = _('Available Categories')
			self.helper['categories'].wrap( Field, size=12 )
			self.helper['competition'].wrap( Field, type='hidden' )
	return CategoryNumbersForm
		
@external_access
def CategoryNumbersDisplay( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	category_numbers_list = sorted( CategoryNumbers.objects.filter(competition = competition), key = CategoryNumbers.get_key )
	return render_to_response( 'category_numbers_list.html', RequestContext(request, locals()) )
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryNumbersNew( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	category_numbers_list = CategoryNumbers.objects.filter( competition = competition )

	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = GetCategoryNumbersForm(competition)(request.POST, button_mask = NEW_BUTTONS)
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'cancelUrl'))
				
			if 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request,'CategoryNumbersEdit', instance.id, cancelUrl = True) )
	else:
		category_numbers = CategoryNumbers()
		category_numbers.competition = competition
		form = GetCategoryNumbersForm(competition)( instance = category_numbers, button_mask = NEW_BUTTONS )
	
	return render_to_response( 'category_numbers_form.html', RequestContext(request, locals()) )
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryNumbersEdit( request, categoryNumbersId ):
	category_numbers = get_object_or_404( CategoryNumbers, pk=categoryNumbersId )
	competition = category_numbers.competition
	return GenericEdit(
		CategoryNumbers, request, categoryNumbersId,
		GetCategoryNumbersForm(competition, category_numbers),
		template = 'category_numbers_form.html',
		additional_context = dict(
			competition = competition,
			category_numbers_list = CategoryNumbers.objects.filter( competition = competition )
		),
	)
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def CategoryNumbersDelete( request, categoryNumbersId ):
	category_numbers = get_object_or_404( CategoryNumbers, pk=categoryNumbersId )
	competition = category_numbers.competition
	category_numbers_list = CategoryNumbers.objects.filter( competition = competition )
	return GenericDelete(
		CategoryNumbers, request, categoryNumbersId,
		GetCategoryNumbersForm(competition, category_numbers),
		template = 'category_numbers_form.html',
		additional_context = dict(
			competition = competition,
			category_numbers_list = CategoryNumbers.objects.filter( competition = competition )
		),
	)

#--------------------------------------------------------------------------------------------

@autostrip
class EventMassStartForm( ModelForm ):
	class Meta:
		model = EventMassStart
		fields = '__all__'
	
	def newWaveCB( self, request, eventMassStart ):
		return HttpResponseRedirect( pushUrl(request,'WaveNew',eventMassStart.id) )
	
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
		
		super(EventMassStartForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline hidden-print'
		
		self.helper.layout = Layout(
			Field( 'competition', type='hidden' ),
			Field( 'event_type', type='hidden' ),
			Field( 'option_id', type='hidden' ),
			Row(
				Col(Field('name', size=40), 6),
				Col(Field('date_time', size=24), 6),
			),
			Row(
				Col(Field('optional'), 6),
				Col(Field('select_by_default'), 6),
			),
			Row( Field('rfid_option') ),
			Row( Field('road_race_finish_times') ),
			Row( Field('note', rows='4', cols='60') ),
		)
		self.additional_buttons = []
		if button_mask == EDIT_BUTTONS:
			self.additional_buttons.extend( [
				('new-wave-submit', _('New Start Wave'), 'btn btn-success', self.newWaveCB),
			] )
		addFormButtons( self, button_mask, self.additional_buttons, print_button = _('Print Waves') if button_mask == EDIT_BUTTONS else None )

@external_access
def EventMassStartDisplay( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return render_to_response( 'event_mass_start_list.html', RequestContext(request, locals()) )

@external_access
@user_passes_test( lambda u: u.is_superuser )
def EventMassStartNew( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = EventMassStartForm(request.POST, button_mask = NEW_BUTTONS)
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST or 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request, 'EventMassStartEdit', instance.id, cancelUrl=True) )
	else:
		instance = EventMassStart(
			competition = competition,
			date_time = datetime.datetime.combine( competition.start_date, datetime.time(10, 0, 0) ),
		)
		form = EventMassStartForm( instance = instance, button_mask = NEW_BUTTONS )
	
	return render_to_response( 'event_mass_start_form.html', RequestContext(request, locals()) )
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def EventMassStartEdit( request, eventId ):
	return GenericEdit( EventMassStart, request, eventId, EventMassStartForm,
		template = 'event_mass_start_form.html'
	)

@external_access
@user_passes_test( lambda u: u.is_superuser )
def EventMassStartDelete( request, eventId ):
	return GenericDelete( EventMassStart, request, eventId, EventMassStartForm,
		template = 'event_mass_start_form.html',
	)

@external_access
def EventMassStartCrossMgr( request, eventId ):
	eventMassStart = get_object_or_404( EventMassStart, pk=eventId )
	competition = eventMassStart.competition
	xl = get_crossmgr_excel( eventMassStart )
	response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	response['Content-Disposition'] = 'attachment; filename={}-{}-{}.xlsx'.format(
		eventMassStart.date_time.strftime('%Y-%m-%d'),
		utils.removeDiacritic(competition.name),
		utils.removeDiacritic(eventMassStart.name),
	)
	return response

#--------------------------------------------------------------------------------------------

def GetWaveForm( event_mass_start, wave = None ):
	@autostrip
	class WaveForm( ModelForm ):
		class Meta:
			model = Wave
			fields = '__all__'
			
		def __init__( self, *args, **kwargs ):
			button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
			
			super(WaveForm, self).__init__(*args, **kwargs)
			
			category_list = event_mass_start.get_categories_without_wave()
			
			if wave is not None and wave.pk is not None:
				category_list.extend( wave.categories.all() )
				category_list.sort( key = lambda c: c.sequence )
				self.fields['distance'].label = u'{} ({})'.format( _('Distance'), wave.distance_unit )
			
			categories_field = self.fields['categories']
			categories_field.choices = [(category.id, category.full_name()) for category in category_list]
			categories_field.label = _('Available Categories')
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'form-inline hidden-print'
			
			self.helper.layout = Layout(
				Field( 'event', type='hidden' ),
				Row(
					Col(Field('name', size=40), 4),
				),
				Row(
					Col(Field('start_offset'), 2),
					Col(Field('distance'), 3),
					Col(Field('laps'), 3),
					Col(Field('minutes'), 3),
				),
				Row(
					Col(Field('categories', size=12, css_class='hidden-print'), 6),
				),
			)
			addFormButtons( self, button_mask )
			
	return WaveForm

@external_access
@user_passes_test( lambda u: u.is_superuser )
def WaveNew( request, eventMassStartId ):
	event_mass_start = get_object_or_404( EventMassStart, pk=eventMassStartId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = GetWaveForm(event_mass_start)(request.POST, button_mask = NEW_BUTTONS)
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'cancelUrl'))
			if 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request, 'WaveEdit', instance.id, cancelUrl = True) )
	else:
		wave = Wave()
		wave.event = event_mass_start
		waves_existing = list( event_mass_start.wave_set.all() )
		c = len( waves_existing )
		waveLetter = []
		while 1:
			waveLetter.append( string.ascii_uppercase[c % 26] )
			c //= 26
			if c == 0:
				break
		waveLetter.reverse()
		waveLetter = ''.join( waveLetter )
		wave.name = _('Wave') + ' ' + waveLetter
		if waves_existing:
			wave_last = waves_existing[-1]
			wave.start_offset = wave_last.start_offset + datetime.timedelta(seconds = 60.0)
			wave.distance = wave_last.distance
			wave.laps = wave_last.laps
			wave.minutes = wave_last.minutes
		form = GetWaveForm(event_mass_start, wave)(instance = wave, button_mask = NEW_BUTTONS)
	
	return render_to_response( 'wave_form.html', RequestContext(request, locals()) )

@external_access
@user_passes_test( lambda u: u.is_superuser )
def WaveEdit( request, waveId ):
	wave = get_object_or_404( Wave, pk=waveId )
	return GenericEdit( Wave, request, waveId, GetWaveForm(wave.event, wave),
		template = 'wave_form.html'
	)
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def WaveDelete( request, waveId ):
	wave = get_object_or_404( Wave, pk=waveId )
	return GenericDelete( Wave, request, waveId, GetWaveForm(wave.event, wave),
		template = 'wave_form.html'
	)

#--------------------------------------------------------------------------------------------

@autostrip
class EventTTForm( ModelForm ):
	class Meta:
		model = EventTT
		fields = '__all__'
	
	def newWaveTTCB( self, request, eventTT ):
		return HttpResponseRedirect( pushUrl(request,'WaveTTNew',eventTT.id) )
	
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
		
		super(EventTTForm, self).__init__(*args, **kwargs)
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline hidden-print'
		
		self.helper.layout = Layout(
			Field( 'competition', type='hidden' ),
			Field( 'event_type', type='hidden' ),
			Field( 'option_id', type='hidden' ),
			Field( 'rfid_option', type='hidden' ),
			Row(
				Col(Field('name', size=40), 4),
				Col(Field('date_time', size=24), 4),
				Col(Field('create_seeded_startlist', size=40), 4),
			),
			Row(
				Col(Field('optional'), 6),
				Col(Field('select_by_default'), 6),
			),
			Row( Field('road_race_finish_times') ),
			Row( Field('note', rows='4', cols='60') ),
		)
		self.additional_buttons = []
		if button_mask == EDIT_BUTTONS:
			self.additional_buttons.extend( [
				('new-wave-submit', _('New TT Wave'), 'btn btn-success', self.newWaveTTCB),
			] )
		addFormButtons( self, button_mask, self.additional_buttons, print_button = _('Print Waves') if button_mask == EDIT_BUTTONS else None )

@external_access
def EventTTDisplay( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return render_to_response( 'event_tt_list.html', RequestContext(request, locals()) )

@external_access
@user_passes_test( lambda u: u.is_superuser )
def EventTTNew( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = EventTTForm(request.POST, button_mask = NEW_BUTTONS)
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST or 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request, 'EventTTEdit', instance.id, cancelUrl=True) )
	else:
		instance = EventTT(
			competition = competition,
			date_time = datetime.datetime.combine( competition.start_date, datetime.time(10, 0, 0) ),
		)
		form = EventTTForm( instance = instance, button_mask = NEW_BUTTONS )
	
	return render_to_response( 'event_tt_form.html', RequestContext(request, locals()) )
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def EventTTEdit( request, eventTTId ):
	return GenericEdit( EventTT, request, eventTTId, EventTTForm,
		template = 'event_tt_form.html'
	)

@external_access
@user_passes_test( lambda u: u.is_superuser )
def EventTTDelete( request, eventTTId ):
	return GenericDelete( EventTT, request, eventTTId, EventTTForm,
		template = 'event_tt_form.html',
	)

@external_access
def EventTTCrossMgr( request, eventTTId ):
	eventTT = get_object_or_404( EventTT, pk=eventTTId )
	competition = eventTT.competition
	xl = get_crossmgr_excel_tt( eventTT )
	response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	response['Content-Disposition'] = 'attachment; filename={}-{}-{}.xlsx'.format(
		eventTT.date_time.strftime('%Y-%m-%d'),
		utils.removeDiacritic(competition.name),
		utils.removeDiacritic(eventTT.name),
	)
	return response

#--------------------------------------------------------------------------------------------

def GetWaveTTForm( event_tt, wave_tt = None ):
	@autostrip
	class WaveTTForm( ModelForm ):
		class Meta:
			model = WaveTT
			fields = '__all__'

		def __init__( self, *args, **kwargs ):
			button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
			
			super(WaveTTForm, self).__init__(*args, **kwargs)
			
			category_list = event_tt.get_categories_without_wave()
			
			if wave_tt is not None and wave_tt.pk is not None:
				category_list.extend( wave_tt.categories.all() )
				category_list.sort( key = lambda c: c.sequence )
				self.fields['distance'].label = u'{} ({})'.format( _('Distance'), wave_tt.distance_unit )
			
			categories_field = self.fields['categories']
			categories_field.choices = [(category.id, category.full_name()) for category in category_list]
			categories_field.label = _('Available Categories')
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'form-inline hidden-print'
			
			self.helper.layout = Layout(
				Field( 'event', type='hidden' ),
				Field( 'sequence', type='hidden' ),
				Row(
					Col(Field('name', size=40), 4),
				),
				Row(
					Col(Field('distance'), 3),
					Col(Field('laps'), 3),
				),
				Row(
					Col(Field('gap_before_wave'), 3),
					Col(Field('regular_start_gap'), 3),
					Col(Field('fastest_participants_start_gap'), 3),
					Col(Field('num_fastest_participants'), 3),
				),
				Row(
					Col(Field('sequence_option'), 6),
				),
				Row(
					Col(Field('categories', size=12, css_class='hidden-print'), 6),
				),
			)
			addFormButtons( self, button_mask )
			
	return WaveTTForm

@external_access
@user_passes_test( lambda u: u.is_superuser )
def WaveTTNew( request, eventTTId ):
	event_tt = get_object_or_404( EventTT, pk=eventTTId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
		form = GetWaveTTForm(event_tt)(request.POST, button_mask = NEW_BUTTONS)
		if form.is_valid():
			instance = form.save()
			
			if 'ok-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'cancelUrl'))
			if 'save-submit' in request.POST:
				return HttpResponseRedirect( pushUrl(request, 'WaveTTEdit', instance.id, cancelUrl = True) )
	else:
		wave_tt = WaveTT()
		wave_tt.event = event_tt
		wave_tts_existing = list( event_tt.wavett_set.all() )
		c = len( wave_tts_existing )
		wave_ttLetter = []
		while 1:
			wave_ttLetter.append( string.ascii_uppercase[c % 26] )
			c //= 26
			if c == 0:
				break
		wave_ttLetter.reverse()
		wave_ttLetter = ''.join( wave_ttLetter )
		wave_tt.name = _('WaveTT') + ' ' + wave_ttLetter
		if wave_tts_existing:
			wave_tt_last = wave_tts_existing[-1]
			wave_tt.distance = wave_tt_last.distance
			wave_tt.laps = wave_tt_last.laps
		form = GetWaveTTForm(event_tt, wave_tt)(instance=wave_tt, button_mask=NEW_BUTTONS)
	
	return render_to_response( 'wave_tt_form.html', RequestContext(request, locals()) )

@external_access
@user_passes_test( lambda u: u.is_superuser )
def WaveTTEdit( request, waveTTId ):
	wave_tt = get_object_or_404( WaveTT, pk=waveTTId )
	return GenericEdit( WaveTT, request, waveTTId, GetWaveTTForm(wave_tt.event, wave_tt),
		template = 'wave_tt_form.html'
	)
	
@external_access
@user_passes_test( lambda u: u.is_superuser )
def WaveTTDelete( request, waveTTId ):
	wave_tt = get_object_or_404( WaveTT, pk=waveTTId )
	return GenericDelete( WaveTT, request, waveTTId, GetWaveTTForm(wave_tt.event, wave_tt),
		template = 'wave_tt_form.html'
	)

@transaction.atomic
def WaveTTSwapAdjacent( waveTT, swapBefore ):
	NormalizeSequence( WaveTT.objects.filter(event=waveTT.event) )
	try:
		waveTTAdjacent = WaveTT.objects.get(event=waveTT.event, sequence=waveTT.sequence + (-1 if swapBefore else 1) )
	except WaveTT.DoesNotExist:
		return
		
	waveTTAdjacent.sequence, waveTT.sequence = waveTT.sequence, waveTTAdjacent.sequence
	waveTTAdjacent.save()
	waveTT.save()

@external_access
def WaveTTDown( request, waveTTId ):
	waveTT = get_object_or_404( WaveTT, pk=waveTTId )
	WaveTTSwapAdjacent( waveTT, False )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
@external_access
def WaveTTUp( request, waveTTId ):
	waveTT = get_object_or_404( WaveTT, pk=waveTTId )
	WaveTTSwapAdjacent( waveTT, True )
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#----------------------------------------------------------------------------

@autostrip
class ParticipantSearchForm( Form ):
	scan = forms.CharField( required=False, label = _('Scan Search'), help_text=_('Including License and RFID Tag') )
	name_text = forms.CharField( required=False, label = _('Name Text') )
	team_text = forms.CharField( required=False, label = _('Team Text') )
	bib = forms.IntegerField( required=False, min_value = -1 , label=_('Bib: (-1 to find NoBib)') )
	gender = forms.ChoiceField( required=False, choices = ((2, '----'), (0, _('Men')), (1, _('Women'))), initial = 2 )
	role_type = forms.ChoiceField( required=False, label = _('Role Type')  )
	category = forms.ChoiceField( required=False, label = _('Category') )
	
	city_text = forms.CharField( required=False, label = _('City') )
	state_prov_text = forms.CharField( required=False, label = _('State/Prov') )
	nationality_text = forms.CharField( required=False, label = _('Nationality') )
	
	confirmed = forms.ChoiceField( required=False, choices = ((2, '----'), (0, _('No')), (1, _('Yes'))) )
	paid = forms.ChoiceField( required=False, choices = ((2, '----'), (0, _('No')), (1, _('Yes'))) )
	
	def __init__(self, *args, **kwargs):
		competition = kwargs.pop( 'competition', None )
		super(ParticipantSearchForm, self).__init__(*args, **kwargs)
		
		if competition:
			self.fields['category'].choices = \
				[(-1, '----')] + [(-2, _('*** Missing ***'))] + [(category.id, category.code_gender) for category in competition.get_categories()]
		roleChoices = [(i, role) for i, role in enumerate(Participant.ROLE_NAMES)]
		roleChoices[0] = (0, '----')
		self.fields['role_type'].choices = roleChoices
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		button_args = [
			Submit( 'search-submit', _('Search'), css_class = 'btn btn-primary' ),
			Submit( 'clear-submit', _('Clear Search'), css_class = 'btn btn-primary' ),
			Submit( 'cancel-submit', _('OK'), css_class = 'btn btn-primary' ),
			Submit( 'export-excel-submit', _('Export to Excel'), css_class = 'btn btn-primary' ),
		]
		
		self.helper.layout = Layout(
			Row( Field('scan', size=20, autofocus=True ), ),
			Row( Field('name_text'), Field('team_text'), Field('bib'), Field('gender'), Field('role_type'), Field('category'), ),
			Row( Field('city_text'), Field('state_prov_text'), Field('nationality_text'), Field('confirmed'), Field('paid'), ),
			Row( *(button_args[:-1] + [HTML('&nbsp;'*8)] + button_args[-1:]) ),
		)

		
@external_access
def Participants( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	pfKey = 'participant_filter_{}'.format( competitionId )
	participant_filter = request.session.get(pfKey, {})
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'clear-submit' in request.POST:
			request.session[pfKey] = {}
			return HttpResponseRedirect(getContext(request,'path'))
			
		form = ParticipantSearchForm( request.POST, competition=competition )
		if form.is_valid():
			participant_filter = form.cleaned_data
			request.session[pfKey] = participant_filter
			
			participant_filter_no_scan = participant_filter.copy()
			participant_filter_no_scan.pop( 'scan' )
			request.session[pfKey] = participant_filter_no_scan
	else:
		form = ParticipantSearchForm( competition = competition, initial = participant_filter )
	
	#-----------------------------------------------------------------------------------------------
	
	participants = Participant.objects.filter( competition=competition )
	missing_category_count = Participant.objects.filter( competition=competition, role=Participant.Competitor, category__isnull=True ).count()
	missing_bib_count = Participant.objects.filter( competition=competition, role=Participant.Competitor, bib__isnull=True ).count()
	
	if participant_filter.get('scan',0):
		name_text = utils.normalizeSearch( participant_filter['scan'] )
		names = name_text.split()
		if names:
			q = Q()
			for n in names:
				q |= Q(license_holder__search_text__contains = n)
			participants = participants.filter( q ).select_related('team', 'license_holder')
			return render_to_response( 'participant_list.html', RequestContext(request, locals()) )
	
	if participant_filter.get('bib',None) is not None:
		bib = participant_filter['bib']
		if bib <= 0:
			participants = participants.filter( bib__isnull=True )
		else:
			participants = participants.filter( bib=bib )
	
	role_type = int(participant_filter.get('role_type',0))
	if role_type > 0:
		participants = participants.filter( role__range=(100*role_type, 100*role_type+99) )
	
	if 0 <= int(participant_filter.get('gender',-1)) <= 1:
		participants = participants.filter( license_holder__gender=participant_filter['gender'])
	
	category_id = int(participant_filter.get('category',-1))
	if category_id > 0:
		participants = participants.filter( category__id=category_id )
	elif category_id == -2:
		participants = participants.filter( category__isnull=True )
		
	if 0 <= int(participant_filter.get('confirmed',-1)) <= 1:
		participants = participants.filter( confirmed=bool(int(participant_filter['confirmed'])) )
	
	if 0 <= int(participant_filter.get('paid',-1)) <= 1:
		participants = participants.filter( paid=bool(int(participant_filter['paid'])) )
	
	participants = participants.select_related('team', 'license_holder')
	
	if participant_filter.get('name_text','').strip():
		name_text = utils.normalizeSearch( participant_filter['name_text'] )
		names = name_text.split()
		if names:
			participants = (p for p in participants
				if all(n in utils.removeDiacritic(p.license_holder.full_name()).lower() for n in names) )

	# Create a search function so we get a closure for the search text in the iterator.
	def search_license_holder( participants, search_text, field ):
		search_fields = utils.normalizeSearch( search_text ).split()
		if search_fields:
			return (p for p in participants if utils.matchSearchFields(search_fields, getattr(p.license_holder, field)) )
		else:
			return participants
		
	for field in ['city', 'state_prov', 'nationality']:
		search_field = field + '_text'
		if participant_filter.get(search_field,'').strip():
			participants = search_license_holder(
				participants,
				participant_filter[search_field],
				field
			)
	
	team_search = participant_filter.get('team_text','').strip()
	if team_search:
		participants = (p for p in participants if p.team and utils.matchSearchFields(team_search, p.team.search_text) )
	
	if request.method == 'POST' and 'export-excel-submit' in request.POST:
		xl = get_participant_excel( Q(pk__in=[p.pk for p in participants]) )
		response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
		response['Content-Disposition'] = 'attachment; filename=RaceDB-Participants-{}.xlsx'.format(
			datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S'),
		)
		return response
	
	return render_to_response( 'participant_list.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------------------------

@external_access
def ParticipantsInEvents( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	competition_events = sorted( competition.get_events(), key=lambda e: e.date_time )
	event_participants = {}
	for event in competition_events:
		p = event.get_participants()
		event_participants[event] = p
	
	participants = sorted( set.union(*[p for p in event_participants.itervalues()]), key=lambda p: p.license_holder.search_text )
	
	check_codes = {
		'optional_selected':	u"\u2611",
		'optional_deselected':	u"\u2610",
		'default_selected':		u"\u2713",
		'unavailable':			u"",
	}
	for participant in participants:
		event_status = []
		for event in competition_events:
			if participant in event_participants[event]:
				event_status.append( check_codes['optional_selected' if event.optional else 'default_selected'] )
			elif event.optional:
				event_status.append( check_codes['optional_deselected'] )
			else:
				event_status.append( check_codes['unavailable'] )
		participant.event_status = event_status
	
	return render_to_response( 'participants_in_events.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------------------------

@external_access
def ParticipantManualAdd( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	search_text = request.session.get('participant_new_filter', '')
	btns = [('new-submit', 'New License Holder', 'btn btn-success')]
	if request.method == 'POST':
	
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'LicenseHolderNew') )
			
		form = SearchForm( btns, request.POST, hide_cancel_button=True )
		if form.is_valid():
			search_text = form.cleaned_data['search_text']
			request.session['participant_new_filter'] = search_text
	else:
		form = SearchForm( btns, initial = {'search_text': search_text}, hide_cancel_button=True )
	
	search_text = utils.normalizeSearch( search_text )
	q = Q( active = True )
	for term in search_text.split():
		q &= Q(search_text__contains = term)
	license_holders = LicenseHolder.objects.filter(q).order_by('search_text')[:MaxReturn]
	
	# Flag which license_holders are already entered in this competition.
	license_holders_in_competition = set( p.license_holder.id
		for p in Participant.objects.select_related('license_holder').filter(competition=competition) )
	
	add_multiple_categories = request.user.is_superuser or SystemInfo.get_singleton().reg_allow_add_multiple_categories
	return render_to_response( 'participant_add_list.html', RequestContext(request, locals()) )

@external_access
def ParticipantAddToCompetition( request, competitionId, licenseHolderId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	
	participant = Participant( competition=competition, license_holder=license_holder, preregistered=False ).init_default_values().auto_confirm()
	
	try:
		# Fails if the license_holder is non-unique.
		participant.save()
		participant.add_to_default_optional_events()
	except IntegrityError as e:
		# Recover silently by going directly to edit screen with the existing participant.
		participant = Participant.objects.filter( competition=competition, license_holder=license_holder ).first()
		
	return HttpResponseRedirect('{}ParticipantEdit/{}/'.format(getContext(request,'pop2Url'), participant.id))

@external_access
def ParticipantAddToCompetitionDifferentCategory( request, competitionId, licenseHolderId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	
	participant = Participant.objects.filter( competition=competition, license_holder=license_holder, category__isnull=True ).first()
	if participant:
		return HttpResponseRedirect('{}ParticipantEdit/{}/'.format(getContext(request,'pop2Url'), participant.id))
	
	participant = Participant.objects.filter( competition=competition, license_holder=license_holder ).first()
	if participant:
		participant.id = None
		participant.category = None
		participant.role = Participant.Competitor
		participant.bib = None
		participant.save()
		return HttpResponseRedirect('{}ParticipantEdit/{}/'.format(getContext(request,'pop2Url'), participant.id))
	
	return ParticipantAddToCompetition( request, competitionId, licenseHolderId )

@external_access
def ParticipantAddToCompetitionDifferentCategoryConfirm( request, competitionId, licenseHolderId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	competition_age = competition.competition_age( license_holder )
	
	participant = Participant.objects.filter( competition=competition, license_holder=license_holder, category__isnull=True ).first()
	if participant:
		return HttpResponseRedirect('{}ParticipantEdit/{}/'.format(getContext(request,'pop2Url'), participant.id))
	
	return render_to_response( 'participant_add_to_category_confirm.html', RequestContext(request, locals()) )

@external_access
def ParticipantEdit( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	system_info = SystemInfo.get_singleton()
	add_multiple_categories = request.user.is_superuser or SystemInfo.get_singleton().reg_allow_add_multiple_categories
	competition_age = participant.competition.competition_age( participant.license_holder )
	is_suspicious_age = not (8 <= competition_age <= 90)
	isEdit = True
	rfid_antenna = int(request.session.get('rfid_antenna', 0))
	return render_to_response( 'participant_form.html', RequestContext(request, locals()) )
	
@external_access
def ParticipantEditFromLicenseHolder( request, competitionId, licenseHolderId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	participant = Participant.objects.filter(competition=competition, license_holder=license_holder).first()
	if not participant:
		return ParticipantAddToCompetition( request, competitionId, licenseHolderId )
	return ParticipantEdit( request, participant.id )
	
@external_access
def ParticipantRemove( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	add_multiple_categories = request.user.is_superuser or SystemInfo.get_singleton().reg_allow_add_multiple_categories
	competition_age = participant.competition.competition_age( participant.license_holder )
	is_suspicious_age = not (8 <= competition_age <= 90)
	isEdit = False
	return render_to_response( 'participant_form.html', RequestContext(request, locals()) )
	
@external_access
def ParticipantDoDelete( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	participant.delete()
	return HttpResponseRedirect( getContext(request,'cancelUrl') )
	
@autostrip
class ParticipantCategorySelectForm( Form ):
	gender = forms.ChoiceField( choices = (
									(-1, _('All')),
									(0, _('Men / Open')),
									(1, _('Women / Open')),
									(2, _('Open')),
								),
								initial = -1 )
	
	def __init__(self, *args, **kwargs):
		super(ParticipantCategorySelectForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'navbar-form navbar-left'
		
		button_args = [
			Submit( 'search-submit', _('Search'), css_class = 'btn btn-primary' ),
			Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
		]
		
		self.helper.layout = Layout(
			HTML( u'{}:&nbsp;&nbsp;&nbsp;&nbsp;'.format( _("Search") ) ),
			Div( Field('gender', css_class = 'form-control'), css_class = 'form-group' ),
			HTML( u'&nbsp;&nbsp;&nbsp;&nbsp;' ),
			button_args[0],
			button_args[1],
		)

@external_access
def ParticipantCategoryChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	license_holder = participant.license_holder
	competition_age = competition.competition_age( license_holder )
	
	gender = None
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
		
		form = ParticipantCategorySelectForm( request.POST )
		if form.is_valid():
			gender = int(form.cleaned_data['gender'])
	else:
		gender = license_holder.gender
		form = ParticipantCategorySelectForm( initial = dict(gender=gender) )
	
	categories = Category.objects.filter( format=competition.category_format )
	if gender == None:
		gender = license_holder.gender
	if gender != -1:
		categories = categories.filter( Q(gender=2) | Q(gender=gender) )
	available_categories = set( competition.get_available_categories(license_holder, gender=gender, participant_exclude=participant) )
	return render_to_response( 'participant_category_select.html', RequestContext(request, locals()) )	

@external_access
def ParticipantCategorySelect( request, participantId, categoryId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	if int(categoryId):
		category = get_object_or_404( Category, pk=categoryId )
	else:
		category = None
	
	categories = set()
	for p in Participant.objects.filter(competition=competition, license_holder=participant.license_holder):
		if p != participant and participant.category:
			categories.add( participant.category )
	if competition.is_category_conflict(categories):
		has_error, conflict_explanation, conflict_participant = True, _('Cannot assign to another Category that already exists in an Event.'), None
		return render_to_response( 'participant_integrity_error.html', RequestContext(request, locals()) )
	
	category_changed = (participant.category != category)
	participant.category = category
	if category and participant.role != Participant.Competitor:
		participant.role = Participant.Competitor
	
	participant.update_bib_new_category()
	
	try:
		participant.auto_confirm().save()
	except IntegrityError:
		has_error, conflict_explanation, conflict_participant = participant.explain_integrity_error()
		return render_to_response( 'participant_integrity_error.html', RequestContext(request, locals()) )

	if category_changed:
		participant.add_to_default_optional_events()
	return HttpResponseRedirect(getContext(request,'pop2Url'))

#--------------------------------------------------------------------------
@external_access
def ParticipantRoleChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	return render_to_response( 'participant_role_select.html', RequestContext(request, locals()) )

@external_access
def ParticipantRoleSelect( request, participantId, role ):
	participant = get_object_or_404( Participant, pk=participantId )
	participant.role = int(role)
	if participant.role != Participant.Competitor:
		participant.bib = None
		participant.category = None
		if participant.role >= 200:			# Remove team for non-team roles.
			participant.team = None
	else:
		participant.init_default_values()
	participant.auto_confirm().save()
	return HttpResponseRedirect(getContext(request,'pop2Url'))
	
#--------------------------------------------------------------------------
@external_access
def ParticipantBooleanChange( request, participantId, field ):
	participant = get_object_or_404( Participant, pk=participantId )
	setattr( participant, field, not getattr(participant, field) )
	if field != 'confirmed':
		participant.auto_confirm()
	participant.save()
	return HttpResponseRedirect(getContext(request,'cancelUrl'))
	
#--------------------------------------------------------------------------

@external_access
def ParticipantTeamChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	search_text = request.session.get('teams_filter', '')
	btns = [('new-submit', _('New Team'), 'btn btn-success')]
	if request.method == 'POST':
	
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		if 'new-submit' in request.POST:
			return HttpResponseRedirect( pushUrl(request,'TeamNew') )
			
		form = SearchForm( btns, request.POST )
		if form.is_valid():
			search_text = form.cleaned_data['search_text']
			request.session['teams_filter'] = search_text
	else:
		form = SearchForm( btns, initial = {'search_text': search_text} )
		
	search_text = utils.normalizeSearch(search_text)
	q = Q()
	for n in search_text.split():
		q &= Q( search_text__contains = n )
	teams = Team.objects.filter(q)[:MaxReturn]
	return render_to_response( 'participant_team_select.html', RequestContext(request, locals()) )
	
@external_access
def ParticipantTeamSelect( request, participantId, teamId ):
	participant = get_object_or_404( Participant, pk=participantId )
	if int(teamId):
		team = get_object_or_404( Team, pk=teamId )
	else:
		team = None
	participant.team = team
	participant.auto_confirm().save()
	return HttpResponseRedirect(getContext(request,'pop2Url'))

#--------------------------------------------------------------------------
class Bib( object ):
	def __init__( self, bib, license_holder = None ):
		self.bib = bib
		self.license_holder = license_holder
		self.full_name = license_holder.full_name() if license_holder else ''

@external_access
def ParticipantBibChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	if not participant.category:
		return HttpResponseRedirect(getContext(request,'cancelUrl'))
	competition = participant.competition
	
	# Find the available category numbers.
	participants = Participant.objects.filter( competition=competition )
	
	category_numbers = competition.get_category_numbers( participant.category )
	if category_numbers:
		participants.filter( category__in=category_numbers.categories.all() )
	participants.select_related('license_holder')
	allocated_numbers = { p.bib: p.license_holder for p in participants }
	
	# Exclude existing bib numbers of all license holders if using existing bibs.  We don't know whether the existing license holders will show up.
	if competition.number_set:
		allocated_numbers.update( { nse.bib:nse.license_holder
			for nse in NumberSetEntry.objects.select_related('license_holder').filter( number_set=competition.number_set, date_lost=None ) } )
	
	if category_numbers:
		available_numbers = sorted( category_numbers.get_numbers() )
		category_numbers_defined = True
	else:
		available_numbers = []
		category_numbers_defined = False
	
	bibs = [Bib(n, allocated_numbers.get(n, None)) for n in available_numbers]
	del available_numbers
	del allocated_numbers
	
	if participant.category:
		bib_participants = { p.bib:p
			for p in Participant.objects.filter(competition=competition, category=participant.category).exclude(bib__isnull=True)
		}
		for b in bibs:
			try:
				b.full_name = bib_participants[b.bib].full_name_team
			except:
				pass
	
	has_existing_number_set_bib = (
		competition.number_set and
		participant.bib == competition.number_set.get_bib( competition, participant.license_holder, participant.category )
	)
	return render_to_response( 'participant_bib_select.html', RequestContext(request, locals()) )
	
@external_access
def ParticipantBibSelect( request, participantId, bib ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	
	bib_save = participant.bib
	
	bib = int(bib )
	if participant.category and bib > 0:
		participant.bib = bib
		bib_conflicts = participant.get_bib_conflicts()
		if bib_conflicts:
			participant.bib = bib_save
			return HttpResponseRedirect(getContext(request,'popUrl'))		# Show the select screen again.
		
		if competition.number_set:
			if bib_save is not None and bib_save != bib:
				competition.number_set.set_lost( bib_save )
	else:
		if competition.number_set and bib_save is not None:
			competition.number_set.return_to_pool( bib_save )
			participant.bib = None
	
	try:
		participant.auto_confirm().save()
	except IntegrityError as error:
		return HttpResponseRedirect(getContext(request,'popUrl'))			# Show the select screen again.
	
	return HttpResponseRedirect(getContext(request,'pop2Url'))

#--------------------------------------------------------------------------
@autostrip
class ParticipantNoteForm( Form ):
	note = forms.CharField( widget = forms.Textarea, required = False, label = _('Note') )
	
	def __init__(self, *args, **kwargs):
		super(ParticipantNoteForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'navbar-form navbar-left'
		
		button_args = [
			Submit( 'ok-submit', _('OK'), css_class = 'btn btn-primary' ),
			Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
		]
		
		self.helper.layout = Layout(
			Row(
				Field('note', css_class = 'form-control', cols = '60'),
			),
			Row(
				button_args[0],
				button_args[1],
			)
		)
		
@external_access
def ParticipantNoteChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	license_holder = participant.license_holder
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = ParticipantNoteForm( request.POST )
		if form.is_valid():
			note = form.cleaned_data['note']
			participant.note = note
			participant.auto_confirm().save()
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = ParticipantNoteForm( initial = dict(note = participant.note) )
		
	return render_to_response( 'participant_note_change.html', RequestContext(request, locals()) )

@external_access
def ParticipantGeneralNoteChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	license_holder = participant.license_holder
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = ParticipantNoteForm( request.POST )
		if form.is_valid():
			note = form.cleaned_data['note']
			license_holder.note = note
			license_holder.save()
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = ParticipantNoteForm( initial = dict(note = license_holder.note) )
		
	return render_to_response( 'participant_note_change.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------
def GetParticipantOptionForm( participation_optional_events ):
	choices = [(event.option_id, u'{} ({})'.format(event.name, event.get_event_type_display()))
					for event, is_participating in participation_optional_events]
	
	@autostrip
	class ParticipantOptionForm( Form ):
		options = forms.MultipleChoiceField( required = False, label = _('Optional Events'), choices=choices )
		
		def __init__(self, *args, **kwargs):
			super(ParticipantOptionForm, self).__init__(*args, **kwargs)
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'navbar-form navbar-left'
			
			button_args = [
				Submit( 'ok-submit', _('OK'), css_class = 'btn btn-primary' ),
				Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
			]
			
			self.helper.layout = Layout(
				Row(
					Field('options', css_class = 'form-control', size = '20'),
				),
				Row(
					button_args[0],
					button_args[1],
				)
			)
	
	return ParticipantOptionForm
		
@external_access
def ParticipantOptionChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	license_holder = participant.license_holder
	
	participation_events = participant.get_participant_events()
	participation_optional_events = [(event, is_participating) for event, optional, is_participating in participation_events if optional]
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = GetParticipantOptionForm( participation_optional_events )( request.POST )
		if form.is_valid():
			options = form.cleaned_data['options']
			ParticipantOption.set_option_ids( participant, options )
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = GetParticipantOptionForm( participation_optional_events )(
			initial = dict(options = [event.option_id for event, is_participating in participation_optional_events if is_participating])
		)
		
	return render_to_response( 'participant_option_change.html', RequestContext(request, locals()) )
#--------------------------------------------------------------------------
def GetParticipantEstSpeedForm( competition ):
	@autostrip
	class ParticipantEstSpeedForm( Form ):
		est_speed = forms.FloatField( required = False, label=_('Estimated Speed for Time Trial') )
		seed_early = forms.BooleanField( required = False, label=_('Seed Early'), help_text=_('Tells RaceDB to start this rider as early as possible in the Start Wave') )
		
		def __init__(self, *args, **kwargs):
			super(ParticipantEstSpeedForm, self).__init__(*args, **kwargs)
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'navbar-form navbar-left'
			
			button_args = [
				Submit( 'ok-submit', _('OK'), css_class = 'btn btn-primary' ),
				Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
			]
			
			self.helper.layout = Layout(
				Row(
					Col(Field('est_speed', css_class = 'form-control', size = '20'), 2),
					Col(HTML( competition.speed_unit_display ), 2),
				),
				Row(
					Field('seed_early'),
				),
				Row(
					button_args[0],
					button_args[1],
				)
			)
	
	return ParticipantEstSpeedForm
		
@external_access
def ParticipantEstSpeedChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	license_holder = participant.license_holder
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = GetParticipantEstSpeedForm(competition)( request.POST )
		if form.is_valid():
			est_speed = form.cleaned_data['est_speed']
			participant.est_kmh = competition.to_kmh( est_speed or 0.0 )
			participant.seed_early = form.cleaned_data['seed_early']
			participant.save()
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = GetParticipantEstSpeedForm(competition)(
			initial = dict( est_speed=competition.to_local_speed(participant.est_kmh), seed_early=participant.seed_early )
		)
	
	speed_rc = {}
	if competition.distance_unit == 0:
		for col, kmh in enumerate(xrange(20, 51)):
			for row, decimal in enumerate(xrange(0, 10)):
				speed_rc[(col, row)] = u'{}.{:01d}'.format(kmh, decimal)
	else:
		for col, mph in enumerate(xrange(12, 32)):
			for row, decimal in enumerate(xrange(0, 10)):
				speed_rc[(col, row)] = u'{}.{:01d}'.format(mph, decimal)
	
	row_max = max( row for row, col in speed_rc.iterkeys() ) + 1
	col_max = max( col for row, col in speed_rc.iterkeys() ) + 1
	
	speed_table = [ [ speed_rc[(row, col)] for col in xrange(col_max) ] for row in xrange(row_max) ]
	speed_table.reverse()
	
	return render_to_response( 'participant_est_speed_change.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------

@autostrip
class ParticipantTagForm( Form ):
	tag = forms.CharField( required = False, label = _('Tag') )
	make_this_existing_tag = forms.BooleanField( required = False, label = _('Rider keeps tag for other races') )
	rfid_antenna = forms.ChoiceField( choices = ((0,_('None')), (1,'1'), (2,'2'), (3,'3'), (4,'4') ), label = _('RFID Antenna to Write Tag') )
	
	def __init__(self, *args, **kwargs):
		super(ParticipantTagForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'navbar-form navbar-left'
		
		button_args = [
			Submit( 'ok-submit', _('Update Tag in Database'), css_class = 'btn btn-primary' ),
			Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning' ),
			Submit( 'auto-generate-tag-submit', _('Auto Generate Tag Only - Do Not Write'), css_class = 'btn btn-primary' ),
			Submit( 'write-tag-submit', _('Write Existing Tag'), css_class = 'btn btn-primary' ),
			Submit( 'auto-generate-and-write-tag-submit', _('Auto Generate and Write Tag'), css_class='btn btn-success' ),
		]
		
		self.helper.layout = Layout(
			Row(
				Col( Field('tag', rows='2', cols='60'), 4 ),
				Col( Field('make_this_existing_tag'), 4 ),
				Col( Field('rfid_antenna'), 4 ),
			),
			HTML( '<br/>' ),
			Row(
				button_args[4],
				HTML( '&nbsp;' * 5 ),
				button_args[3],
			),
			HTML( '<br/>' * 2 ),
			Row(
				button_args[2],
			),
			HTML( '<br/>' * 2 ),
			Row( 
				button_args[0],
				HTML( '&nbsp;' * 5 ),
				button_args[1],
			),
		)

@external_access
def ParticipantTagChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	competition = participant.competition
	license_holder = participant.license_holder
	rfid_antenna = int(request.session.get('rfid_antenna', 0))
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
		
		form = ParticipantTagForm( request.POST )
		if form.is_valid():
			status = True
			status_entries = []

			tag = form.cleaned_data['tag'].strip().upper()
			make_this_existing_tag = form.cleaned_data['make_this_existing_tag']
			rfid_antenna = request.session['rfid_antenna'] = int(form.cleaned_data['rfid_antenna'])
			
			if 'auto-generate-tag-submit' in request.POST or 'auto-generate-and-write-tag-submit' in request.POST:
				tag = license_holder.get_unique_tag()
				
			if not tag:
				status = False
				status_entries.append(
					(_('Empty Tag'), (
						_('Cannot write an empty Tag to the Database.'),
						_('Please specify a Tag or press Cancel.'),
					)),
				)
			elif not utils.allHex(tag):
				status = False
				status_entries.append(
					(_('Non-Hex Characters in Tag'), (
						_('All Tag characters must be hexadecimal ("0123456789ABCDEF").'),
						_('Please change the Tag to all hexadecimal.'),
					)),
				)
			if not status:
				return render_to_response( 'rfid_write_status.html', RequestContext(request, locals()) )
			
			participant.tag = tag
			try:
				participant.auto_confirm().save()
			except IntegrityError as e:
				# Report the error - probably a non-unique field.
				has_error, conflict_explanation, conflict_participant = participant.explain_integrity_error()
				status = False
				status_entries.append(
					(_('Participant Save Failure'), (
						u'{}'.format(e),
					)),
				)
				return render_to_response( 'rfid_write_status.html', RequestContext(request, locals()) )
			
			if make_this_existing_tag:
				license_holder.existing_tag = tag
				try:
					license_holder.save()
				except Exception as e:
					# Report the error - probably a non-unique field.
					status = False
					status_entries.append(
						(string_concat(_('LicenseHolder'), u': ', _('Existing Tag Save Exception:')), (
							unicode(e),
						)),
					)
					return render_to_response( 'rfid_write_status.html', RequestContext(request, locals()) )
			
			if 'write-tag-submit' in request.POST or 'auto-generate-and-write-tag-submit' in request.POST:
				if not rfid_antenna:
					status = False
					status_entries.append(
						(_('RFID Antenna Configuration'), (
							_('RFID Antenna for Tag Write must be specified.'),
							_('Please specify the RFID Antenna.'),
						)),
					)
				
				if status:
					status, response = WriteTag(tag, rfid_antenna)
					if not status:
						status_entries = [
							(_('Tag Write Failure'), response.get('errors',[]) ),
						]
				
				if not status:
					return render_to_response( 'rfid_write_status.html', RequestContext(request, locals()) )
				# if status: fall through to ok-submit case.
			
			# ok-submit
			if 'auto-generate-tag-submit' in request.POST:
				return HttpResponseRedirect(getContext(request,'path'))
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = ParticipantTagForm( initial = dict(tag=participant.tag, rfid_antenna=rfid_antenna, make_this_existing_tag=competition.use_existing_tags) )
		
	return render_to_response( 'participant_tag_change.html', RequestContext(request, locals()) )
	
#--------------------------------------------------------------------------
@autostrip
class ParticipantSignatureForm( Form ):
	signature = forms.CharField( required = False, label = _('Signature') )
	
	def __init__(self, *args, **kwargs):
		is_jsignature = kwargs.pop( 'is_jsignature', True )
		super(ParticipantSignatureForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_id = 'id_signature_form'
		self.helper.form_class = 'navbar-form navbar-left'
		
		if is_jsignature:
			button_args = [
				Submit( 'ok-submit', ('&nbsp;'*10) + unicode(_('OK')) + ('&nbsp;'*10), css_class = 'btn btn-success', style='font-size:200%' ),
				Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning hidden-print', style='font-size:200%' ),
				HTML(u'<button class="btn btn-warning hidden-print" onClick="reset_signature()">{}</button>'.format(_('Reset'))),
			]
		else:
			button_args = [
				HTML('&nbsp;'*24),
				Submit( 'cancel-submit', _('Cancel'), css_class = 'btn btn-warning hidden-print', style='font-size:150%' )
			]
		
		if is_jsignature:
			self.helper.layout = Layout(
				Container(
					Row( Col(Field('signature'), 12) ),
					Row(
						Col(button_args[0],4),
						Col(button_args[1],4),
						Col(button_args[2],4),
					),
				)
			)
		else:
			self.helper.layout = Layout(
				Container(
					Row( Col( Field( 'signature' ), 12) ),
					Row( Div( Div(*button_args, css_class='row'), css_class='col-md-12 text-center' ) ),
				)
			)

@external_access
def ParticipantSignatureChange( request, participantId ):
	participant = get_object_or_404( Participant, pk=participantId )
	signature_with_touch_screen = int(request.session.get('signature_with_touch_screen', True))
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = ParticipantSignatureForm( request.POST, is_jsignature=signature_with_touch_screen )
		if form.is_valid():
			signature = form.cleaned_data['signature']
			signature = signature.strip()
			if not signature:
				return HttpResponseRedirect(getContext(request,'path'))
				
			participant.signature = signature
			participant.auto_confirm().save()
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
	else:
		form = ParticipantSignatureForm( is_jsignature=signature_with_touch_screen )
	
	if signature_with_touch_screen:
		return render_to_response( 'participant_jsignature_change.html', RequestContext(request, locals()) )
	else:
		return render_to_response( 'participant_signature_change.html', RequestContext(request, locals()) )
	
@external_access
def SetSignatureWithTouchScreen( request, use_touch_screen ):
	request.session['signature_with_touch_screen'] = bool(int(use_touch_screen))
	return HttpResponseRedirect(getContext(request,'cancelUrl'))

#--------------------------------------------------------------------------
@external_access
def ParticipantBarcodeAdd( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = BarcodeScanForm( request.POST, hide_cancel_button=True )
		if form.is_valid():
			scan = form.cleaned_data['scan'].strip()
			if not scan:
				return HttpResponseRedirect(getContext(request,'path'))
				
			license_holder, participants = participant_key_filter( competition, scan, False )
			if not license_holder:
				return render_to_response( 'participant_scan_error.html', RequestContext(request, locals()) )
			
			if len(participants) == 1:
				return HttpResponseRedirect(pushUrl(request,'ParticipantEdit',participants[0].id))
			if len(participants) > 1:
				return render_to_response( 'participant_scan_error.html', RequestContext(request, locals()) )
			
			return HttpResponseRedirect(pushUrl(request,'LicenseHolderAddConfirm', competition.id, license_holder.id))
	else:
		form = BarcodeScanForm( hide_cancel_button=True )
		
	return render_to_response( 'participant_scan_form.html', RequestContext(request, locals()) )
	
@external_access
def ParticipantNotFound( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return render_to_response( 'participant_not_found.html', RequestContext(request, locals()) )
	
@external_access
def ParticipantMultiFound( request, competitionId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	return render_to_response( 'participant_multi_found.html', RequestContext(request, locals()) )
	
#--------------------------------------------------------------------------
@external_access
def ParticipantRfidAdd( request, competitionId, autoSubmit=False ):
	competition = get_object_or_404( Competition, pk=competitionId )
	rfid_antenna = int(request.session.get('rfid_antenna', 0))
	
	status = True
	status_entries = []
	tag = None
	tags = []
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
		
		form = RfidScanForm( request.POST, hide_cancel_button=True )
		if form.is_valid():
		
			request.session['rfid_antenna'] = rfid_antenna = int(form.cleaned_data['rfid_antenna'])
			
			if not rfid_antenna:
				status = False
				status_entries.append(
					(_('RFID Antenna Configuration'), (
						_('RFID Antenna for Tag Read must be specified.'),
						_('Please specify the RFID Antenna.'),
					)),
				)
			else:
				status, response = ReadTag(rfid_antenna)
				# DEBUG DEBUG
				#status, response = True, {'tags': ['A7A2102303']}
				if not status:
					status_entries.append(
						(_('Tag Read Failure'), response.get('errors',[]) ),
					)
				else:
					tags = response.get('tags', [])
					try:
						tag = tags[0]
					except (AttributeError, IndexError) as e:
						status = False
						status_entries.append(
							(_('Tag Read Failure'), [e] ),
						)
				
				if tag and len(tags) > 1:
					status = False
					status_entries.append(
						(_('Multiple Tags Read'), tags ),
					)
			
			if not status:
				return render_to_response( 'participant_scan_rfid.html', RequestContext(request, locals()) )
				
			license_holder, participants = participant_key_filter( competition, tag, False )
			if not license_holder:
				return render_to_response( 'participant_scan_error.html', RequestContext(request, locals()) )
			
			if len(participants) == 1:
				return HttpResponseRedirect(pushUrl(request,'ParticipantEdit',participants[0].id))
			if len(participants) > 1:
				return render_to_response( 'participant_scan_error.html', RequestContext(request, locals()) )
			
			return HttpResponseRedirect(pushUrl(request,'LicenseHolderAddConfirm', competition.id, license_holder.id))
	else:
		form = RfidScanForm( initial=dict(rfid_antenna=rfid_antenna), hide_cancel_button=True )
		
	return render_to_response( 'participant_scan_rfid.html', RequestContext(request, locals()) )

@external_access
def LicenseHolderAddConfirm( request, competitionId, licenseHolderId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	competition_age = competition.competition_age( license_holder )
	return render_to_response( 'license_holder_add_confirm.html', RequestContext(request, locals()) )

@external_access
def LicenseHolderConfirmAddToCompetition( request, competitionId, licenseHolderId ):
	competition = get_object_or_404( Competition, pk=competitionId )
	license_holder = get_object_or_404( LicenseHolder, pk=licenseHolderId )
	
	# Try to create a new participant from the license_holder.
	participant = Participant( competition=competition, license_holder=license_holder, preregistered=False ).init_default_values()
	try:
		participant.auto_confirm().save()
		participant.add_to_default_optional_events()
		return HttpResponseRedirect(pushUrl(request, 'ParticipantEdit', participant.id, cancelUrl=True))
	except IntegrityError as e:
		# If this participant exists already, recover silently by going directly to the existing participant.
		participant = Participant.objects.filter(competition=competition, license_holder=license_holder).first()
		return HttpResponseRedirect(pushUrl(request, 'ParticipantEdit', participant.id, cancelUrl=True))

#--------------------------------------------------------------------------
@autostrip
class SystemInfoForm( ModelForm ):
	class Meta:
		model = SystemInfo
		fields = '__all__'
		
	def __init__( self, *args, **kwargs ):
		button_mask = kwargs.pop('button_mask', EDIT_BUTTONS)
		super(SystemInfoForm, self).__init__(*args, **kwargs)
		
		self.helper = FormHelper( self )
		self.helper.form_action = '.'
		self.helper.form_class = 'form-inline'
		
		self.helper.layout = Layout(
			Row(
				Col(Field('tag_template', size=24), 6),
			),
			HTML( '<hr/>' ),
			Row(
				Col(Field('tag_from_license'), 4),
				Col(Field('tag_from_license_id'), 4),
			),
			HTML( '<hr/>' ),
			Row(
				Col(Field('reg_closure_minutes', size=6), 6),
			),
			Row(
				Col(Field('exclude_empty_categories', size=6), 6),
			),
			Row(
				Col(Field('reg_allow_add_multiple_categories', size=6), 6),
			),
			HTML( '<hr/>' ),
			Field( 'rfid_server_host', type='hidden' ),
			Field( 'rfid_server_port', type='hidden' ),
		)
		addFormButtons( self, button_mask )
		
@external_access
@user_passes_test( lambda u: u.is_superuser )
def SystemInfoEdit( request ):
	return GenericEdit( SystemInfo, request, SystemInfo.get_singleton().id, SystemInfoForm )
	
#--------------------------------------------------------------------------
def get_year_choices():
	year_choices = [
		(y, u'{}'.format(y))
			for y in sorted(
			set(d.year for d in Competition.objects.all()
				.order_by('-start_date')
				.values_list('start_date', flat=True)), reverse=True
			)
	][:20] + [(-1, _('All'))]
	return year_choices
	
def get_search_start_date():
	last_competition = Competition.objects.all().order_by('-start_date').first()
	return datetime.date( last_competition.start_date.year, 1, 1 ) if last_competition else None
	
def get_search_end_date():
	last_competition = Competition.objects.all().order_by('-start_date').first()
	return datetime.date( last_competition.start_date.year, 12, 31 ) if last_competition else None
	
def get_organizer_choices():
	return [(v, v) for v in sorted( set(Competition.objects.all().values_list('organizer', flat=True) ) )]

def get_discipline_choices():
	return [(id, name) for seq, id, name in sorted( set(Competition.objects.all().values_list(
		'discipline__sequence','discipline__id', 'discipline__name')
	) )]

def get_race_class_choices():
	return [(id, name) for seq, id, name in sorted( set(Competition.objects.all().values_list(
		'race_class__sequence','race_class__id', 'race_class__name')
	) )]

def get_participant_report_form():
	@autostrip
	class ParticipantReportForm( Form ):
		start_date = forms.DateField( required = False, label = _('Start Date') )
		end_date = forms.DateField( required = False, label = _('End Date')  )
		race_classes = forms.MultipleChoiceField( required = False, label = _('Race Classes'), choices = get_race_class_choices() )
		disciplines = forms.MultipleChoiceField( required = False, label = _('Disciplines'), choices = get_discipline_choices() )
		organizers = forms.MultipleChoiceField( required = False, label = _('Organizers'), choices = get_organizer_choices() )
		include_labels = forms.MultipleChoiceField( required = False, label = _('Include Labels'), choices = [(r.pk, r.name) for r in ReportLabel.objects.all()] )
		exclude_labels = forms.MultipleChoiceField( required = False, label = _('Exclude Labels'), choices = [(r.pk, r.name) for r in ReportLabel.objects.all()] )
		
		def __init__( self, *args, **kwargs ):
			super(ParticipantReportForm, self).__init__(*args, **kwargs)
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'form-inline'
			
			self.helper.layout = Layout(
				Row(
					Div(
						Row( Field('start_date') ),
						Row( Field('end_date') ),
						css_class = 'col-md-3'
					),
					Div(
						Row(
							Field('race_classes', id='focus', size=10),
							Field('disciplines', size=10),
							Field('organizers', size=10),
							Field('include_labels', size=10),
							Field('exclude_labels', size=10),
						),
						Row( HTML( _('Ctrl-Click to Multi-Select') ) ),
						css_class = 'col-md-9',
					)
				),
				HTML( '<hr/>' ),
			)
			addFormButtons( self, OK_BUTTON | CANCEL_BUTTON )
	
		def clean(self):
			cleaned_data = super(ParticipantReportForm, self).clean()
			start_date = cleaned_data.get("start_date")
			end_date = cleaned_data.get("end_date")

			if start_date and end_date:
				if start_date > end_date:
					raise forms.ValidationError( _("Start Date must be less than End Date") )
	
	return ParticipantReportForm

@external_access
@user_passes_test( lambda u: u.is_superuser )
def ParticipantReport( request ):
	start_date, end_date, discipline, race_classes = get_search_start_date(), get_search_end_date(), None, None
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = get_participant_report_form()( request.POST )
		start_date, end_date, discipline, race_classes = None, None, None, None
		if form.is_valid():
			start_date = form.cleaned_data['start_date']
			end_date = form.cleaned_data['end_date']
			disciplines = form.cleaned_data['disciplines']
			race_classes = form.cleaned_data['race_classes']
			organizers = form.cleaned_data['organizers']
			include_labels = form.cleaned_data['include_labels']
			exclude_labels = form.cleaned_data['exclude_labels']
			
		sheet_name, xl = participation_excel( start_date, end_date, disciplines, race_classes, organizers, include_labels, exclude_labels )
		response = HttpResponse(xl, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
		response['Content-Disposition'] = 'attachment; filename={}.xlsx'.format(sheet_name)
		return response
	else:
		form = get_participant_report_form()( initial={'start_date':start_date, 'end_date':end_date} )
		
	return render_to_response( 'generic_form.html', RequestContext(request, locals()) )

#--------------------------------------------------------------------------
@external_access
@user_passes_test( lambda u: u.is_superuser )
def AttendanceAnalytics( request ):
	initial = request.session.get( 'attendance_analytics', {
			'start_date':get_search_start_date(),
			'end_date':get_search_end_date(),
			'disciplines':[],
			'race_classes':[],
			'organizers':[],
		}
	)
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = get_participant_report_form()( request.POST )
		if form.is_valid():
			initial = {
				'start_date':form.cleaned_data['start_date'],
				'end_date':form.cleaned_data['end_date'],
				'disciplines':form.cleaned_data['disciplines'],
				'race_classes':form.cleaned_data['race_classes'],
				'organizers':form.cleaned_data['organizers'],
				'include_labels':form.cleaned_data['include_labels'],
				'exclude_labels':form.cleaned_data['exclude_labels'],
			}
			
			payload, license_holders_event_errors = participation_data( **initial )
			payload_json = json.dumps(payload, separators=(',',':'))
	else:
		payload, license_holders_event_errors = participation_data( **initial )
		payload_json = json.dumps(payload, separators=(',',':'))
		form = get_participant_report_form()( initial=initial )
	
	page_title = [u'Analytics']
	if initial.get('start_date',None) is not None:
		page_title.append( u'from {}'.format( initial['start_date'] .strftime('%Y-%d-%m') ) )
	if initial.get('end_date', None) is not None:
		page_title.append( u'to {}'.format( initial['end_date'].strftime('%Y-%d-%m') ) )
	if initial.get('organizers',None):
		page_title.append( u'for {}'.format( u', '.join(initial['organizers']) ) )
	page_title = u' '.join( page_title )
		
	def get_name( cls, id ):
		obj = cls.objects.filter(id=id).first()
		return obj.name if obj else ''
	
	if initial.get('disciplines',None):
		page_title += u' ({})'.format( u','.join( d.name for d in Discipline.objects.filter(pk__in=initial['disciplines'])) )
	if initial.get('race_classes',None):
		page_title += u' ({})'.format(u','.join( d.name for d in RaceClass.objects.filter(pk__in=initial['race_classes'])) )
	if initial.get('include_labels', None):
		page_title += u', +({})'.format( u','.join( r.name for r in ReportLabel.objects.filter(pk__in=initial['include_labels'])) )
	if initial.get('exclude_labels',None):
		page_title += u', -({})'.format( u','.join( r.name for r in ReportLabel.objects.filter(pk__in=initial['exclude_labels'])) )
		
	return render_to_response( 'system_analytics.html', RequestContext(request, locals()) )
#--------------------------------------------------------------------------

def get_discipline_race_class_choices():
	discipline_used, race_class_used = set(), set()
	for competition in Competition.objects.all():
		discipline_used.add( competition.discipline )
		race_class_used.add( competition.race_class )
	discipline_choices = [(0,_('All'))] + [(d.id, d.name) for d in sorted(discipline_used, key=lambda x: x.sequence)]
	race_class_choices = [(0,_('All'))] + [(d.id, d.name) for d in sorted(race_class_used, key=lambda x: x.sequence)]
	return discipline_choices, race_class_choices

def get_year_on_year_form():
	@autostrip
	class YearOnYearReportForm( Form ):
		discipline_choices, race_class_choices = get_discipline_race_class_choices()
		discipline = forms.ChoiceField( required = False, label = _('Discipline'), choices = discipline_choices )
		race_class = forms.ChoiceField( required = False, label = _('Race Class'), choices = race_class_choices )
		organizers = forms.MultipleChoiceField( required = False, label = _('Organizers'), choices = get_organizer_choices(), help_text=_('Ctrl-Click to Multi-Select') )
		include_labels = forms.MultipleChoiceField( required = False, label = _('Include Labels'), choices = [(r.pk, r.name) for r in ReportLabel.objects.all()], help_text=_('Ctrl-Click to Multi-Select') )
		exclude_labels = forms.MultipleChoiceField( required = False, label = _('Exclude Labels'), choices = [(r.pk, r.name) for r in ReportLabel.objects.all()], help_text=_('Ctrl-Click to Multi-Select') )
		
		def __init__( self, *args, **kwargs ):
			super(YearOnYearReportForm, self).__init__(*args, **kwargs)
			
			self.helper = FormHelper( self )
			self.helper.form_action = '.'
			self.helper.form_class = 'form-inline'
			
			self.helper.layout = Layout(
				Row(
					Field('discipline', id='focus'), Field('race_class'),
					Field('organizers', size=8),
					Field('include_labels', size=8),
					Field('exclude_labels', size=8),
				),
				HTML( '<hr/>' ),
			)
			addFormButtons( self, OK_BUTTON | CANCEL_BUTTON )
	
	return YearOnYearReportForm

@external_access
@user_passes_test( lambda u: u.is_superuser )
def YearOnYearAnalytics( request ):
	initial = request.session.get( 'attendance_analytics', {
			'discipline':-1,
			'race_class':-1,
			'organizers':[],
		}
	)
	
	if request.method == 'POST':
		if 'cancel-submit' in request.POST:
			return HttpResponseRedirect(getContext(request,'cancelUrl'))
			
		form = get_year_on_year_form()( request.POST )
		if form.is_valid():
			initial = {
				'discipline':int(form.cleaned_data['discipline']),
				'race_class':int(form.cleaned_data['race_class']),
				'organizers':form.cleaned_data['organizers'],
				'include_labels':form.cleaned_data['include_labels'],
				'exclude_labels':form.cleaned_data['exclude_labels'],
			}
			
			payload = year_on_year_data( **initial )
			payload_json = json.dumps(payload, separators=(',',':'))
	else:
		payload = year_on_year_data( **initial )
		payload_json = json.dumps(payload, separators=(',',':'))
		form = get_year_on_year_form()( initial=initial )
	
	page_title = [u'Year on Year Analytics']
	if initial.get('organizers',None):
		page_title.append( u'for {}'.format( u', '.join(initial['organizers']) ) )
	page_title = u' '.join( page_title )
		
	def get_name( cls, id ):
		obj = cls.objects.filter(id=id).first()
		return obj.name if obj else ''
	
	if initial.get('discipline',0) > 0:
		page_title += u' {}'.format(get_name(Discipline, initial['discipline']))
	if initial.get('race_class',0) > 0:
		page_title += u' {}'.format(get_name(RaceClass, initial['race_class']))
	if initial.get('include_labels', None):
		page_title += u', +({})'.format( u','.join( r.name for r in ReportLabel.objects.filter(pk__in=initial['include_labels'])) )
	if initial.get('exclude_labels',None):
		page_title += u', -({})'.format( u','.join( r.name for r in ReportLabel.objects.filter(pk__in=initial['exclude_labels'])) )
		
	return render_to_response( 'year_on_year_analytics.html', RequestContext(request, locals()) )

#-----------------------------------------------------------------------------------------

def QRCode( request ):
	exclude_breadcrumbs = True
	qrpath = request.build_absolute_uri()
	qrpath = os.path.dirname( qrpath )
	qrpath = os.path.dirname( qrpath ) + '/'
	return render_to_response( 'qrcode.html', RequestContext(request, locals()) )
	
#--------------------------------------------------------------------------

def Logout( request ):
	logout( request )
	next = getContext(request, 'cancelUrl')
	return HttpResponseRedirect('/RaceDB/login?next=' + next)

from django.conf import settings
settings.DATABASES['default']['NAME'] = settings.DATABASES['default']['NAME']
