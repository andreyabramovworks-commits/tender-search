"""Deterministic business routing for TenderLand candidates.

These rules intentionally operate only on a tender's visible title and lot names.
They do not claim that a document has been fully read: ambiguous procurements stay
in the main queue with a review flag, and every rejected candidate remains visible
on the ``Нецелевые`` tab.
"""

import re
from dataclasses import dataclass

DIRECT = "direct"
PARTNER = "partner"
NON_TARGET = "non_target"


@dataclass(frozen=True)
class Decision:
    bucket: str
    label: str
    reason: str
    needs_review: bool = False


def _has(text, pattern):
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _text(record):
    return " ".join(str(record.get(key, "")) for key in ("title", "lot_names")).lower()


def classify(record):
    """Return the current routing decision without changing the source record."""
    text = _text(record)

    # Clear false positives first. These are procurement subjects, not merely words
    # that might occur in a relevant bill of quantities.
    if _has(text, r"\b(продаж|реализац|аукцион).*\b(имуществ|земельн|нежил|помещен|квартир|машино-мест)"):
        return Decision(NON_TARGET, "Нецелевые", "Исключено: продажа или реализация имущества")
    if _has(
        text,
        r"\b(пультов\w*\s+(?:охран|централ|наблюден)|охран\w*\s+.*пульт|тревожн\w*\s+кнопк|охранн\w*\s+сигнализац)",
    ):
        return Decision(NON_TARGET, "Нецелевые", "Исключено: пультовая охрана или охранная сигнализация")
    if _has(text, r"\b(костюм|обув|пошив|одежд[аы]?\s+сцен|театральн\w*\s+одежд|фурнитур)\w*"):
        return Decision(NON_TARGET, "Нецелевые", "Исключено: костюмы, обувь, пошив или одежда сцены")
    if _has(
        text,
        r"\b(рэб|радиоэлектрон|радиолокац|обнаружен\w*\s+(?:бпла|дрон)|подавлен\w*\s+(?:бпла|дрон)|средств\w*\s+связи)",
    ):
        return Decision(NON_TARGET, "Нецелевые", "Исключено: электроника, РЭБ или обнаружение БПЛА")
    if _has(text, r"\b(канализац|водоснабж|водоотвед|теплоснабж|газоснабж|ливнев\w*\s+сет)\w*") and not _has(
        text,
        r"\b(лес[аов]\w*|подмост|вышк\w*[- ]?тур|клинов|хомут|сцен|подиум|трибун|шат[её]р|огражд|сетк\w*\s+(?:от|для).*(?:бпла|дрон))",
    ):
        return Decision(NON_TARGET, "Нецелевые", "Исключено: коммунальная инфраструктура без нашего объекта")
    # "Фестивальная" is an address; it is not a festival. Limit this exclusion to
    # address wording, so a real festival is unaffected.
    if _has(text, r"\b(?:ул\.|улиц[аые]|проспект|шоссе)\s+фестивальн"):
        return Decision(
            NON_TARGET, "Нецелевые", "Исключено: «Фестивальная» указана как адрес, не мероприятие"
        )

    direct_scene = _has(
        text,
        r"\b(подиум|трибун|танцпол|шат[её]р|ферм\w*\s+(?:сцен|алюм)|сценическ\w*\s+(?:конструк|комплекс|модул|ножнич|площад)|мобил\w*\s+огражд|барьер|декорац|оформлен\w*\s+(?:мероприят|вход|арок|площад|концерт|событ)|монтаж\w*.{0,50}(?:сцен|подиум|трибун|шат[её]р|декорац))",
    )
    direct_scaffold = _has(
        text, r"\b(строительн\w*\s+лес|клинов\w*\s+лес|хомут\w*\s+лес|подмост|вышк\w*[- ]?тур)"
    )
    direct_uav = _has(
        text,
        r"\b(?:бпла|беспилот|дрон|антидрон|противодрон).{0,80}\b(сетк|каркас|огражд|укрыт|загражд|улавливающ)",
    ) or _has(
        text,
        r"\b(сетк|каркас|огражд|укрыт|загражд|улавливающ).{0,80}\b(?:бпла|беспилот|дрон|антидрон|противодрон)",
    )

    partner_only = _has(text, r"\b(светов|звуков|экран|мультимед)\w*")
    supply_or_rent = _has(text, r"\b(поставк|продаж|аренд|приобретен|закупк)\w*")
    installation = _has(text, r"\b(строительств|монтаж|демонтаж|установк|возведен)\w*")

    if (direct_scaffold or direct_uav) and supply_or_rent and not installation:
        return Decision(PARTNER, "Партнёрам", "Не наша прямая работа: поставка или аренда лесов/защиты БПЛА")

    if direct_scene or direct_scaffold or direct_uav:
        # A direct object plus services that colleagues can perform is still a live
        # opportunity. It gets a visible review flag rather than being discarded.
        mixed = partner_only and (direct_scene or direct_scaffold or direct_uav)
        return Decision(
            DIRECT,
            "Тендеры",
            "Подходит: есть наш объект для изготовления, аренды или монтажа"
            + ("; есть смежные работы партнёров" if mixed else ""),
            needs_review=mixed,
        )
    if partner_only or (supply_or_rent and _has(text, r"\b(лес|бпла|беспилот|дрон|сетк|огражд)")):
        return Decision(
            PARTNER, "Партнёрам", "Не наша прямая работа: поставка/аренда или чистый свет, звук, экран"
        )
    return Decision(NON_TARGET, "Нецелевые", "Исключено: нет подтверждённого объекта из нашего профиля")
