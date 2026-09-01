from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shop.core.exceptions import OrderNotFound, ProductInactive, ProductNotFound
from shop.core.ids import new_order_id
from shop.core.logging import get_logger
from shop.domain.enums import OrderStatus
from shop.models import Delivery, Order
from shop.repositories.deliveries import DeliveryRepository
from shop.repositories.ledger import AuditRepository
from shop.repositories.orders import OrderRepository
from shop.repositories.products import ProductRepository
from shop.services.payment_service import PaymentService

logger = get_logger(__name__)


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._products = ProductRepository(session)
        self._deliveries = DeliveryRepository(session)
        self._audit = AuditRepository(session)
        self._payments = PaymentService(session)

    async def create_order(self, *, sku: str, idempotency_key: str | None = None) -> Order:
        """Создаёт заказ по SKU.

        Если по этому ключу идемпотентности заказ уже есть — возвращает его,
        нового не создаёт.
        """
        if idempotency_key:
            existing = await self._orders.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info(
                    "order_create_idempotent_hit",
                    order_id=existing.id,
                    idempotency_key=idempotency_key,
                )
                return existing

        product = await self._products.get(sku)
        if product is None:
            raise ProductNotFound(f"sku {sku} not found", sku=sku)
        if not product.is_active:
            raise ProductInactive(f"sku {sku} is not available", sku=sku)

        order = Order(
            id=new_order_id(),
            sku=product.sku,
            amount_minor=product.price_minor,
            currency=product.currency,
            status=OrderStatus.CREATED,
            idempotency_key=idempotency_key,
        )
        self._orders.add(order)
        self._audit.add(
            order_id=order.id,
            event="order_created",
            to_status=OrderStatus.CREATED,
            payload={"sku": sku},
        )

        try:
            await self._session.flush()
        except IntegrityError:
            # Конкурентное создание с тем же ключом идемпотентности.
            await self._session.rollback()
            if idempotency_key:
                existing = await self._orders.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise

        logger.info("order_created", order_id=order.id, sku=sku, amount_minor=order.amount_minor)

        # Вебхук мог прийти раньше заказа — применяем накопленные события.
        await self._payments.apply_pending_orphans(order)
        return order

    async def get_order(self, order_id: str) -> Order:
        order = await self._orders.get(order_id)
        if order is None:
            raise OrderNotFound(f"order {order_id} not found", order_id=order_id)
        return order

    async def get_delivered_code(self, order_id: str) -> str | None:
        delivery = await self._deliveries.get_succeeded(order_id)
        return delivery.code if delivery else None

    async def list_deliveries(self, order_id: str) -> Sequence[Delivery]:
        return await self._deliveries.list_for_order(order_id)
