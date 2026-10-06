"""Named bits in the existing Position / Role / PermissionOverride bitmask."""

from enum import IntFlag


class Permission(IntFlag):
    CONFIRM_BROADCAST = 1 << 0
    REPLY_BROADCAST = 1 << 1
