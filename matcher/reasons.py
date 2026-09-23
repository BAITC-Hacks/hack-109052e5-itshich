"""Deterministic, grounded reasons for an already ranked set of cards."""
from __future__ import annotations

from dataclasses import replace
from itertools import combinations, product
from math import fsum

from matcher import aspects, filtering, ranking
from matcher.model import (
    FEATURES, MAX_CARDS, CardFacts, Contractor, MatchResult, Reason, ReasonFamily, RejectReason, SemanticScorer,
)
from matcher.textfmt import format_date, money


_FEATURE = {ReasonFamily.BUDGET: "budget_fit", ReasonFamily.LANGUAGE: "language_fit",
            ReasonFamily.DURATION: "duration_fit", ReasonFamily.DESCRIPTION: "semantic"}


def _attribution(card, all_scored, shown, weights):
    baseline = {f: fsum(getattr(c.score, f) for c in all_scored) / len(all_scored) for f in FEATURES}
    phi = {f: round(weights[f] * (getattr(card.score, f) - baseline[f]), 4) for f in FEATURES}
    shown_ids = {c.contractor.id for c in shown}
    outside = sorted((c for c in all_scored if c.contractor.id not in shown_ids),
                     key=lambda c: (-c.score.total, c.contractor.id))[:1]
    # Keep the entrance-threshold comparison separate from the shown-card contrasts.
    delta = {c.contractor.id: {f: round(weights[f] * (getattr(card.score, f) - getattr(c.score, f)), 4)
                              for f in FEATURES}
             for c in sorted((*shown, *outside), key=lambda c: c.contractor.id)
             if c.contractor.id != card.contractor.id}
    distinct = {f: max(0.0, min((delta[c.contractor.id][f] for c in shown
                                if c.contractor.id != card.contractor.id), default=0.0))
                for f in FEATURES}
    return phi, {f: value / (max(distinct.values()) or 1) for f, value in distinct.items()}


def _availability(result, all_scored, contractors, scorer):
    replacements = {}
    busy_count = sum(RejectReason.BUSY_ON_DATE in r.reasons for r in result.rejections)
    if result.pool_size > 1 and result.eligible_count == 1 and busy_count:
        replacements[result.cards[0].contractor.id] = Reason(
            "AVAILABILITY_ONLY_FREE", ReasonFamily.AVAILABILITY,
            {"date": format_date(result.request.event_date)})
    if not any(r.reasons == (RejectReason.BUSY_ON_DATE,) for r in result.rejections):
        return replacements
    _, ignoring_date, _ = filtering.filter_pool(contractors, result.request, ignore_date=True)
    top = ranking.score_all(ignoring_date, result.request, scorer)[:MAX_CARDS]
    top_ids = {c.contractor.id for c in top}
    eligible = [c.contractor for c in all_scored]
    for competitor_card in sorted(top, key=lambda c: (-c.score.total, c.contractor.id)):
        competitor = competitor_card.contractor
        if result.request.event_date not in competitor.busy_dates:
            continue
        restored = ranking.score_all(sorted([*eligible, competitor], key=lambda c: c.id), result.request, scorer)
        restored_ids = {c.contractor.id for c in restored[:MAX_CARDS]}
        for card in sorted(result.cards, key=lambda c: c.contractor.id):
            cid = card.contractor.id
            if cid not in top_ids and cid not in restored_ids and cid not in replacements:
                replacements[cid] = Reason("AVAILABILITY_REPLACEMENT", ReasonFamily.AVAILABILITY,
                                          {"competitor": competitor.name, "date": format_date(result.request.event_date)})
    return replacements


