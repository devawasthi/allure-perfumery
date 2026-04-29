from __future__ import annotations

import html
import re
from typing import Mapping


NOTE_BANK = [
    ("mandarin orange", "Mandarin Orange", "top"),
    ("calabria bergamot", "Calabria Bergamot", "top"),
    ("blood mandarin", "Blood Mandarin", "top"),
    ("pink pepper", "Pink Pepper", "top"),
    ("black pepper", "Black Pepper", "top"),
    ("sichuan pepper", "Sichuan Pepper", "top"),
    ("juniper berries", "Juniper Berries", "top"),
    ("blackcurrant", "Blackcurrant", "top"),
    ("red berries", "Red Berries", "top"),
    ("petitgrain", "Petitgrain", "top"),
    ("bergamot", "Bergamot", "top"),
    ("mandarin", "Mandarin", "top"),
    ("grapefruit", "Grapefruit", "top"),
    ("orange", "Orange", "top"),
    ("lemon", "Lemon", "top"),
    ("lime", "Lime", "top"),
    ("yuzu", "Yuzu", "top"),
    ("neroli", "Neroli", "top"),
    ("cardamom", "Cardamom", "top"),
    ("saffron", "Saffron", "top"),
    ("ginger", "Ginger", "top"),
    ("mint", "Mint", "top"),
    ("aldehydes", "Aldehydes", "top"),
    ("green notes", "Green Notes", "top"),
    ("aquatic notes", "Aquatic Notes", "top"),
    ("marine notes", "Marine Notes", "top"),
    ("sea salt", "Sea Salt", "top"),
    ("salt", "Salt", "top"),
    ("apple", "Apple", "top"),
    ("pineapple", "Pineapple", "top"),
    ("pear", "Pear", "top"),
    ("peach", "Peach", "top"),
    ("plum", "Plum", "top"),
    ("rhubarb", "Rhubarb", "top"),
    ("coconut", "Coconut", "top"),
    ("orange blossom", "Orange Blossom", "heart"),
    ("lily-of-the-valley", "Lily-of-the-Valley", "heart"),
    ("violet leaf", "Violet Leaf", "heart"),
    ("orris root", "Orris Root", "heart"),
    ("ylang-ylang", "Ylang-Ylang", "heart"),
    ("clary sage", "Clary Sage", "heart"),
    ("water lily", "Water Lily", "heart"),
    ("dried fruits", "Dried Fruits", "heart"),
    ("rose absolute", "Rose Absolute", "heart"),
    ("jasmine", "Jasmine", "heart"),
    ("rose", "Rose", "heart"),
    ("tuberose", "Tuberose", "heart"),
    ("iris", "Iris", "heart"),
    ("orris", "Orris", "heart"),
    ("violet", "Violet", "heart"),
    ("geranium", "Geranium", "heart"),
    ("magnolia", "Magnolia", "heart"),
    ("freesia", "Freesia", "heart"),
    ("lily", "Lily", "heart"),
    ("narcissus", "Narcissus", "heart"),
    ("heliotrope", "Heliotrope", "heart"),
    ("osmanthus", "Osmanthus", "heart"),
    ("lavender", "Lavender", "heart"),
    ("cinnamon", "Cinnamon", "heart"),
    ("nutmeg", "Nutmeg", "heart"),
    ("clove", "Clove", "heart"),
    ("cloves", "Clove", "heart"),
    ("tea", "Tea", "heart"),
    ("sage", "Sage", "heart"),
    ("rosemary", "Rosemary", "heart"),
    ("cannabis", "Cannabis", "heart"),
    ("wildflowers", "Wildflowers", "heart"),
    ("honey", "Honey", "heart"),
    ("coffee", "Coffee", "heart"),
    ("cacao", "Cacao", "heart"),
    ("white musk", "White Musk", "base"),
    ("amber woods", "Amber Woods", "base"),
    ("amberwood", "Amberwood", "base"),
    ("cashmere wood", "Cashmere Wood", "base"),
    ("guaiac wood", "Guaiac Wood", "base"),
    ("virginia cedar", "Virginia Cedar", "base"),
    ("cedarwood", "Cedarwood", "base"),
    ("sandalwood", "Sandalwood", "base"),
    ("tonka bean", "Tonka Bean", "base"),
    ("frankincense", "Frankincense", "base"),
    ("oakmoss", "Oakmoss", "base"),
    ("labdanum", "Labdanum", "base"),
    ("benzoin", "Benzoin", "base"),
    ("ambergris", "Ambergris", "base"),
    ("patchouli", "Patchouli", "base"),
    ("vetiver", "Vetiver", "base"),
    ("vanilla", "Vanilla", "base"),
    ("tonka", "Tonka Bean", "base"),
    ("cedar", "Cedar", "base"),
    ("musk", "Musk", "base"),
    ("amber", "Amber", "base"),
    ("oud", "Oud", "base"),
    ("agarwood", "Agarwood", "base"),
    ("leather", "Leather", "base"),
    ("suede", "Suede", "base"),
    ("tobacco", "Tobacco", "base"),
    ("incense", "Incense", "base"),
    ("myrrh", "Myrrh", "base"),
    ("moss", "Moss", "base"),
    ("cypriol", "Cypriol", "base"),
    ("papyrus", "Papyrus", "base"),
    ("praline", "Praline", "base"),
    ("caramel", "Caramel", "base"),
    ("marshmallow", "Marshmallow", "base"),
    ("smoke", "Smoke", "base"),
    ("birch", "Birch", "base"),
    ("civet", "Civet", "base"),
    ("pine", "Pine", "base"),
]

