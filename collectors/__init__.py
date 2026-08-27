from collectors.avito import AvitoCollector
from collectors.base import BaseCollector
from collectors.cian import CianCollector
from collectors.domclick import DomclickCollector
from collectors.mirkvartir import MirKvartirCollector
from collectors.yandex_realty import YandexRealtyCollector

COLLECTORS: dict[str, BaseCollector] = {
    "yandex_realty": YandexRealtyCollector(),
    "domclick": DomclickCollector(),
    "cian": CianCollector(),
    "avito": AvitoCollector(),
    "mirkvartir": MirKvartirCollector(),
}

__all__ = ["COLLECTORS", "BaseCollector"]
