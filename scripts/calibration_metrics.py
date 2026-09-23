"""Listwise vote aggregation, order-bias diagnostics and request-macro metrics."""
from dataclasses import dataclass
from itertools import combinations, permutations
import math
from statistics import fmean


@dataclass(frozen=True)
class Pair:
    request_id: str
    a: str
    b: str
    y: float
    weight: float = 1.0
    votes: int = 6


def grouped_rankings(rows):
    grouped = {}
    for row in rows:
        key = row["assignment"], row["direction"]
        votes = grouped.setdefault(row["request_id"], {})
        if key in votes:
            raise ValueError("Повторный голос в агрегации.")
        ranking = row["ranking"]
        if len(ranking) < 2 or len(set(ranking)) != len(ranking):
            raise ValueError("Требуется строгий рейтинг всех кандидатов.")
        votes[key] = ranking
    expected = {(i, direction) for i in range(3) for direction in ("forward", "reverse")}
    for votes in grouped.values():
        if set(votes) != expected or len({tuple(sorted(order)) for order in votes.values()}) != 1:
            raise ValueError("Для запроса нужны шесть полных рейтингов одного пула.")
    return grouped


def aggregate(rows):
    pairs, details = [], []
    for request_id, rankings in sorted(grouped_rankings(rows).items()):
        orders = list(rankings.values())
        for a, b in combinations(sorted(orders[0]), 2):
            votes = sum(order.index(a) < order.index(b) for order in orders)
            agreement = max(votes, 6 - votes)
            kind = "hard" if agreement >= 5 else "soft" if agreement == 4 else "excluded"
            y = float(votes > 3) if kind == "hard" else (0.83 if votes > 3 else 0.17) if kind == "soft" else None
            weight = 1.0 if kind == "hard" else 0.5 if kind == "soft" else 0.0
            details.append({"request_id": request_id, "a": a, "b": b, "votes_a": votes,
                            "vote_fraction": votes / 6, "kind": kind, "y": y, "weight": weight})
            if y is not None:
                pairs.append(Pair(request_id, a, b, y, weight, votes))
    return pairs, details


def concordance(left, right):
    positions = {cid: i for i, cid in enumerate(right)}
    return sum(positions[a] < positions[b] for a, b in combinations(left, 2))


def consistency(rows):
    per_request = []
    for request_id, votes in sorted(grouped_rankings(rows).items()):
        pairs = math.comb(len(next(iter(votes.values()))), 2)
        swap = [(votes[i, "forward"], votes[i, "reverse"]) for i in range(3)]
        shuffle = [(votes[i, direction], votes[j, direction])
                   for direction in ("forward", "reverse") for i, j in combinations(range(3), 2)]
        row = {"id": request_id}
        for name, comparisons in (("swap", swap), ("label_shuffle", shuffle)):
            matches = sum(concordance(a, b) for a, b in comparisons)
            row[name] = {"agreement": matches / (len(comparisons) * pairs), "matches": matches,
                         "comparisons": len(comparisons) * pairs,
                         "exact_rankings": sum(a == b for a, b in comparisons),
                         "ranking_comparisons": len(comparisons)}
        per_request.append(row)
    return {"per_request": per_request, **{name: {
        "macro_agreement": fmean(row[name]["agreement"] for row in per_request),
        "pooled_agreement": sum(row[name]["matches"] for row in per_request) /
                            sum(row[name]["comparisons"] for row in per_request),
        **{key: sum(row[name][key] for row in per_request)
           for key in ("matches", "comparisons", "exact_rankings", "ranking_comparisons")}}
        for name in ("swap", "label_shuffle")}}


def majority_orders(rows):
    """Exact Kemeny consensus for at most six candidates; resolve cycles explicitly."""
    result = {}
    for request_id, votes in sorted(grouped_rankings(rows).items()):
        rankings = list(votes.values())
        ids = sorted(rankings[0])
        counts = {(a, b): sum(order.index(a) < order.index(b) for order in rankings)
                  for a in ids for b in ids if a != b}
        rank_sums = {cid: sum(order.index(cid) for order in rankings) for cid in ids}
        objective = lambda order: sum(counts[a, b] for a, b in combinations(order, 2))
        orders = list(permutations(ids))
        best = min(orders, key=lambda order: (-objective(order), tuple(rank_sums[c] for c in order), order))
        optimal = objective(best)
        cycles = sum((counts[a, b] > 3 and counts[b, c] > 3 and counts[c, a] > 3)
                     or (counts[b, a] > 3 and counts[c, b] > 3 and counts[a, c] > 3)
                     for a, b, c in combinations(ids, 3))
        result[request_id] = {"order": list(best), "vote_agreements": optimal,
                              "optimal_orders": sum(objective(order) == optimal for order in orders),
                              "majority_cycles": cycles}
    return result


def weighted_mean(values):
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values) if values else None


def evaluate(requests, pairs, scores, consensus):
    rows = []
    for q in requests:
        order = sorted(scores[q["id"]], key=lambda cid: (-scores[q["id"]][cid], cid))
        labels = [pair for pair in pairs if pair.request_id == q["id"]]
        agreement = lambda p: float((order.index(p.a) < order.index(p.b)) == (p.y > 0.5))
        boundary = [p for p in labels if (p.a in order[:3]) != (p.b in order[:3])]
        judge_order = consensus[q["id"]]["order"]
        rows.append({"id": q["id"], "order": order, "top3": order[:3], "pairs": len(labels),
                     "boundary_pairs": len(boundary), "coverage": len(labels) / math.comb(len(order), 2),
                     "pairwise_agreement": fmean(map(agreement, labels)) if labels else None,
                     "weighted_pairwise_agreement": weighted_mean([(agreement(p), p.weight) for p in labels]),
                     "boundary_agreement": fmean(map(agreement, boundary)) if boundary else None,
                     "kendall_tau": 2 * concordance(order, judge_order) / math.comb(len(order), 2) - 1})
    return {"per_request": rows, **{key: fmean(values) if values else None
            for key in ("pairwise_agreement", "weighted_pairwise_agreement", "boundary_agreement", "kendall_tau")
            for values in [[row[key] for row in rows if row[key] is not None]]}}
