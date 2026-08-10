# -*- coding: utf-8 -*-
#
import calendar
import threading
import time
from email.utils import formatdate

from rest_framework.serializers import BooleanField

_STRPTIME_LOCK = threading.Lock()

_GMT_FORMAT = "%a, %d %b %Y %H:%M:%S GMT"
_ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


def to_unixtime(time_string, format_string):
    time_string = time_string.decode("ascii")
    with _STRPTIME_LOCK:
        return int(calendar.timegm(time.strptime(time_string, format_string)))


def http_date(timeval=None):
    """Returns a GMT time string conforming to the HTTP standard, which in
    strftime format is "%a, %d %b %Y %H:%M:%S GMT".
    strftime itself cannot be used, because its result depends on the locale.
    """
    return formatdate(timeval, usegmt=True)


def http_to_unixtime(time_string):
    """Converts an HTTP Date format string into UNIX time (seconds since
    1970-01-01 00:00:00 UTC).

    An HTTP Date looks like `Sat, 05 Dec 2015 11:10:29 GMT`.
    """
    return to_unixtime(time_string, _GMT_FORMAT)


def iso8601_to_unixtime(time_string):
    """Converts an ISO8601 time string (e.g. 2012-02-24T06:07:48.000Z) into UNIX time, accurate to the second."""
    return to_unixtime(time_string, _ISO8601_FORMAT)


def is_true(value):
    return value in BooleanField.TRUE_VALUES


def is_false(value):
    return value in BooleanField.FALSE_VALUES
