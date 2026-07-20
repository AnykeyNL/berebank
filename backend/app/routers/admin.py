from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account, AppSetting, Holding, Order, RssFeed, Trade, User
from ..schemas import (
    AdminUserCreate,
    AdminUserOut,
    AdminUserUpdate,
    RssFeedCreate,
    RssFeedOut,
    RssFeedStatusOut,
    RssFeedUpdate,
    SettingsOut,
    SettingsUpdate,
)
from ..security import hash_password, require_bank_manager
from ..services.bitvavo import bitvavo_service
from ..services.rss_aggregator import rss_aggregator_service
from ..services.twelvedata import twelvedata_service

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_bank_manager)])


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        balance_eur=user.account.balance_eur,
        created_at=user.created_at,
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id)).all()
    return [_user_out(u) for u in users]


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: AdminUserCreate, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
    )
    db.add(user)
    db.flush()
    db.add(Account(user_id=user.id, balance_eur=body.initial_balance_eur))
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(user_id: int, body: AdminUserUpdate, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.balance_eur is not None:
        user.account.balance_eur = body.balance_eur
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current: User = Depends(require_bank_manager),
    db: Session = Depends(get_db),
):
    if user_id == current.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.role == "bank_manager":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "BankManager accounts cannot be deleted")

    account_id = user.account.id
    db.execute(delete(Trade).where(Trade.account_id == account_id))
    db.execute(delete(Order).where(Order.account_id == account_id))
    db.execute(delete(Holding).where(Holding.account_id == account_id))
    db.execute(delete(Account).where(Account.id == account_id))
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _get_setting(db: Session, key: str) -> str | None:
    setting = db.get(AppSetting, key)
    return setting.value if setting else None


def _set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


@router.get("/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return SettingsOut(
        bitvavo_api_key_masked=_mask(_get_setting(db, "bitvavo_api_key")),
        has_api_secret=_get_setting(db, "bitvavo_api_secret") is not None,
        connection=bitvavo_service.status(),
        twelvedata_api_key_masked=_mask(_get_setting(db, "twelvedata_api_key")),
        twelvedata=twelvedata_service.status(),
    )


@router.put("/settings", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)):
    if body.bitvavo_api_key is not None:
        _set_setting(db, "bitvavo_api_key", body.bitvavo_api_key)
    if body.bitvavo_api_secret is not None:
        _set_setting(db, "bitvavo_api_secret", body.bitvavo_api_secret)
    if body.twelvedata_api_key is not None:
        _set_setting(db, "twelvedata_api_key", body.twelvedata_api_key)
    db.commit()
    if body.twelvedata_api_key is not None:
        # Apply the new key immediately so the stock/fund feed (re)starts.
        await twelvedata_service.restart(body.twelvedata_api_key)
    return get_settings(db)


@router.get("/rss-feeds", response_model=RssFeedStatusOut)
def list_rss_feeds(db: Session = Depends(get_db)):
    feeds = db.scalars(select(RssFeed).order_by(RssFeed.id)).all()
    return RssFeedStatusOut(
        feeds=[RssFeedOut.model_validate(f) for f in feeds],
        aggregator=rss_aggregator_service.status(),
    )


@router.post("/rss-feeds", response_model=RssFeedOut, status_code=status.HTTP_201_CREATED)
def create_rss_feed(body: RssFeedCreate, db: Session = Depends(get_db)):
    url = body.url.strip()
    if db.scalar(select(RssFeed).where(RssFeed.url == url)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A feed with this URL already exists")
    name = (body.name or "").strip() or url
    feed = RssFeed(url=url, name=name, enabled=True)
    db.add(feed)
    db.commit()
    db.refresh(feed)
    return RssFeedOut.model_validate(feed)


@router.patch("/rss-feeds/{feed_id}", response_model=RssFeedOut)
def update_rss_feed(feed_id: int, body: RssFeedUpdate, db: Session = Depends(get_db)):
    feed = db.get(RssFeed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RSS feed not found")
    if body.name is not None:
        feed.name = body.name
    if body.enabled is not None:
        feed.enabled = body.enabled
    db.commit()
    db.refresh(feed)
    return RssFeedOut.model_validate(feed)


@router.delete("/rss-feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rss_feed(feed_id: int, db: Session = Depends(get_db)):
    feed = db.get(RssFeed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RSS feed not found")
    db.delete(feed)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/rss-feeds/{feed_id}/fetch", response_model=RssFeedOut)
async def fetch_rss_feed(feed_id: int, db: Session = Depends(get_db)):
    feed = db.get(RssFeed, feed_id)
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RSS feed not found")
    try:
        await rss_aggregator_service.poll_feed_by_id(feed_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Feed fetch failed: {exc}")
    db.refresh(feed)
    return RssFeedOut.model_validate(feed)
