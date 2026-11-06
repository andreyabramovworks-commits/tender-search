import pytest

from tender_search.classification import DIRECT, NON_TARGET, PARTNER, classify


@pytest.mark.parametrize(
    ("title", "bucket"),
    [
        ("Оказание услуг по пультовой централизованной охране", NON_TARGET),
        ("Пошив сценических костюмов для ансамбля", NON_TARGET),
        ("Продажа имущества, находящегося в собственности города Москвы", NON_TARGET),
        ("Реконструкция наружной сети канализации по ул. Фестивальной", NON_TARGET),
        ("Система РЭБ для обнаружения БПЛА", NON_TARGET),
        ("Транспортные услуги по перевозке артистов и декораций", NON_TARGET),
        ("Оказание услуги по оформлению площадок мягкой мебелью", NON_TARGET),
        ("Продажа трибуны", NON_TARGET),
        ("Закупка и поставка трибуны для стадиона", DIRECT),
        ("Аренда земельного участка с кадастровым номером в пер. Лесной", NON_TARGET),
        ("Утилизация вооружения. Лом черных металлов", NON_TARGET),
        ("Аренда светового и звукового оборудования", PARTNER),
        ("Поставка клиновых строительных лесов", DIRECT),
        ("Монтаж сценического подиума, ферм и трибун", DIRECT),
        ("Монтаж защитной сетки от БПЛА", DIRECT),
    ],
)
def test_business_routing(title, bucket):
    assert classify({"title": title, "lot_names": ""}).bucket == bucket


def test_mixed_event_with_screen_stays_direct_and_is_flagged_for_review():
    decision = classify({"title": "Монтаж подиума и аренда светодиодного экрана", "lot_names": ""})
    assert decision.bucket == DIRECT
    assert decision.needs_review
