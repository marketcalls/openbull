import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.dependencies import get_db, get_current_user
from backend.models.user import User
from backend.models.broker_config import BrokerConfig
from backend.schemas.broker import BrokerConfigCreate, BrokerConfigResponse, BrokerListItem
from backend.security import encrypt_value, decrypt_value
from backend.utils.plugin_loader import get_all_plugins

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/broker", tags=["broker-config"])


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-3:]


@router.get("/list", response_model=list[BrokerListItem])
async def list_brokers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plugins = get_all_plugins()
    result = await db.execute(
        select(BrokerConfig).where(BrokerConfig.user_id == user.id)
    )
    configs = {c.broker_name: c for c in result.scalars().all()}

    brokers = []
    for name, info in plugins.items():
        cfg = configs.get(name)
        brokers.append(BrokerListItem(
            name=name,
            display_name=info.get("display_name", name),
            supported_exchanges=info.get("supported_exchanges", []),
            is_configured=cfg is not None,
            is_active=cfg.is_active if cfg else False,
            oauth_type=info.get("oauth_type", ""),
        ))
    return brokers


@router.get("/credentials/{broker_name}", response_model=BrokerConfigResponse)
async def get_broker_credentials(
    broker_name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user.id,
            BrokerConfig.broker_name == broker_name,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Broker not configured")

    extra = config.extra_config or {}
    client_id = extra.get("client_id") or None

    return BrokerConfigResponse(
        broker_name=config.broker_name,
        api_key_masked=_mask(decrypt_value(config.api_key)),
        api_secret_masked=_mask(decrypt_value(config.api_secret)) if config.api_secret else "",
        redirect_url=config.redirect_url,
        is_active=config.is_active,
        client_id=client_id,
    )


@router.put("/credentials")
async def save_broker_credentials(
    data: BrokerConfigCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plugins = get_all_plugins()
    if data.broker_name not in plugins:
        raise HTTPException(status_code=400, detail=f"Unknown broker: {data.broker_name}")

    plugin = plugins[data.broker_name]
    oauth_type = plugin.get("oauth_type", "")
    needs_secret = oauth_type not in ("credentials",)

    result = await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user.id,
            BrokerConfig.broker_name == data.broker_name,
        )
    )
    existing = result.scalar_one_or_none()

    # Empty inputs on update are treated as "keep existing" so a user can
    # tweak (say) redirect_url without re-typing the secret. We only require
    # non-empty key/secret on first-time setup.
    if not existing:
        if not data.api_key:
            raise HTTPException(status_code=400, detail="API Key is required.")
        if needs_secret and not data.api_secret:
            raise HTTPException(status_code=400, detail="API Secret is required.")

    final_client_id = data.client_id or (
        (existing.extra_config or {}).get("client_id") if existing else None
    )
    if data.broker_name == "dhan" and not final_client_id:
        raise HTTPException(status_code=400, detail="Dhan requires a Client ID.")

    if existing:
        if data.api_key:
            existing.api_key = encrypt_value(data.api_key)
        if data.api_secret:
            existing.api_secret = encrypt_value(data.api_secret)
        if data.redirect_url:
            existing.redirect_url = data.redirect_url
        extra = dict(existing.extra_config or {})
        if data.client_id:
            extra["client_id"] = data.client_id
        existing.extra_config = extra
        # SQLAlchemy doesn't auto-detect mutations to JSONB dicts.
        flag_modified(existing, "extra_config")
    else:
        extra = {"client_id": data.client_id} if data.client_id else {}
        db.add(BrokerConfig(
            user_id=user.id,
            broker_name=data.broker_name,
            api_key=encrypt_value(data.api_key),
            api_secret=encrypt_value(data.api_secret) if data.api_secret else "",
            redirect_url=data.redirect_url or "",
            is_active=False,
            extra_config=extra,
        ))

    await db.commit()
    logger.info("Broker credentials saved for %s by user %s", data.broker_name, user.username)
    return {"status": "success", "message": f"Broker credentials for {data.broker_name} saved."}
