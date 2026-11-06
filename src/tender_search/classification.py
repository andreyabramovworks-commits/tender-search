"""Deterministic routing. Rules are editable in the Google Sheets reference tab."""

import re
from dataclasses import dataclass

DIRECT, PARTNER, NON_TARGET = "direct", "partner", "non_target"


@dataclass(frozen=True)
class Decision:
    bucket: str
    label: str
    reason: str
    needs_review: bool = False


def default_rules():
    # all: every line must match. Prefix a line with ! to require its absence.
    return [
        (
            "exclude-property",
            10,
            NON_TARGET,
            "Продажа имущества",
            r"\b(продаж|реализац|аукцион).*\b(имуществ|земельн|нежил|помещен|квартир|машино-мест)",
            "",
            "Исключено: продажа или реализация имущества",
        ),
        (
            "exclude-security",
            20,
            NON_TARGET,
            "Пультовая охрана",
            r"\b(пультов\w*\s+(?:охран|централ|наблюден)|охран\w*\s+.*пульт|тревожн\w*\s+кнопк|охранн\w*\s+сигнализац)",
            "",
            "Исключено: пультовая охрана или охранная сигнализация",
        ),
        (
            "exclude-costumes",
            30,
            NON_TARGET,
            "Костюмы и одежда сцены",
            r"\b(костюм|обув|пошив|одежд[аы]?\s+сцен|театральн\w*\s+одежд|фурнитур)\w*",
            "",
            "Исключено: костюмы, обувь, пошив или одежда сцены",
        ),
        (
            "exclude-electronics",
            40,
            NON_TARGET,
            "РЭБ и электроника БПЛА",
            r"\b(рэб|радиоэлектрон|радиолокац|обнаружен\w*\s+(?:бпла|дрон)|подавлен\w*\s+(?:бпла|дрон)|средств\w*\s+связи)",
            "",
            "Исключено: электроника, РЭБ или обнаружение БПЛА",
        ),
        (
            "exclude-address",
            50,
            NON_TARGET,
            "Адрес «Фестивальная»",
            r"\b(?:ул\.|улиц[аые]|проспект|шоссе)\s+фестивальн",
            "",
            "Исключено: «Фестивальная» указана как адрес, не мероприятие",
        ),
        (
            "direct-events",
            100,
            DIRECT,
            "Сцены и конструкции",
            "",
            r"\b(подиум|трибун|танцпол|шат[её]р|сценическ\w*\s+(?:конструк|комплекс|модул|ножнич|площад)|мобил\w*\s+огражд|барьер|декорац|оформлен\w*\s+(?:мероприят|вход|арок|площад|концерт|событ)|монтаж\w*.{0,50}(?:сцен|подиум|трибун|шат[её]р|декорац))",
            "Подходит: есть наш объект для изготовления, аренды или монтажа",
        ),
        (
            "direct-scaffolding",
            110,
            DIRECT,
            "Монтаж строительных лесов",
            r"\b(строительн\w*\s+лес|клинов\w*\s+лес|хомут\w*\s+лес|подмост|вышк\w*[- ]?тур)\n\b(строительств|монтаж|демонтаж|установк|возведен)\w*",
            "",
            "Подходит: монтаж или строительство лесов",
        ),
        (
            "direct-uav",
            120,
            DIRECT,
            "Монтаж защиты от БПЛА",
            r"\b(?:бпла|беспилот|дрон|антидрон|противодрон)\n\b(сетк|каркас|огражд|укрыт|загражд|улавливающ)\n\b(монтаж|демонтаж|установк|строительств)\w*",
            "",
            "Подходит: монтаж физической защиты от БПЛА",
        ),
        (
            "partner-light",
            200,
            PARTNER,
            "Свет, звук и экраны",
            r"!\b(подиум|трибун|танцпол|шат[её]р|декорац)\w*",
            r"\b(светов|звуков|экран|мультимед)\w*",
            "Партнёрам: чистый свет, звук или экран",
        ),
        (
            "partner-supply",
            210,
            PARTNER,
            "Поставка или аренда лесов/БПЛА",
            r"\b(поставк|продаж|аренд|приобретен|закупк)\w*\n\b(лес|бпла|беспилот|дрон|сетк|огражд)",
            "",
            "Партнёрам: поставка или аренда лесов/защиты БПЛА",
        ),
        (
            "exclude-infrastructure",
            300,
            NON_TARGET,
            "Коммунальная инфраструктура",
            r"\b(канализац|водоснабж|водоотвед|теплоснабж|газоснабж|ливнев\w*\s+сет)\w*\n!\b(лес[аов]\w*|подмост|вышк\w*[- ]?тур|подиум|трибун|шат[её]р|огражд)\w*",
            "",
            "Исключено: коммунальная инфраструктура без нашего объекта",
        ),
    ]


def _matches(text, rule):
    for condition in filter(None, rule.get("all", "").replace("\\n", "\n").splitlines()):
        absent, pattern = condition.startswith("!"), condition.removeprefix("!")
        if bool(re.search(pattern, text, re.I)) == absent:
            return False
    choices = list(filter(None, rule.get("any", "").replace("\\n", "\n").splitlines()))
    return not choices or any(re.search(pattern, text, re.I) for pattern in choices)


def classify(record, rules=None):
    text = " ".join(str(record.get(key, "")) for key in ("title", "lot_names")).lower()
    if rules is None:
        rules = [
            dict(zip(("id", "priority", "bucket", "name", "all", "any", "reason"), row))
            for row in default_rules()
        ]
    for rule in sorted((x for x in rules if x.get("enabled", True)), key=lambda x: int(x["priority"])):
        if _matches(text, rule):
            review = rule["bucket"] == DIRECT and bool(re.search(r"\b(светов|звуков|экран)\w*", text, re.I))
            return Decision(
                rule["bucket"],
                {DIRECT: "Тендеры", PARTNER: "Партнёрам", NON_TARGET: "Нецелевые"}[rule["bucket"]],
                rule["reason"],
                review,
            )
    return Decision(NON_TARGET, "Нецелевые", "Исключено: нет подтверждённого объекта из нашего профиля")
