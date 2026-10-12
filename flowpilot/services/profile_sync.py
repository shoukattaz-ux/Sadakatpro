"""Pull profiles from an anti-detect browser into the accounts table."""

from __future__ import annotations

from dataclasses import dataclass

from flowpilot.adapters.base import BrowserAdapter
from flowpilot.storage.models import Account, Connection
from flowpilot.storage.repositories import Repositories


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    total: int = 0


async def sync_profiles(repos: Repositories, adapter: BrowserAdapter, conn: Connection,
                        use_remote_groups: bool = True) -> SyncResult:
    """Create accounts for new profiles and refresh names of known ones. Groups reported by
    the browser are created locally when ``use_remote_groups`` is on. Variables are kept."""
    profiles = await adapter.list_profiles()
    groups = {g.name: g for g in repos.groups.list()}
    result = SyncResult(total=len(profiles))
    for prof in profiles:
        acc = repos.accounts.find_by_remote(conn.id, prof.id)
        if acc is None:
            acc = Account(connection_id=conn.id, remote_profile_id=prof.id, name=prof.name or prof.id,
                          tags=", ".join(prof.tags), notes=prof.notes)
            if use_remote_groups and prof.group:
                group = groups.get(prof.group) or repos.groups.create(prof.group)
                groups[group.name] = group
                acc.group_id = group.id
            repos.accounts.save(acc)
            result.created += 1
        elif prof.name and acc.name != prof.name:
            acc.name = prof.name
            repos.accounts.save(acc)
            result.updated += 1
    return result