SORTED_NOTE_BANK = sorted(NOTE_BANK, key=lambda item: len(item[0]), reverse=True)

LAYER_PATTERNS = {
    "top": [
        r"\btop notes?\s*(?:are|is|include|includes|:|-)?\s*(?P<notes>[^.;]+)",
        r"\bopening notes?\s*(?:are|is|include|includes|of|with|:|-)?\s*(?P<notes>[^.;]+)",
        r"\bopens with\s*(?P<notes>[^.;]+)",
        r"\bbegins with\s*(?P<notes>[^.;]+)",
        r"\bstarts with\s*(?P<notes>[^.;]+)",
    ],
    "heart": [
        r"(?P<notes>[^.;]{0,180}?)\s+(?:form|forms|make|makes)\s+(?:the )?(?:perfume'?s )?heart",
        r"\bmiddle notes?\s*(?:are|is|include|includes|:|-)?\s*(?P<notes>[^.;]+)",
        r"\bheart notes?\s*(?:are|is|include|includes|:|-)?\s*(?P<notes>[^.;]+)",
        r"\b(?:the )?heart\s*(?:is|of|with|has|features|reveals|blends)\s*(?P<notes>[^.;]+)",
        r"\bleads? (?:to|into)\s*(?:a |an |the )?(?:heart|middle)\s*(?:of|with)?\s*(?P<notes>[^.;]+)",
    ],
    "base": [
        r"\bbase notes?\s*(?:are|is|include|includes|:|-)?\s*(?P<notes>[^.;]+)",
        r"\b(?:at|in|on)?\s*(?:the )?base\s*(?:is|of|with|has|features|includes|settles into|by)\s*(?P<notes>[^.;]+)",
        r"\bdrydown\s*(?:is|of|with|into|reveals)?\s*(?P<notes>[^.;]+)",
    ],
}

STOP_MARKERS = [
    " top note",
    " top notes",
    " opening note",
    " middle note",
    " middle notes",
    " heart note",
    " heart notes",
    " base note",
    " base notes",
    " drydown",
]

FILLER_RE = re.compile(
    r"\b(?:notes?|accords?|essences?|absolute|aromas?|facets?|fragrance|perfume|scent|"
    r"include|includes|including|with|of|the|a|an|and|then|finally|while|before|"
    r"warm|fresh|intense|rich|sweet|soft|deep|smooth|creamy|sensual|luminous|"
    r"spicy|woody|floral|aromatic|oriental|modern|classic|beautiful)\b",
    re.IGNORECASE,
)


def extract_note_pyramid(row: Mapping[str, str]) -> dict[str, list[str]]:
    """Build a useful note pyramid from Scentoria source text.

    The source catalog does not expose dedicated note columns. This keeps explicit
    note pyramids when the description has them, then fills gaps from recognized
    note words and finally from the product's scent family.
    """

    text = _source_text(row)
    notes = {"top": [], "heart": [], "base": []}

    for layer, patterns in LAYER_PATTERNS.items():
        notes[layer] = _extract_labeled_notes(text, patterns)

    classified = _classify_note_mentions(text)
    for layer in ("top", "heart", "base"):
        if not notes[layer]:
            notes[layer] = classified[layer]

    fallback = _fallback_notes(row)
    for layer in ("top", "heart", "base"):
        if not notes[layer]:
            notes[layer] = fallback[layer]
        notes[layer] = notes[layer][:5]

    return {
        "top_notes": notes["top"],
        "heart_notes": notes["heart"],
        "base_notes": notes["base"],
    }


def _source_text(row: Mapping[str, str]) -> str:
    parts = [
        row.get("description", ""),
        row.get("tags", ""),
        row.get("family", ""),
        row.get("perfume_name", ""),
    ]
    text = " ".join(part for part in parts if part)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("¬†", " ")
    text = text.replace("Õ", "'").replace("Ò", '"').replace("Ó", '"').replace("Ñ", "-")
    return re.sub(r"\s+", " ", text).strip()


