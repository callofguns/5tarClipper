class TryLater(Exception):
    """Something temporary went wrong (e.g. Claude usage limit). Press Retry later."""


class UserFixable(Exception):
    """A problem you need to fix (bad link, private video, ...). The message says what to do."""
