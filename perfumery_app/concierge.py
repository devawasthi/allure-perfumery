from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any


STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "around",
    "below",
    "best",
    "budget",
    "can",
    "for",
    "from",
    "give",
    "have",
    "into",
    "like",
    "looking",
    "most",
    "need",
    "perfume",
    "please",
    "recommend",
    "scent",
    "smell",
    "something",
    "that",
    "the",
    "under",
    "want",
    "with",
}

POPULARITY_TERMS = {
    "famous",
    "famoud",
    "popular",
    "iconic",
    "known",
    "classic",
    "bestseller",
    "bestsellers",
    "hyped",
    "recognisable",
    "recognizable",
}

ICONIC_SEARCH_TERMS = [
    "aventus",
    "bleu de chanel",
    "sauvage",
    "baccarat rouge",
    "acqua di gio",
    "terre d hermes",
    "oud wood",
    "naxos",
    "layton",
    "angels share",
    "angel s share",
    "coco mademoiselle",
    "black opium",
    "libre",
    "good girl",
    "eros",
    "le male",
    "light blue",
    "la vie est belle",
    "ombre leather",
    "tobacco vanille",
    "lost cherry",
    "portrait of a lady",
    "reflection",
    "grand soir",
    "hacivat",
]

ICONIC_SIGNATURES = [
    (("creed",), (r"\baventus\b",), 100),
    (("chanel",), (r"\bbleu de chanel\b",), 98),
    (("christian dior", "dior"), (r"\bsauvage\b",), 96),
    (("mfk", "maison francis kurkdjian"), (r"\bbaccarat rouge 540\b",), 95),
    (("giorgio armani",), (r"\bacqua di gio\b",), 94),
    (("hermes",), (r"\bterre d hermes\b",), 92),
    (("tom ford",), (r"\boud wood\b",), 90),
    (("xerjoff",), (r"\bnaxos\b",), 88),
    (("parfums de marly",), (r"\blayton\b",), 87),
    (("kilian", "by kilian"), (r"\bangels share\b", r"\bangel s share\b"), 86),
    (("chanel",), (r"\bno 5\b", r"\bn 5\b"), 85),
    (("chanel",), (r"\bcoco mademoiselle\b",), 84),
    (("ysl", "yves saint laurent"), (r"\bblack opium\b",), 83),
    (("ysl", "yves saint laurent"), (r"\blibre\b",), 82),
    (("carolina herrera",), (r"\bgood girl\b",), 81),
    (("dolce gabbana", "dolce and gabbana"), (r"\blight blue\b",), 80),
    (("lancome",), (r"\bla vie est belle\b",), 79),
    (("giorgio armani",), (r"\bsi\b",), 78),
    (("versace",), (r"\beros\b",), 77),
    (("jean paul gaultier",), (r"\ble male\b",), 76),
    (("tom ford",), (r"\bombre leather\b",), 75),
    (("tom ford",), (r"\btobacco vanille\b",), 74),
    (("tom ford",), (r"\blost cherry\b",), 73),
    (("frederic malle",), (r"\bportrait of a lady\b",), 72),
    (("amouage",), (r"\breflection\b",), 71),
    (("mfk", "maison francis kurkdjian"), (r"\bgrand soir\b",), 70),
    (("nishane",), (r"\bhacivat\b",), 69),
]

NON_CANONICAL_TERMS = {
    "partial",
    "gift set",
    "sample set",
    "miniature",
    "miniatures",
    "deodorant",
    "hair care",
    "travel set",
    "case",
    "roll on",
}

FLANKER_TERMS = {
    "absolu",
    "cologne",
    "eau forte",
    "eau fraiche",
    "eau givree",
    "exclusif",
    "for her",
    "gone bad",
    "glitter",
    "illicit",
    "intense",
    "intensement",
    "limited edition",
    "nuit blanche",
    "over red",
    "profondo",
    "profumo",
    "supreme",
    "very good girl",
}

NOTE_TERMS = {
    "amber",
    "aquatic",
    "aromatic",
    "blue",
    "citrus",
    "clean",
    "fresh",
    "floral",
    "gourmand",
    "iris",
    "leather",
    "marine",
    "musk",
    "musky",
    "oud",
    "patchouli",
    "powdery",
    "rose",
    "sandalwood",
    "smoky",
    "spicy",
    "sweet",
    "tobacco",
    "vanilla",
    "vetiver",
    "woody",
}

