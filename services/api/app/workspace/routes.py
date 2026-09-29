import uuid

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth.dependencies import AnalystUser, CurrentUser, DbSession
from app.companies.service import resolve_security
from app.core.errors import ApiError
from app.db.models import User, Watchlist, WatchlistItem
from app.workspace.schemas import (
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistItemOut,
    WatchlistOut,
)

router = APIRouter(prefix="/watchlists", tags=["watchlists"])
MAX_WATCHLISTS = 50
MAX_ITEMS = 500


def _owned(db: Session, user: User, watchlist_id: uuid.UUID) -> Watchlist:
    # Ownership is part of the query, so another user's watchlist is indistinguishable
    # from a missing one.
    watchlist = db.scalar(
        select(Watchlist)
        .where(Watchlist.id == watchlist_id, Watchlist.owner_id == user.id)
        .options(selectinload(Watchlist.items).selectinload(WatchlistItem.security))
    )
    if watchlist is None:
        raise ApiError(404, "watchlist_not_found", "Watchlist not found.")
    return watchlist


def _out(watchlist: Watchlist) -> WatchlistOut:
    return WatchlistOut(
        id=watchlist.id,
        name=watchlist.name,
        created_at=watchlist.created_at,
        items=[
            WatchlistItemOut(
                ticker=item.security.ticker,
                name=item.security.company.legal_name,
                exchange=item.security.exchange,
                is_active=item.security.is_active,
                added_at=item.added_at,
            )
            for item in watchlist.items
        ],
    )


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(user: CurrentUser, db: DbSession) -> list[WatchlistOut]:
    watchlists = db.scalars(
        select(Watchlist)
        .where(Watchlist.owner_id == user.id)
        .order_by(Watchlist.created_at)
        .options(selectinload(Watchlist.items).selectinload(WatchlistItem.security))
    ).all()
    return [_out(w) for w in watchlists]


@router.post("", response_model=WatchlistOut, status_code=201)
def create_watchlist(body: WatchlistCreate, user: AnalystUser, db: DbSession) -> WatchlistOut:
    count = (
        db.scalar(select(func.count()).select_from(Watchlist).where(Watchlist.owner_id == user.id))
        or 0
    )
    if count >= MAX_WATCHLISTS:
        raise ApiError(409, "watchlist_limit_reached", f"At most {MAX_WATCHLISTS} watchlists.")
    watchlist = Watchlist(owner_id=user.id, name=body.name.strip())
    db.add(watchlist)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "watchlist_name_taken", "A watchlist with this name exists.") from exc
    return _out(_owned(db, user, watchlist.id))


@router.delete("/{watchlist_id}", status_code=204)
def delete_watchlist(watchlist_id: uuid.UUID, user: AnalystUser, db: DbSession) -> None:
    db.delete(_owned(db, user, watchlist_id))
    db.commit()


@router.post("/{watchlist_id}/items", response_model=WatchlistOut, status_code=201)
def add_item(
    watchlist_id: uuid.UUID, body: WatchlistItemCreate, user: AnalystUser, db: DbSession
) -> WatchlistOut:
    watchlist = _owned(db, user, watchlist_id)
    security = resolve_security(db, body.ticker)
    if any(item.security_id == security.id for item in watchlist.items):
        return _out(watchlist)
    if len(watchlist.items) >= MAX_ITEMS:
        raise ApiError(409, "watchlist_full", f"A watchlist holds at most {MAX_ITEMS} items.")
    watchlist.items.append(WatchlistItem(security=security))
    db.commit()
    return _out(_owned(db, user, watchlist_id))


@router.delete("/{watchlist_id}/items/{ticker}", response_model=WatchlistOut)
def remove_item(
    watchlist_id: uuid.UUID, ticker: str, user: AnalystUser, db: DbSession
) -> WatchlistOut:
    watchlist = _owned(db, user, watchlist_id)
    normalized = ticker.strip().upper()
    watchlist.items = [item for item in watchlist.items if item.security.ticker != normalized]
    db.commit()
    return _out(_owned(db, user, watchlist_id))
