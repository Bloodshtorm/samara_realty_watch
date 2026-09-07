from __future__ import annotations

import itertools
import math
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApartmentGroup, ApartmentUserState, Listing, ListingLink, ListingUserState
from services.deduplication import ALGORITHM_VERSION, building_key, compare_listings, is_flat


def pair_key(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    return tuple(sorted((a, b), key=str))  # type: ignore[return-value]


def candidate_pairs(listings: list[Listing]) -> set[tuple[UUID, UUID]]:
    houses: dict[str, list[Listing]] = defaultdict(list)
    cells: dict[tuple[int, int], list[Listing]] = defaultdict(list)
    pairs: set[tuple[UUID, UUID]] = set()
    for item in listings:
        if not is_flat(item):
            continue
        key = building_key(item)
        if key:
            for other in houses[key]:
                pairs.add(pair_key(item.id, other.id))
            houses[key].append(item)
        if item.latitude is not None and item.longitude is not None:
            cell = (math.floor(item.latitude * 1000), math.floor(item.longitude * 1000))
            for dx, dy in itertools.product((-1, 0, 1), repeat=2):
                for other in cells[cell[0] + dx, cell[1] + dy]:
                    pairs.add(pair_key(item.id, other.id))
            cells[cell].append(item)
    return pairs


async def group_members(session: AsyncSession, group_id: UUID) -> list[Listing]:
    return list(
        (
            await session.scalars(
                select(Listing)
                .where(Listing.group_id == group_id)
                .order_by(Listing.source, Listing.id)
            )
        ).all()
    )


async def ensure_groups(session: AsyncSession, listings: list[Listing]) -> None:
    states = {
        state.listing_id: state for state in (await session.scalars(select(ListingUserState))).all()
    }
    for item in listings:
        if item.group_id is None and is_flat(item):
            state = states.get(item.id)
            group = ApartmentGroup(
                is_favorite=bool(state and state.is_favorite),
                is_hidden=bool(state and state.is_hidden),
            )
            session.add(group)
            await session.flush()
            item.group_id = group.id
            listing_states = (
                await session.scalars(
                    select(ListingUserState).where(ListingUserState.listing_id == item.id)
                )
            ).all()
            for listing_state in listing_states:
                if listing_state.user_id is not None:
                    session.add(
                        ApartmentUserState(
                            user_id=listing_state.user_id,
                            group_id=group.id,
                            is_favorite=listing_state.is_favorite,
                            is_hidden=listing_state.is_hidden,
                            hidden_reason=listing_state.hidden_reason,
                        )
                    )
    await session.flush()


async def merge_groups(session: AsyncSession, left: UUID, right: UUID) -> UUID:
    if left == right:
        return left
    target = await session.get(ApartmentGroup, left)
    source = await session.get(ApartmentGroup, right)
    if target is None or source is None:
        raise ValueError("Apartment group no longer exists")
    target.is_favorite = target.is_favorite or source.is_favorite
    target.is_hidden = target.is_hidden and source.is_hidden
    target.needs_review = target.needs_review or source.needs_review
    await _merge_user_states(session, left, right)
    for item in await group_members(session, right):
        item.group_id = left
    await session.flush()
    await session.delete(source)
    await session.flush()
    return left


async def set_link(
    session: AsyncSession,
    a: Listing,
    b: Listing,
    status: str,
    origin: str,
    reason: dict | None = None,
) -> ListingLink:
    left, right = pair_key(a.id, b.id)
    link = (
        await session.scalars(
            select(ListingLink).where(
                ListingLink.listing_id_a == left,
                ListingLink.listing_id_b == right,
                ListingLink.match_type == "apartment",
            )
        )
    ).one_or_none()
    if link is None:
        link = ListingLink(listing_id_a=left, listing_id_b=right, match_type="apartment")
        session.add(link)
    link.status = status
    link.decision_origin = origin
    link.confidence = 1.0 if status == "confirmed" else 0.5
    link.match_reason = reason or {"algorithm_version": ALGORITHM_VERSION, "manual": True}
    await session.flush()
    return link


async def reconcile_groups(session: AsyncSession) -> dict[str, int]:
    listings = list((await session.scalars(select(Listing))).all())
    await ensure_groups(session, listings)
    by_id = {item.id: item for item in listings}
    groups: dict[UUID, list[Listing]] = defaultdict(list)
    for item in listings:
        if item.group_id:
            groups[item.group_id].append(item)
    links = {
        pair_key(link.listing_id_a, link.listing_id_b): link
        for link in (
            await session.scalars(select(ListingLink).where(ListingLink.match_type == "apartment"))
        ).all()
    }
    pairs = candidate_pairs(listings)
    # Check existing membership even if a source changed the address and left its old bucket.
    for members in groups.values():
        pairs.update(pair_key(a.id, b.id) for a, b in itertools.combinations(members, 2))
    comparisons = {key: compare_listings(by_id[key[0]], by_id[key[1]]) for key in pairs}
    merged = suggested = 0
    for key in sorted(pairs, key=lambda pair: (str(pair[0]), str(pair[1]))):
        a, b = (by_id[ident] for ident in key)
        result = comparisons[key]
        existing = links.get(key)
        if a.group_id == b.group_id and a.group_id:
            address_conflict = (
                building_key(a) and building_key(b) and building_key(a) != building_key(b)
            )
            floor_conflict = a.floor is not None and b.floor is not None and a.floor != b.floor
            if address_conflict or floor_conflict:
                group = await session.get(ApartmentGroup, a.group_id)
                assert group is not None
                group.needs_review = True
            continue
        if existing and (existing.status == "rejected" or existing.decision_origin == "manual"):
            continue
        if result is None or a.group_id is None or b.group_id is None:
            if existing and existing.status == "candidate":
                await session.delete(existing)
            continue
        left, right = a.group_id, b.group_id
        can_merge = result.automatic
        for group_id in (left, right):
            group = await session.get(ApartmentGroup, group_id)
            if group and group.needs_review:
                can_merge = False
        for x, y in itertools.product(groups[left], groups[right]):
            cross_key = pair_key(x.id, y.id)
            cross = comparisons.get(cross_key)
            veto = links.get(cross_key)
            if not cross or not cross.automatic or (veto and veto.status == "rejected"):
                can_merge = False
                break
        if can_merge:
            for x, y in itertools.product(groups[left], groups[right]):
                cross_key = pair_key(x.id, y.id)
                cross = comparisons[cross_key]
                assert cross is not None
                links[cross_key] = await set_link(
                    session, x, y, "confirmed", "automatic", cross.match_reason
                )
            await merge_groups(session, left, right)
            groups[left].extend(groups.pop(right))
            merged += 1
        else:
            links[key] = await set_link(
                session, a, b, "candidate", "automatic", result.match_reason
            )
            suggested += 1
    await session.flush()
    return {"merged_groups": merged, "candidate_pairs": suggested, "compared_pairs": len(pairs)}


async def confirm_link(session: AsyncSession, link: ListingLink) -> UUID:
    a = await session.get(Listing, link.listing_id_a)
    b = await session.get(Listing, link.listing_id_b)
    if a is None or b is None or a.group_id is None or b.group_id is None:
        raise ValueError("Listings are not assigned to apartments")
    left, right = a.group_id, b.group_id
    # Explicit confirmation overrides prior automatic/manual decisions for this merge.
    for x, y in itertools.product(
        await group_members(session, left), await group_members(session, right)
    ):
        if x.id != y.id:
            await set_link(session, x, y, "confirmed", "manual")
    return await merge_groups(session, left, right)


async def split_member(session: AsyncSession, group_id: UUID, listing_id: UUID) -> UUID:
    group = await session.get(ApartmentGroup, group_id)
    members = await group_members(session, group_id)
    item = next((member for member in members if member.id == listing_id), None)
    if group is None or item is None or len(members) < 2:
        raise ValueError("Cannot split this apartment")
    new = ApartmentGroup(is_favorite=group.is_favorite, is_hidden=group.is_hidden)
    session.add(new)
    await session.flush()
    await _copy_user_states(session, group.id, new.id)
    for other in members:
        if other.id != item.id:
            await set_link(session, item, other, "rejected", "manual")
    item.group_id = new.id
    group.needs_review = False
    for a, b in itertools.combinations([member for member in members if member.id != item.id], 2):
        if (building_key(a) and building_key(b) and building_key(a) != building_key(b)) or (
            a.floor is not None and b.floor is not None and a.floor != b.floor
        ):
            group.needs_review = True
    await session.flush()
    return new.id


async def _merge_user_states(session: AsyncSession, target_id: UUID, source_id: UUID) -> None:
    source_states = (
        await session.scalars(
            select(ApartmentUserState).where(ApartmentUserState.group_id == source_id)
        )
    ).all()
    for source_state in source_states:
        target_state = (
            await session.scalars(
                select(ApartmentUserState).where(
                    ApartmentUserState.group_id == target_id,
                    ApartmentUserState.user_id == source_state.user_id,
                )
            )
        ).one_or_none()
        if target_state is None:
            source_state.group_id = target_id
            continue
        target_state.is_favorite = target_state.is_favorite or source_state.is_favorite
        target_state.is_hidden = target_state.is_hidden and source_state.is_hidden
        await session.delete(source_state)


async def _copy_user_states(session: AsyncSession, source_id: UUID, target_id: UUID) -> None:
    states = (
        await session.scalars(
            select(ApartmentUserState).where(ApartmentUserState.group_id == source_id)
        )
    ).all()
    for state in states:
        session.add(
            ApartmentUserState(
                user_id=state.user_id,
                group_id=target_id,
                is_favorite=state.is_favorite,
                is_hidden=state.is_hidden,
                hidden_reason=state.hidden_reason,
            )
        )
