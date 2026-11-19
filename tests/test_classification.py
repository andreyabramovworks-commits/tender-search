import pytest

from tender_search.classification import DIRECT, NON_TARGET, PARTNER, classify, deduplicate_records


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


def test_price_below_200k_is_non_target_but_zero_is_kept():
    assert classify({"title": "Поставка клиновых строительных лесов", "price": "199 999", "lot_names": ""}).bucket == NON_TARGET
    assert classify({"title": "Поставка клиновых строительных лесов", "price": "0", "lot_names": ""}).bucket == DIRECT


def test_cross_platform_duplicate_uses_procurement_number_not_title_only():
    records = [
        {"id": "20", "number": "123-ABC", "title": "Леса", "source_url": "https://b", "price": "300000"},
        {"id": "10", "number": "123-ABC", "title": "Леса", "source_url": "https://a", "price": "300000"},
        {"id": "30", "number": "456-ABC", "title": "Леса", "price": "300000"},
    ]
    assert [row["id"] for row in deduplicate_records(records)] == ["10", "30"]
