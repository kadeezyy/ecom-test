"""Иерархия ошибок.

Два независимых дерева:

* :class:`DomainError` — нарушение правил предметной области, транслируется
  в HTTP-код на границе транспорта;
* :class:`SupplierError` — инфраструктурный сбой интеграции. Ключевое деление
  здесь не «ошибка/успех», а **известно ли, выдал ли поставщик код**:
  :class:`SupplierRefused` (точно не выдал) против :class:`SupplierUnknown`
  (мог выдать — ответ не дошёл).
"""

from __future__ import annotations


class DomainError(Exception):

    code: str = "domain_error"
    http_status: int = 400

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


class NotFoundError(DomainError):
    code = "not_found"
    http_status = 404


class OrderNotFound(NotFoundError):
    code = "order_not_found"


class ProductNotFound(NotFoundError):
    code = "product_not_found"


class ProductInactive(DomainError):
    code = "product_inactive"
    http_status = 409


class InvalidTransition(DomainError):

    code = "invalid_transition"
    http_status = 409


class AmountMismatch(DomainError):

    code = "amount_mismatch"
    http_status = 422


class Unauthorized(DomainError):
    code = "unauthorized"
    http_status = 401


class SupplierError(Exception):

    def __init__(self, message: str, *, supplier: str, request_id: str) -> None:
        super().__init__(message)
        self.message = message
        self.supplier = supplier
        self.request_id = request_id


class SupplierRefused(SupplierError):
    """Поставщик **точно** не выдал код.

    Сюда попадают только доказуемо отрицательные исходы: ответ по контракту
    ``{"status": "error", "reason": ...}`` и ошибка установки соединения
    (запрос физически не был отправлен). Только из этого состояния разрешён
    фолбэк на другого поставщика.
    """

    def __init__(self, message: str, *, supplier: str, request_id: str, reason: str) -> None:
        super().__init__(message, supplier=supplier, request_id=request_id)
        self.reason = reason


class SupplierUnknown(SupplierError):
    """Исход неизвестен: поставщик мог успеть выдать код.

    Таймаут чтения, обрыв соединения, невалидный ответ. Фолбэк на другого
    поставщика из этого состояния **запрещён** — иначе возможна двойная
    выдача. Разрешён только повтор тому же поставщику с тем же ``request_id``.
    """

    def __init__(self, message: str, *, supplier: str, request_id: str, reason: str) -> None:
        super().__init__(message, supplier=supplier, request_id=request_id)
        self.reason = reason


class DeliveryConflict(Exception):
    """Код получен, но заказ уже был выдан другой попыткой.

    Ситуация аномальная (её предотвращают частичный уникальный индекс и
    единственность job'а на заказ), но если она случилась — код помечается
    ``superseded`` и попадает в отчёт сверки, а не теряется молча.
    """