def _candidates(card, shown, phi, availability, tagged, scarcity):
    contractor = card.contractor
    peers = [c for c in shown if c.contractor.id != contractor.id]
    reasons = [availability[contractor.id]] if contractor.id in availability else []
    if scarcity:
        if reasons:
            reasons[0] = replace(reasons[0], evidence={**reasons[0].evidence, "scarcity": scarcity})
        else:
            reasons.append(Reason("AVAILABILITY_SCARCE", ReasonFamily.AVAILABILITY, {"scarcity": scarcity}))

    def add(code, family, **evidence):
        reasons.append(Reason(code, family, evidence, phi.get(_FEATURE.get(family), 0.0)))

    price, budget = money(contractor.price_from_kzt), money(card.budget_kzt)
    if phi["budget_fit"] > 0 or card.budget_headroom_pct >= 30:
        add("BUDGET_HEADROOM", ReasonFamily.BUDGET, price=price, budget=budget,
            headroom_pct=str(card.budget_headroom_pct))
    if peers and contractor.price_from_kzt < min(c.contractor.price_from_kzt for c in peers):
        next_price = min(c.contractor.price_from_kzt for c in peers)
        add("BUDGET_LOWER_THAN_SHOWN", ReasonFamily.BUDGET, price=price, next_price=money(next_price),
            diff_pct=str(round(100 * (next_price - contractor.price_from_kzt) / next_price)))
    if card.requested_language is not None:
        add("LANGUAGE_REQUEST_MATCH", ReasonFamily.LANGUAGE, language=card.requested_language)
    unique_languages = sorted(language for language in contractor.languages
                              if language != card.requested_language and peers
                              and all(language not in c.contractor.languages for c in peers))
    if unique_languages:
        add("LANGUAGE_UNIQUE_IN_SHOWN", ReasonFamily.LANGUAGE, language=unique_languages[0])
    if len(contractor.languages) == 3 and card.requested_language is None:
        add("LANGUAGE_OPTIONS", ReasonFamily.LANGUAGE, languages=", ".join(sorted(contractor.languages)))
    if contractor.max_hours is None:
        add("DURATION_NOT_APPLICABLE", ReasonFamily.DURATION)
    else:
        if card.requested_hours is not None and phi["duration_fit"] > 0:
            add("DURATION_HEADROOM", ReasonFamily.DURATION,
                requested_hours=str(card.requested_hours), max_hours=str(contractor.max_hours))
        if peers and all(c.contractor.max_hours is None or contractor.max_hours > c.contractor.max_hours
                         for c in peers):
            add("DURATION_MAX_IN_SHOWN", ReasonFamily.DURATION, max_hours=str(contractor.max_hours))
    relevant = sorted((a for a in tagged.get(contractor.id, ())
                       if a.polarity == "positive" and a.relevant_to(card.format_matched)), key=lambda a: a.tag)
    evidence = {"aspect": relevant[0].label, "tag": relevant[0].tag} if relevant else {}
    if relevant:
        add("DESCRIPTION_ASPECT", ReasonFamily.DESCRIPTION, **evidence)
    if peers and card.semantic_score > max(c.semantic_score for c in peers):
        add("DESCRIPTION_CLOSEST_IN_SHOWN", ReasonFamily.DESCRIPTION, **evidence)
    if not any(_can_headline(r) for r in reasons) or (
            contractor.price_from_kzt * 100 > card.budget_kzt * 85
            and not any(r.family == ReasonFamily.BUDGET for r in reasons)):
        add("BUDGET_FITS", ReasonFamily.BUDGET, price=price, budget=budget)
    return reasons


def _can_headline(reason):
    return reason.code not in {"DURATION_NOT_APPLICABLE", "AVAILABILITY_SCARCE"} and not (
        reason.code == "DESCRIPTION_CLOSEST_IN_SHOWN" and not reason.evidence)


def _strong(reason):
    return reason.code.endswith("_IN_SHOWN") or reason.code in {
        "BUDGET_LOWER_THAN_SHOWN", "LANGUAGE_REQUEST_MATCH", "AVAILABILITY_REPLACEMENT",
    }