OCCASION_TERMS = {
    "date": ["amber", "vanilla", "sweet", "woody", "musk"],
    "evening": ["amber", "spicy", "tobacco", "leather", "oud", "vanilla"],
    "night": ["amber", "spicy", "tobacco", "leather", "oud", "vanilla"],
    "office": ["fresh", "clean", "aromatic", "citrus", "vetiver", "woody"],
    "party": ["sweet", "amber", "spicy", "vanilla", "oud"],
    "summer": ["fresh", "citrus", "aquatic", "marine", "clean"],
    "wedding": ["floral", "amber", "woody", "musk", "rose"],
    "winter": ["amber", "vanilla", "spicy", "tobacco", "oud"],
}


class ScentConcierge:
    def __init__(self, settings: Any, database: Any):
        self.settings = settings
        self.database = database

    def recommend(self, message: str) -> dict[str, Any]:
        prompt = self._clean_message(message)
        preferences = self._parse_preferences(prompt)
        candidates = self.database.get_concierge_candidates(preferences["query_terms"], limit=180)
        ranked = self._rank_candidates(candidates, preferences, prompt)
        if preferences["popularity_intent"]:
            ranked = self._diversify_popular_results(ranked)

        if not ranked:
            return {
                "mode": "local",
                "reply": "I could not find an available match for that edit right now.",
                "recommendations": [],
            }

        shortlist = ranked[:8]
        if self.settings.openai_api_key:
            try:
                return self._ai_response(prompt, preferences, shortlist)
            except Exception:
                pass

        return self._local_response(preferences, shortlist[:4])

    def _clean_message(self, message: str) -> str:
        prompt = re.sub(r"\s+", " ", str(message or "")).strip()
        return prompt[:600]

    def _parse_preferences(self, prompt: str) -> dict[str, Any]:
        lower = prompt.lower()
        terms = [term for term in re.findall(r"[a-z0-9][a-z0-9'-]{2,}", lower) if term not in STOP_WORDS]
        popularity_intent = bool(
            POPULARITY_TERMS.intersection(terms)
            or re.search(r"\bmost\s+(?:famous|popular|iconic|known)\b", lower)
        )

        budget = None
        for amount, suffix in re.findall(r"(?:rs\.?|inr|₹)?\s*([1-9][0-9,]{2,5})\s*(k)?", lower):
            parsed = int(amount.replace(",", ""))
            if suffix:
                parsed *= 1000
            if parsed >= 300:
                budget = parsed if budget is None else min(budget, parsed)

        gender = ""
        if re.search(r"\b(him|men|man|male|masculine|boyfriend|husband)\b", lower):
            gender = "him"
        elif re.search(r"\b(her|women|woman|female|feminine|girlfriend|wife)\b", lower):
            gender = "her"
        elif "unisex" in lower:
            gender = "unisex"

        collection_type = ""
        if "niche" in lower:
            collection_type = "niche"
        elif "designer" in lower:
            collection_type = "designer"

        sale_type = ""
        if re.search(r"\b(decant|sample|travel)\b", lower):
            sale_type = "decant"
        elif "tester" in lower:
            sale_type = "tester"
        elif re.search(r"\b(partial|used bottle)\b", lower):
            sale_type = "partial"
        elif re.search(r"\b(retail|full bottle|sealed)\b", lower):
            sale_type = "retail"

        note_terms = sorted({term for term in terms if term in NOTE_TERMS})
        occasions = sorted({occasion for occasion in OCCASION_TERMS if occasion in lower})
        for occasion in occasions:
            note_terms.extend(term for term in OCCASION_TERMS[occasion] if term not in note_terms)

        expanded_terms = []
        for term in [*terms, gender, collection_type, sale_type, *note_terms]:
            if term and term not in expanded_terms:
                expanded_terms.append(term)

        query_terms = list(expanded_terms)
        if popularity_intent:
            query_terms = [
                term
                for term in [*ICONIC_SEARCH_TERMS, *note_terms, collection_type, sale_type]
                if term and term not in POPULARITY_TERMS
            ]

        return {
            "budget": budget,
            "gender": gender,
            "collection_type": collection_type,
            "sale_type": sale_type,
            "note_terms": note_terms,
            "occasions": occasions,
            "terms": expanded_terms,
            "query_terms": query_terms,
            "popularity_intent": popularity_intent,
        }

    def _rank_candidates(
        self,
        candidates: list[dict[str, Any]],
        preferences: dict[str, Any],
        prompt: str,
    ) -> list[dict[str, Any]]:
        ranked = []
        prompt_terms = [term for term in preferences["terms"] if len(term) >= 3]
        for candidate in candidates:
            variant = self._choose_variant(candidate["variants"], preferences)
            if not variant:
                continue

            haystack = " ".join(
                [
                    candidate["brand"],
                    candidate["name"],
                    candidate["collection_type"],
                    candidate["gender"],
                    candidate["family"],
                    candidate["concentration"],
                    candidate["description"],
                    candidate["signature"],
                    " ".join(candidate["notes"]),
                ]
            ).lower()

            score = 0
            fame_score, fame_key = self._iconic_match(candidate)
            if candidate["featured"]:
                score += 4
            if preferences["popularity_intent"]:
                score += fame_score
            for term in prompt_terms:
                if preferences["popularity_intent"] and term in POPULARITY_TERMS:
                    continue
                if term in haystack:
                    score += 3
            if preferences["gender"]:
                if candidate["gender"] == preferences["gender"]:
                    score += 16 if preferences["popularity_intent"] else 8
                elif candidate["gender"] == "unisex":
                    score += 2 if preferences["popularity_intent"] else 4
                else:
                    score -= 40
            if preferences["collection_type"] and candidate["collection_type"] == preferences["collection_type"]:
                score += 7
            if preferences["sale_type"] and variant["sale_type"] == preferences["sale_type"]:
                score += 7
            if preferences["budget"]:
                if variant["price_inr"] <= preferences["budget"]:
                    score += 9
                else:
                    score -= 10
            for term in preferences["note_terms"]:
                if term in haystack:
                    score += 4
            if re.search(r"\b(long lasting|beast|projection|projects|strong)\b", prompt.lower()):
                if candidate["concentration"].lower() in {"parfum", "extrait", "extrait de parfum"}:
                    score += 4
                if any(term in haystack for term in ["amber", "oud", "tobacco", "leather", "vanilla", "spicy"]):
                    score += 2

            recommendation = self._public_recommendation(candidate, variant)
            recommendation["score"] = score
            recommendation["fame_score"] = fame_score
            recommendation["fame_key"] = fame_key
            recommendation["reason"] = self._local_reason(candidate, variant, preferences)
            ranked.append(recommendation)

        ranked.sort(key=lambda item: (item["score"], item["fame_score"], -item["price_inr"]), reverse=True)
        return ranked

    def _diversify_popular_results(self, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
        first_pass = []
        deferred = []
        seen = set()
        for item in ranked:
            key = item.get("fame_key") or item["slug"]
            if key in seen:
                deferred.append(item)
                continue
            seen.add(key)
            first_pass.append(item)
        return [*first_pass, *deferred]

    def _iconic_score(self, candidate: dict[str, Any]) -> int:
        score, _ = self._iconic_match(candidate)
        return score

    def _iconic_match(self, candidate: dict[str, Any]) -> tuple[int, str]:
        brand = self._normalize(candidate["brand"])
        name = self._normalize(candidate["name"])
        score = 0
        fame_key = ""

        for brands, patterns, weight in ICONIC_SIGNATURES:
            if brand not in brands:
                continue
            if any(re.search(pattern, name) for pattern in patterns):
                if weight > score:
                    score = weight
                    fame_key = f"{brand}:{patterns[0]}"

        if score:
            if any(term in name for term in NON_CANONICAL_TERMS):
                score -= 18
            if any(term in name for term in FLANKER_TERMS):
                score -= 8
            if "for her" in name:
                score -= 18
            if brand in {"christian dior", "dior"} and "eau sauvage" in name:
                score -= 18
            if re.search(r"\b(intense|elixir|parfum|extrait)\b", name):
                score -= 2
            if re.search(r"\b(edp|edt|eau de parfum|eau de toilette)\b", name):
                score += 2
        return max(score, 0), fame_key

    def _normalize(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

    def _choose_variant(self, variants: list[dict[str, Any]], preferences: dict[str, Any]) -> dict[str, Any] | None:
        available = [variant for variant in variants if variant["stock_units"] > 0]
        if preferences["sale_type"]:
            preferred = [variant for variant in available if variant["sale_type"] == preferences["sale_type"]]
            if preferred:
                available = preferred

        budget = preferences["budget"]
        within_budget = [variant for variant in available if not budget or variant["price_inr"] <= budget]
        pool = within_budget or available
        if not pool:
            return None

        decant_10 = [
            variant
            for variant in pool
            if variant["sale_type"] == "decant" and int(variant.get("size_ml") or 0) == 10
        ]
        if decant_10:
            return min(decant_10, key=lambda variant: variant["price_inr"])
        return min(pool, key=lambda variant: variant["price_inr"])

    def _public_recommendation(self, candidate: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
        return {
            "slug": candidate["slug"],
            "brand": candidate["brand"],
            "name": candidate["name"],
            "collection_type": candidate["collection_type"],
            "gender": candidate["gender"],
            "family": candidate["family"],
            "concentration": candidate["concentration"],
            "description": candidate["description"],
            "image_url": candidate["photo_icon_url"] or candidate["image_url"],
            "product_path": candidate["product_path"],
            "variant_id": variant["id"],
            "variant_label": variant["size_label"],
            "sale_type": variant["sale_type"],
            "price_inr": variant["price_inr"],
            "notes": candidate["notes"][:6],
        }

    def _local_reason(
        self,
        candidate: dict[str, Any],
        variant: dict[str, Any],
        preferences: dict[str, Any],
    ) -> str:
        details = []
        if preferences["popularity_intent"] and self._iconic_score(candidate):
            details.append("a widely recognized signature")
        if preferences["gender"] and candidate["gender"] in {preferences["gender"], "unisex"}:
            details.append(candidate["gender"].replace("him", "masculine").replace("her", "feminine"))
        if preferences["note_terms"]:
            matched_notes = [
                term
                for term in preferences["note_terms"]
                if term in " ".join([candidate["family"], candidate["description"], " ".join(candidate["notes"])]).lower()
            ][:2]
            if matched_notes:
                details.append(" / ".join(matched_notes))
        if preferences["budget"] and variant["price_inr"] <= preferences["budget"]:
            details.append(f"within INR {preferences['budget']:,}")
        if not details:
            details.append(candidate["family"].lower() or "balanced")
        return f"{candidate['brand']} {candidate['name']} fits the {', '.join(details)} brief."

    def _local_response(self, preferences: dict[str, Any], recommendations: list[dict[str, Any]]) -> dict[str, Any]:
        if preferences["popularity_intent"]:
            context = "widely recognized signatures from the available catalog"
        elif preferences["occasions"]:
            context = f"for {preferences['occasions'][0]} wear"
        elif preferences["note_terms"]:
            context = f"around {', '.join(preferences['note_terms'][:2])}"
        elif preferences["budget"]:
            context = f"under INR {preferences['budget']:,}"
        else:
            context = "from the available edit"

        return {
            "mode": "local",
            "reply": f"I would start with these {context}.",
            "recommendations": recommendations,
        }

    def _ai_response(
        self,
        prompt: str,
        preferences: dict[str, Any],
        shortlist: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates = [
            {
                "slug": item["slug"],
                "brand": item["brand"],
                "name": item["name"],
                "family": item["family"],
                "concentration": item["concentration"],
                "price_inr": item["price_inr"],
                "variant_label": item["variant_label"],
                "sale_type": item["sale_type"],
                "notes": item["notes"],
                "description": item["description"][:220],
            }
            for item in shortlist
        ]
        payload = {
            "model": self.settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are The Scentist's fragrance concierge. Recommend only from the provided "
                        "candidate slugs. Do not mention unavailable products. Do not make medical, "
                        "allergy, authenticity, or delivery promises. Return valid JSON only with keys "
                        "reply and recommendations. recommendations must contain slug and reason."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "customer_request": prompt,
                            "parsed_preferences": preferences,
                            "candidate_products": candidates,
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
            "temperature": 0.35,
            "max_output_tokens": 900,
        }

        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))

        text = self._response_text(data)
        parsed = self._parse_json_text(text)
        by_slug = {item["slug"]: item for item in shortlist}
        recommendations = []
        for item in parsed.get("recommendations", []):
            slug = str(item.get("slug", "")).strip()
            if slug not in by_slug:
                continue
            recommendation = dict(by_slug[slug])
            reason = str(item.get("reason", "")).strip()
            if reason:
                recommendation["reason"] = reason[:280]
            recommendations.append(recommendation)
            if len(recommendations) == 4:
                break

        if not recommendations:
            return self._local_response(preferences, shortlist[:4])

        return {
            "mode": "ai",
            "reply": str(parsed.get("reply") or "Here is the edit I would start with.").strip()[:420],
            "recommendations": recommendations,
        }

    def _response_text(self, data: dict[str, Any]) -> str:
        if data.get("output_text"):
            return str(data["output_text"])

        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    chunks.append(str(content["text"]))
        return "\n".join(chunks)

    def _parse_json_text(self, text: str) -> dict[str, Any]:
        clean = text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean)
        if not clean.startswith("{"):
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                clean = match.group(0)
        parsed = json.loads(clean)
        return parsed if isinstance(parsed, dict) else {}
