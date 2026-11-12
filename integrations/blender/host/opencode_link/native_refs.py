# SPDX-License-Identifier: GPL-3.0-or-later
"""Ephemeral document/epoch-owned RNA references; never writes Blender metadata."""

import re
import secrets

from .wire import HostFailure

ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def identity(value):
    """ID session_uid plus RNA type, with a live-wrapper check. Never a bare address."""
    try:
        if not value.as_pointer():
            raise ReferenceError()
        uid = getattr(value, "session_uid", None)
        if isinstance(uid, int) and uid > 0:
            return (value.bl_rna.identifier, uid)
        return None
    except (ReferenceError, AttributeError, RuntimeError):
        return None


def _live_equal(a, b):
    try:
        return bool(a.as_pointer() and b.as_pointer() and a == b)
    except (ReferenceError, RuntimeError, TypeError):
        return False


class NativeRefs:
    """inventory yields (RNA entity, ID owner). Children require the same live owner.

    ID lookup uses Blender's session UID. Children retain a validated RNA wrapper and
    membership, refusing uncertain identities rather than resolving by display name.
    """

    def __init__(self, inventory):
        self._inventory = inventory
        self.reset()

    def reset(self):
        self.epoch = secrets.token_hex(16)
        self._entries = {}
        self._by_identity = {}

    def _valid(self, entry, inventory):
        value, owner, token, owner_token = entry
        if identity(owner) != owner_token or not _live_equal(owner, owner):
            return False
        if not _live_equal(value, value):
            return False
        return any(identity(o) == owner_token and _live_equal(o, owner) and
                   ((token is not None and identity(v) == token) or
                    (token is None and _live_equal(v, value))) for v, o in inventory)

    def ref(self, value, owner=None):
        owner = owner if owner is not None else value
        token, owner_token = identity(value), identity(owner)
        if owner_token is None or not _live_equal(value, value):
            raise HostFailure("stale_handle", "RNA identity is unavailable; read context again.")
        # Pointer is an index only. Reuse always revalidates the retained wrapper and owner.
        key = (owner_token, token if token is not None else (value.bl_rna.identifier, value.as_pointer()))
        # ponytail: linear live-membership scan per ref; add a revision-owned index if measured page cost requires it.
        inventory = list(self._inventory())
        previous = self._by_identity.get(key)
        entry = (value, owner, token, owner_token)
        if not self._valid(entry, inventory):
            raise HostFailure("stale_handle", "Entity is outside this document or owner.")
        if previous in self._entries and self._valid(self._entries[previous], inventory):
            return previous
        ref = "rna-" + secrets.token_hex(16)
        self._entries[ref] = entry
        self._by_identity[key] = ref
        return ref

    def resolve(self, ref):
        if not isinstance(ref, str) or ref not in self._entries:
            raise HostFailure("stale_handle", "Unknown document/epoch reference; read context again.")
        entry = self._entries[ref]
        if not self._valid(entry, list(self._inventory())):
            del self._entries[ref]
            raise HostFailure("stale_handle", "Entity was removed or its owner changed; read context again.")
        return entry[0]

    def owner(self, ref):
        self.resolve(ref)
        return self._entries[ref][1]


class BatchAliases:
    """Local-only future plan contract. A binding names an earlier creation, never a path."""

    def __init__(self):
        self._values = {}

    def bind(self, name, value):
        if not isinstance(name, str) or not ALIAS.fullmatch(name) or name in self._values:
            raise HostFailure("invalid_args", "bind must be a unique simple identifier.")
        self._values[name] = value

    def resolve(self, value):
        if isinstance(value, str) and value.startswith("$"):
            name = value[1:]
            if not ALIAS.fullmatch(name) or name not in self._values:
                raise HostFailure("invalid_args", "Alias must name an earlier creation in this batch.")
            return self._values[name]
        return value
