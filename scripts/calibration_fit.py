"""Small train-only Bradley–Terry search around the production category priors."""
from itertools import product
import math
from statistics import fmean

from matcher.model import FEATURES
from scripts.calibration_data import group_priors


def bt_loss(pairs, scores, thetas=(1.0, 1.0, 1.0)):
    losses = {}
    for pair in pairs:
        z = (scores[pair.request_id][pair.a] - scores[pair.request_id][pair.b]) / 0.20
        loss = max(z, 0) + math.log1p(math.exp(-abs(z))) - pair.y * z
        losses.setdefault(pair.request_id, []).append((loss, pair.weight))
    if not losses:
        raise ValueError("Нет согласованных пар для обучения.")
    macro = fmean(sum(loss * weight for loss, weight in values) / sum(weight for _, weight in values)
                  for values in losses.values())
    return macro + 0.05 * sum(math.log(t) ** 2 for t in thetas)


def grid():
    return tuple(product((0.5, 0.75, 1.0, 1.25, 1.5), repeat=3))


def weights(group, thetas, duration=True, priors=None):
    prior = (priors or group_priors())[group]
    values = [p * t for p, t in zip(prior, (thetas[0], 1, 1, thetas[1] if duration else 0, thetas[2]))]
    if tuple(thetas) == (1, 1, 1):
        if duration:
            return tuple(prior)
        # Match weights_for's four-decimal normalization, including the residue.
        normalized = [round(value / sum(values), 4) for value in values]
        normalized[1] = round(normalized[1] + 1 - sum(normalized), 4)
        return tuple(normalized)
    return tuple(value / sum(values) for value in values)


def score_requests(requests, thetas=(1, 1, 1), old_weights=False, priors=None):
    priors = priors or group_priors()
    return {q["id"]: {cid: round(sum(v * w for v, w in zip(values,
            (0.35, 0.35, 0.10, 0.10, 0.10) if old_weights else weights(
                q["group"], thetas, q["request"]["duration_hours"] is not None, priors))), 4)
            for cid, values in q["features"].items()} for q in requests}


def fit(requests, pairs, priors=None):
    training_ids = {q["id"] for q in requests if q["split"] == "train"}
    training = [p for p in pairs if p.request_id in training_ids]
    results = [(theta, bt_loss(training, score_requests(requests, theta, priors=priors), theta)) for theta in grid()]
    best = min(results, key=lambda item: (item[1], sum(math.log(t) ** 2 for t in item[0]), item[0]))
    return best, [{"theta": theta, "train_loss": loss} for theta, loss in results]


def weight_table(theta, priors):
    return {g: dict(zip(FEATURES, weights(g, theta, priors=priors))) for g in priors}
