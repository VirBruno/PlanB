"""Notificaciones privadas sin copia del estado de la invitación."""
from uuid import UUID

from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.groups.services.invitation_service import (
    InvitationUnavailable, canonical_id, rpc,
)
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired

PAGE_SIZE = 12


@sensitive_variables()
def unread_count(access_token):
    if not access_token:
        raise SessionExpired()
    try:
        with clients.public_client(access_token=access_token) as client:
            result = (client.table('notifications').select('id', count='exact', head=True)
                      .is_('read_at', 'null').execute())
        if type(result.count) is not int or result.count < 0:
            raise ValueError
        return result.count
    except Exception as error:
        if isinstance(error, APIError) and error.code in ('PGRST301', 'PGRST303'):
            raise SessionExpired() from None
        raise InvitationUnavailable() from None


@sensitive_variables()
def list_notifications(access_token, *, page=1):
    if type(page) is not int or not 1 <= page <= 10000:
        page = 1
    rows = rpc(access_token, 'list_notifications', {'p_page': page}, read=True)
    try:
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE + 1:
            raise ValueError
        items = []
        for row in rows[:PAGE_SIZE]:
            item = {'id': str(UUID(row['id'])), 'type': row['type']}
            for key in ('created_at', 'read_at'):
                item[key] = parse_datetime(row[key]) if row[key] is not None else None
                if row[key] is not None and item[key] is None:
                    raise ValueError
            if item['created_at'] is None or not isinstance(item['type'], str):
                raise ValueError
            if item['type'] == 'group_invitation':
                for key in ('invitation_id', 'group_id'):
                    item[key] = str(UUID(row[key]))
                for key in ('group_name', 'inviter_username'):
                    item[key] = row[key]
                    if not isinstance(item[key], str):
                        raise ValueError
                item['status'] = row['status']
                if item['status'] not in ('pending', 'accepted', 'rejected'):
                    raise ValueError
            items.append(item)
        return {'items': items, 'page': page,
                'previous_page': page - 1 if page > 1 else None,
                'next_page': page + 1 if len(rows) > PAGE_SIZE else None}
    except Exception:
        raise InvitationUnavailable() from None


@sensitive_variables()
def mark_read(access_token, notification_id):
    notification_id = canonical_id(notification_id)
    result = rpc(access_token, 'mark_notification_read', {'p_notification_id': notification_id})
    if result != notification_id:
        raise InvitationUnavailable()
    return notification_id
