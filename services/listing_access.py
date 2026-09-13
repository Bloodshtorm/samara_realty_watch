from sqlalchemy import exists, select, true
from sqlalchemy.sql.elements import ColumnElement

from app.models import Listing, ListingObservation, Search, SearchContext, User


def visible_listing_condition(user: User | None) -> ColumnElement[bool]:
    if user is None or user.role == "admin":
        return true()
    return exists(
        select(ListingObservation.id)
        .join(Search)
        .join(SearchContext)
        .where(
            ListingObservation.listing_id == Listing.id,
            SearchContext.owner_user_id == user.id,
            SearchContext.enabled.is_(True),
        )
    ).correlate(Listing)
