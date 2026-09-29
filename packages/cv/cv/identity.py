"""Conservative name-conflict evidence; never invent a match or lower a CV floor."""
from .text_fusion import query_tokens, token_sim
from .text_rerank import CatalogText

_GENERIC = query_tokens('''вино винное wine wines winery vineyards estate family семейная винодельня
усадьба поместье урожай vintage reserve резерв резерва коллекция collection серия series
терруар terroir линия line вторая second авторское красное белое розовое red white rose
сухое полусухое сладкое полусладкое dry sweet semi brut брют игристое sparkling выдержанное''')


def _matches(token, others):
    return any(token_sim(token, other) >= .8 for other in others)


def _distinct(text, context=()):
    return {t for t in query_tokens(text) if not t.isdigit()
            and not _matches(t, _GENERIC) and not _matches(t, context)}


def name_conflicts(fields: dict[str, str], candidate: CatalogText, ocr_text: str) -> bool:
    """Reject only a specific alternative name corroborated by independent OCR.

    Missing/generic fields, different winery aliases and unreadable OCR are not
    evidence of a mismatch. Sharing any distinctive name token is inconclusive.
    This deliberately favors abstaining from a veto over rejecting a true match.
    """
    if not fields.get('name') or not fields.get('winery') or not ocr_text:
        return False
    winery = _distinct(candidate.winery)
    read_winery = _distinct(fields['winery'])
    if not winery or not read_winery or sum(_matches(t, read_winery) for t in winery) / len(winery) < .5:
        return False
    context = query_tokens(' '.join([candidate.winery, candidate.grape,
                                    fields['winery'], fields.get('grapes', '')]))
    read_name = _distinct(fields['name'], context)
    catalog_name = _distinct(candidate.name, context)
    if not read_name or not catalog_name or any(_matches(t, catalog_name) for t in read_name):
        return False
    ocr = query_tokens(ocr_text)
    return any(_matches(t, ocr) for t in read_name) and not any(_matches(t, ocr) for t in catalog_name)
