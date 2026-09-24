import pytest

from winescan.catalog.strapi_names import (
    is_format_variant,
    parse_upload,
    photo_key,
    separator_key,
    split_extension,
)


@pytest.mark.parametrize(
    ("csv_photo_name", "upload_filename"),
    [
        # CamelCase разбит подчёркиванием
        ("DSC09173.webp", "DSC_09173_4a9ff95cc2.webp"),
        ("fpZEJhYZPstBYLV_1775199670.webp", "fp_ZE_Jh_YZ_Pst_BYLV_1775199670_92154c67f4.webp"),
        # кириллица, длинное тире, «копия»
        ("Агора Резерв Яхтинг Совиньон — копия.webp", "Agora_Rezerv_Yahting_Sovinon_kopiya_066436e3ad.webp"),
        # точки внутри имени не считаются расширением
        ("Спуманте белый брют.webp", "Spumante_belyj_bryut_d34e854a7b.webp"),
        # регистр расширения не важен
        ("slug-name-13.WEBP", "slug_name_13_0123456789.webp"),
        # «ц» -> «cz», «ь» выпадает
        ("Поместье Голубицкое Рислинг.webp", "Pomeste_Golubiczkoe_Risling_0123456789.webp"),
        # «№» выбрасывается
        (
            "Мильстрим Селлар (Cellar) Бленд №4, сухое, красное.webp",
            "Milstrim_Sellar_Cellar_Blend_4_suhoe_krasnoe_b6bf6ca178.webp",
        ),
    ],
)
def test_csv_photo_name_matches_upload_key(csv_photo_name, upload_filename):
    assert photo_key(csv_photo_name) == parse_upload(upload_filename).key


def test_separator_key_keeps_word_boundaries():
    joined = parse_upload("shato_pino_aligoterkatsiteli_beloe_suhoe_12_c00c55cc32.webp")
    separated = parse_upload("shato_pino_aligote_rkatsiteli_beloe_suhoe_12_c9a1a8a474.webp")

    assert joined.key == separated.key
    assert separator_key("shato-pino-aligoterkatsiteli-beloe-suhoe-12") == separator_key(joined.base)
    assert separator_key("shato-pino-aligoterkatsiteli-beloe-suhoe-12") != separator_key(separated.base)


def test_parse_upload_splits_hash_and_extension():
    upload = parse_upload("Screenshot_16_a237793391.webp")
    assert (upload.base, upload.file_hash, upload.ext) == ("Screenshot_16", "a237793391", ".webp")


def test_parse_upload_without_hash_keeps_whole_stem():
    upload = parse_upload("Logo.png")
    assert (upload.base, upload.file_hash, upload.key) == ("Logo", None, "logo")


def test_split_extension_ignores_non_image_suffix():
    assert split_extension("Алиготе, Цитрон бел.сух") == ("Алиготе, Цитрон бел.сух", "")


def test_format_variant_requires_existing_original():
    names = {"bottle_0123456789.webp", "thumbnail_bottle_0123456789.webp", "small_cat_9876543210.webp"}
    assert is_format_variant("thumbnail_bottle_0123456789.webp", names)
    assert not is_format_variant("small_cat_9876543210.webp", names)
    assert not is_format_variant("bottle_0123456789.webp", names)
