from datetime import timedelta

from django.db import transaction

from common.utils import get_logger
from common.utils.timezone import local_now
from users.models import User
from .models import MessageContent, SiteMessage

logger = get_logger(__file__)


class SiteMessageUtil:

    @classmethod
    def send_msg(cls, subject, message, user_ids=(), group_ids=(),
                 sender=None, is_broadcast=False, display_mode=MessageContent.DisplayMode.default):
        if not any((user_ids, group_ids, is_broadcast)):
            raise ValueError('No recipient is specified')

        with transaction.atomic():
            site_msg = MessageContent(
                subject=subject, message=message,
                is_broadcast=is_broadcast, sender=sender,
                display_mode=display_mode
            )

            if is_broadcast:
                # For broadcast messages, only create them for online users; messages are not created for users who are not logged in,
                # they are created on demand in SiteMessage.create_site_msg_for_user_if_need once the user logs in
                from audits.models import UserSession
                user_ids = UserSession.objects.all().values_list('user_id', flat=True).distinct()
            elif group_ids:
                site_msg.groups.add(*group_ids)

                user_ids_from_group = User.groups.through.objects.filter(
                    usergroup_id__in=group_ids
                ).values_list('user_id', flat=True)
                user_ids = [*user_ids, *user_ids_from_group]

            site_msg.users.add(*user_ids)
            # Only calling save triggers the post_save signal
            site_msg.save()
        return site_msg

    @classmethod
    def get_user_all_msgs(cls, user_id):
        site_msg_rels = SiteMessage.objects \
            .filter(user=user_id) \
            .prefetch_related('content') \
            .order_by('-date_created')
        return site_msg_rels

    @classmethod
    def get_user_all_msgs_count(cls, user_id):
        site_msgs_count = SiteMessage.objects.filter(
            user_id=user_id
        ).distinct().count()
        return site_msgs_count

    @classmethod
    def filter_user_msgs(cls, user_id, has_read=False):
        return cls.get_user_all_msgs(user_id).filter(has_read=has_read)

    @classmethod
    def get_user_unread_msgs_count(cls, user_id):
        site_msgs_count = SiteMessage.objects \
            .filter(user=user_id, has_read=False) \
            .values_list('content', flat=True) \
            .distinct().count()
        return site_msgs_count
    
    @classmethod
    def get_user_display_msgs(cls, user_id):
        # Get the user's unread messages that need to be displayed
        msgs = SiteMessage.objects.filter(user_id=user_id, has_read=False).exclude(
            content__display_mode=MessageContent.DisplayMode.default
        ).prefetch_related('content')
        return msgs
    
    @classmethod
    def create_site_msgs_for_user_if_need(cls, user_id):
        '''
        Create the user's unread messages that need to be displayed.
        For broadcast messages, only create them for online users; messages are not created for users who are not logged in,
        they are created on demand here once the user logs in.
        Only create messages the user doesn't already have, that were created within the last 24 hours, and that need to be broadcast.
        '''
        contents = MessageContent.objects.filter(
            is_broadcast=True,
            date_created__gt=local_now() - timedelta(hours=24)
        ).exclude(display_mode=MessageContent.DisplayMode.default)
        content_ids = set(contents.values_list('id', flat=True))
        has_content_ids = SiteMessage.objects.filter(
            user_id=user_id, content_id__in=content_ids
        ).values_list('content_id', flat=True)
        to_create_content_ids = set(content_ids) - set(has_content_ids)
        site_msgs = [
            SiteMessage(user_id=user_id, content_id=cid) for cid in to_create_content_ids
        ]
        SiteMessage.objects.bulk_create(site_msgs)

    @classmethod
    def mark_msgs_as_read(cls, user_id, msg_ids=None):
        site_msgs = SiteMessage.objects.filter(user_id=user_id)
        if msg_ids:
            site_msgs = site_msgs.filter(id__in=msg_ids)
        site_msgs.update(has_read=True, read_at=local_now())
