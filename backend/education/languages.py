"""Bounded locale negotiation for reviewed content; never triggers generation."""
import re

_TAG = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z]{2})?$")


def normalize(value):
    if not _TAG.fullmatch(value):
        return None
    parts = value.split('-')
    return parts[0].lower() + ('-' + parts[1].upper() if len(parts) == 2 else '')


def preferences(language=None, accept_language=None):
    # An explicit query always wins, including explicit Korean.
    if language is not None:
        return language
    ranked = []
    excluded = set()
    for index, entry in enumerate((accept_language or '')[:4096].split(',')[:32]):
        parts = [part.strip() for part in entry.split(';')]
        tag = normalize(parts[0])
        if tag is None:
            continue
        quality = 1.0
        if len(parts) > 1:
            if len(parts) != 2 or not re.fullmatch(r'q=(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)', parts[1]):
                continue
            quality = float(parts[1][2:])
        if quality == 0:
            excluded.add(tag)
        else:
            ranked.append((-quality, index, tag))
    ordered = []
    for _, _, tag in sorted(ranked):
        for candidate in (tag, tag.split('-')[0]):
            if candidate not in excluded and candidate not in ordered:
                ordered.append(candidate)
    return tuple(ordered) or ('ko',)


def requested(language):
    return language if isinstance(language, str) else language[0]


def candidates(language):
    if not isinstance(language, str):
        yield from language
        return
    yield language
    base = language.split('-')[0]
    if base != language:
        yield base


def protect_worker_response(response):
    """Worker content depends on identity and locale; never share/cache it."""
    if response is None:  # Internal callers may invoke the route directly.
        return
    response.headers['Cache-Control'] = 'private, no-store'
    vary = [value.strip() for value in response.headers.get('Vary', '').split(',') if value.strip()]
    if '*' not in vary and not any(value.lower() == 'accept-language' for value in vary):
        vary.append('Accept-Language')
    response.headers['Vary'] = ', '.join(vary)