def _extract_labeled_notes(text: str, patterns: list[str]) -> list[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        segment = _trim_segment(match.group("notes"))
        notes = _notes_from_segment(segment)
        if notes:
            return notes[:5]
    return []


def _trim_segment(segment: str) -> str:
    lowered = f" {segment.lower()}"
    cut_at = len(segment)
    for marker in STOP_MARKERS:
        position = lowered.find(marker)
        if position > 1:
            cut_at = min(cut_at, position - 1)
    segment = segment[:cut_at]
    return segment.strip(" :-,")


def _notes_from_segment(segment: str) -> list[str]:
    known = [display for _, display, _ in _note_matches(segment)]
    if known:
        return _dedupe(known)

    cleaned = FILLER_RE.sub(" ", segment)
    cleaned = re.sub(r"\s+", " ", cleaned)
    parts = re.split(r",|/|;|\s+\+\s+|\s+&\s+|\s+and\s+", cleaned, flags=re.IGNORECASE)
    notes = []
    for part in parts:
        value = part.strip(" .:-")
        value = re.sub(r"\s+", " ", value)
        if not value or len(value) < 3:
            continue
        if len(value.split()) > 3:
            continue
        if re.search(r"\b(?:start|rear|composition|journey|skin|bottle|collection)\b", value, re.IGNORECASE):
            continue
        notes.append(value.title())
    return _dedupe(notes)


def _classify_note_mentions(text: str) -> dict[str, list[str]]:
    layers = {"top": [], "heart": [], "base": []}
    for _, display, layer in _note_matches(text):
        layers[layer].append(display)
    return {layer: _dedupe(values)[:5] for layer, values in layers.items()}


def _note_matches(text: str) -> list[tuple[int, str, str]]:
    matches: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    lower = text.lower()

    for needle, display, layer in SORTED_NOTE_BANK:
        pattern = _needle_pattern(needle)
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            span = match.span()
            if any(not (span[1] <= used[0] or span[0] >= used[1]) for used in occupied):
                continue
            occupied.append(span)
            matches.append((span[0], span[1], display, layer))

    matches.sort(key=lambda item: item[0])
    return [(start, display, layer) for start, _, display, layer in matches]


def _needle_pattern(needle: str) -> str:
    escaped = re.escape(needle.lower())
    escaped = escaped.replace(r"\ ", r"[\s-]+")
    return rf"(?<![a-z]){escaped}(?![a-z])"


def _fallback_notes(row: Mapping[str, str]) -> dict[str, list[str]]:
    family_text = " ".join(
        [
            row.get("family", ""),
            row.get("tags", ""),
            row.get("description", ""),
        ]
    ).lower()

    rules = [
        (r"citrus", ["Citrus"], ["Neroli"], ["Musk"]),
        (r"aquatic|marine", ["Citrus"], ["Marine Notes"], ["White Musk"]),
        (r"green", ["Green Notes"], ["Herbs"], ["Vetiver"]),
        (r"rose", ["Pink Pepper"], ["Rose"], ["Musk"]),
        (r"floral", ["Bergamot"], ["Florals"], ["Musk"]),
        (r"fougere|aromatic", ["Bergamot"], ["Lavender"], ["Oakmoss"]),
        (r"chypre", ["Bergamot"], ["Florals"], ["Oakmoss"]),
        (r"leather", ["Saffron"], ["Leather"], ["Amber"]),
        (r"tobacco", ["Spices"], ["Tobacco"], ["Tonka Bean"]),
        (r"gourmand", ["Sweet Notes"], ["Vanilla"], ["Tonka Bean"]),
        (r"amber|oriental", ["Spices"], ["Amber"], ["Vanilla"]),
        (r"spicy", ["Spices"], ["Cinnamon"], ["Woods"]),
        (r"fruity", ["Fruits"], ["Florals"], ["Musk"]),
        (r"woody|wood", ["Bergamot"], ["Cedarwood"], ["Sandalwood"]),
        (r"musky|musk", ["Aldehydes"], ["Musk"], ["Woods"]),
    ]

    for pattern, top, heart, base in rules:
        if re.search(pattern, family_text):
            return {"top": top, "heart": heart, "base": base}

    return {
        "top": ["Fresh Notes"],
        "heart": ["Signature Accord"],
        "base": ["Musk"],
    }


def _merge(primary: list[str], secondary: list[str]) -> list[str]:
    return _dedupe([*primary, *secondary])


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip(" ,.;:-")
        if not cleaned:
            continue
        key = re.sub(r"[^a-z0-9]+", "", cleaned.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result
