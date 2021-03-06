from django.shortcuts import render, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.utils import timezone
from issues.models import *
from issues.utils import send_mail, send_sms
from users.utils import sorted_users
import re

ISSUE_MAGIC_TOKEN = '#!'

@login_required
def detail(request, issue_id):
	issue = get_object_or_404(Issue, pk=issue_id)

	action = request.POST.get('action')		# Check if postback
	if action == 'assign':
		assign(issue, request)
	elif action == 'set-label':
		set_label(issue, request)
	#elif action == 'set-due':
		#pass
	elif action == 'toggle-star':
		toggle_star(issue, request)
	elif action:
		content = request.POST.get('content')
		if content: comment(issue, request)
		if action == 'toggle-state':
			toggle_state(issue, request)

	return render(request, 'issues/detail.html', {
		'issue': issue,
		'labels': Label.objects.all(),
		'users': sorted_users(User.objects.filter(is_active=True)),
		'has_starred': issue.starring.filter(id=request.user.id).count() > 0,
	})


def update(issue, user, content='', mode=IssueHistory.COMMENT):
	issue.last_updated = timezone.now()
	issue.save()
	IssueHistory.objects.create(issue=issue, user=user, mode=mode, content=content)

def notify(issue, user, template_name, context):
	for watcher in issue.starring.all():
		if user == watcher: continue
		send_mail(user, watcher, template_name, context)

def assign(issue, request):
	if not request.user.has_perm('issues.assign_issue'):
		return	# Audit fail

	assignee = request.POST.get('assignee')
	if assignee is not None:					# empty string => unassign
		if len(assignee) > 0:
			try:
				u = User.objects.get(id=assignee)
				issue.assignee = u
				issue.starring.add(u)			# Automatic starring
				update(issue=issue, user=request.user, mode=IssueHistory.ASSIGN, content=assignee)
				notify(issue, request.user, 'mail/issue_assigned.html', { 'issue': issue })
			except User.DoesNotExist: pass		# Just in case we're under attack...
		else:
			issue.assignee = None
			update(issue=issue, user=request.user, mode=IssueHistory.UNASSIGN)
			notify(issue, request.user, 'mail/issue_assigned.html', { 'issue': issue })

def set_label(issue, request):
	if not (issue.assignee == request.user or request.user.has_perm('issues.label_issue')):
		return	# Audit fail

	old_labels = [l.id for l in issue.labels.all()]
	new_labels = []

	for label_str in request.POST.getlist('labels'):
		try:
			new_labels.append(int(label_str))
		except ValueError: pass

	# Remove unused labels
	labels_to_remove = [l for l in old_labels if l not in new_labels]
	labels_to_add = [l for l in new_labels if l not in old_labels]

	# * Old labels won't have integrity issues so eliminate try block
	for label_id in labels_to_remove:
		issue.labels.remove(Label.objects.get(id=label_id))
		update(issue=issue, user=request.user, mode=IssueHistory.UNLABEL, content=label_id)

	# Add new labels
	for label_id in labels_to_add:
		try:
			issue.labels.add(Label.objects.get(id=label_id))
			update(issue=issue, user=request.user, mode=IssueHistory.LABEL, content=label_id)
		except Label.DoesNotExist:
			pass

	issue.save()
	notify(issue, request.user, 'mail/issue_labeled.html', {'issue': issue, 'old_labels': old_labels, 'new_labels': new_labels})

def comment(issue, request):
	if not request.user.has_perm('issues.comment_issue'):
		return	# Audit fail

	content = request.POST.get('content')
	if content:
		urgent = False
		if content.startswith(ISSUE_MAGIC_TOKEN):
			content = content[len(ISSUE_MAGIC_TOKEN):]
			urgent = True

		update(issue=issue, user=request.user, content=content)
		notify(issue, request.user, 'mail/issue_general.html', {'issue': issue, 'comment': content})

		mentions = set(re.findall(u'(?<=@)[0-9A-Za-z\u3400-\u9fff\uf900-\ufaff_\\-]+', content))
		for mention in mentions:
			try:
				mentionee = User.objects.get(Q(username__istartswith=mention) | Q(profile__display_name__iexact=mention))
			except User.DoesNotExist:
				continue

			issue.starring.add(mentionee)	# Auto watch
			send_mail(request.user, mentionee, 'mail/issue_mentioned.html', {'issue': issue, 'comment': content })
			if urgent:
				send_sms(request.user, mentionee, 'sms/issue_comment.txt', { 'issue': issue, 'comment': content })

		if urgent:
			if issue.assignee and issue.assignee.profile.phone:
				send_sms(request.user, issue.assignee, 'sms/issue_comment.txt', { 'issue': issue, 'comment': content })

def toggle_state(issue, request):
	if not request.user.has_perm('issues.toggle_issue'):
		return	# Audit fail

	issue.is_open = not issue.is_open
	update(issue=issue, user=request.user, mode=(IssueHistory.REOPEN if issue.is_open else IssueHistory.CLOSE))
	notify(issue, request.user, 'mail/issue_general.html', {'issue': issue})

def toggle_star(issue, request):
	if issue.starring.filter(id=request.user.id).count():
		issue.starring.remove(request.user)
	else:
		issue.starring.add(request.user)

def edit(issue, request):
	if request.user == issue.creator or request.user.has_perm('issues.change_issue'):
		pass	# Audit success
	else:
		pass	# Audit fail