def _priority(reason, card):
    if reason.family == ReasonFamily.BUDGET and card.contractor.price_from_kzt * 100 > card.budget_kzt * 85:
        return 0.3
    if reason.family == ReasonFamily.AVAILABILITY and "scarcity" in reason.evidence:
        return 0.3
    if reason.code == "DURATION_HEADROOM" and card.requested_hours is not None:
        return 0.2
    if reason.code == "LANGUAGE_REQUEST_MATCH" and card.requested_language is not None:
        return 0.2
    return 0.0


def _plan_key(plan, cards):
    pairs = tuple(combinations(plan, 2))
    utility = (fsum(u for _, u in plan) - 0.5 * sum(a.code == b.code for (a, _), (b, _) in pairs)
               - 0.1 * sum(a.family == b.family for (a, _), (b, _) in pairs))
    return -round(utility, 8), tuple((r.code, c.contractor.id) for (r, _), c in zip(plan, cards))


def assign(result: MatchResult, all_scored: tuple[CardFacts, ...],
           contractors: list[Contractor], scorer: SemanticScorer) -> MatchResult:
    if not result.cards:
        return replace(result, diversity_limited=False)
    all_scored = tuple(sorted(all_scored, key=lambda c: c.contractor.id))
    weights = ranking.weights_for(result.request)
    contexts = [_attribution(c, all_scored, result.cards, weights) for c in result.cards]
    availability = _availability(result, all_scored, contractors, scorer)
    tagged = aspects.load_aspects()
    busy_count = sum(RejectReason.BUSY_ON_DATE in r.reasons for r in result.rejections)
    scarce = result.request.event_date.month == 12 or busy_count * 2 >= result.pool_size
    scarcity = f"в эту дату свободны {result.pool_size - busy_count} из {result.pool_size}" if scarce else None
    candidates = [_candidates(c, result.cards, phi, availability, tagged, scarcity)
                  for c, (phi, _) in zip(result.cards, contexts)]
    options, rated = [], []
    for index, (reasons, (phi, distinct)) in enumerate(zip(candidates, contexts)):
        maximum = max(0.0, *phi.values())
        choices = []
        for reason in reasons:
            a = 1.0 if _strong(reason) else max(0.0, reason.contribution) / (maximum or 1)
            unique = len(result.cards) > 1 and not any(
                reason.code == other.code and reason.evidence == other.evidence
                for j, group in enumerate(candidates) if j != index for other in group)
            utility = (0.65 * a + 0.25 * distinct.get(_FEATURE.get(reason.family), 0) + 0.10 * unique
                       + _priority(reason, result.cards[index]))
            choices.append((reason, utility))
        choices.sort(key=lambda item: (-item[1], item[0].code, result.cards[index].contractor.id))
        substantive = [item for item in choices if _can_headline(item[0])]
        primary = [item for item in substantive if _strong(item[0]) or _priority(item[0], result.cards[index]) or
                   item[0].contribution >= 0.2 * maximum]
        options.append((primary or substantive)[:6])
        rated.append(choices)
    plan = min(product(*options), key=lambda p: _plan_key(p, result.cards))
    cards = []
    for card, (primary, _), choices in zip(result.cards, plan, rated):
        reasons, families = [replace(primary, primary=True)], {primary.family}
        for reason, _ in sorted(choices, key=lambda item: "scarcity" not in item[0].evidence):
            if reason.family not in families:
                reasons.append(reason)
                families.add(reason.family)
            if len(reasons) == 3:
                break
        if len(reasons) == 1:
            reasons.append(Reason("FORMAT_SUPPORTED", ReasonFamily.FORMAT, {"format": card.format_matched}))
        caveats = tuple(Reason(flag.upper(), ReasonFamily.DATA_QUALITY, {})
                        for flag in sorted(card.caveats))
        cards.append(replace(card, reasons=tuple(reasons) + caveats))
    return replace(result, cards=tuple(cards),
                   diversity_limited=len({c.reasons[0].code for c in cards}) < len(cards))
