"""Name guards require independent evidence, not just a high model confidence."""
from cv.identity import name_conflicts
from cv.text_rerank import CatalogText

CANDIDATE = CatalogText(slug='south', name='Южный лес', winery='Усадьба Дивноморское', grape='Мерло')
FIELDS = {'name': 'Вечерница', 'winery': 'Усадьба Дивноморское'}


def test_distinct_name_corroborated_by_ocr_is_a_conflict():
    assert name_conflicts(FIELDS, CANDIDATE, 'Усадьба Дивноморское Вечерница')


def test_vlm_alone_cannot_veto_the_candidate():
    assert not name_conflicts(FIELDS, CANDIDATE, '')
    assert not name_conflicts(FIELDS, CANDIDATE, 'Усадьба Дивноморское')


def test_ocr_mentions_candidate_so_conflict_is_inconclusive():
    assert not name_conflicts(FIELDS, CANDIDATE, 'Вечерница Южный лес')


def test_generic_grape_name_does_not_veto_a_product_name():
    fields = {'name': 'Мерло сухое красное', 'winery': 'Усадьба Дивноморское', 'grapes': 'Мерло'}
    assert not name_conflicts(fields, CANDIDATE, 'Мерло сухое красное')


def test_matching_name_and_cyrillic_latin_transliteration():
    fields = {'name': 'Южный лес', 'winery': 'Усадьба Дивноморское'}
    assert not name_conflicts(fields, CANDIDATE, 'Южный лес')
    fields['name'] = 'Yuzhnyy les'
    assert not name_conflicts(fields, CANDIDATE, 'Южный лес')


def test_unknown_winery_alias_is_not_proof_of_a_name_conflict():
    assert not name_conflicts({**FIELDS, 'winery': 'another brand'}, CANDIDATE, 'Вечерница')


def test_missing_fields_and_generic_catalog_name_do_not_veto():
    assert not name_conflicts({}, CANDIDATE, 'Вечерница')
    generic = CatalogText(slug='merlot', name='Мерло', winery='Усадьба Дивноморское', grape='Мерло')
    assert not name_conflicts(FIELDS, generic, 'Вечерница')
