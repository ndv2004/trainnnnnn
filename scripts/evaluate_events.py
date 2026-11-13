"""One-to-one temporal IoU evaluation. Never invent labels or accuracy from mocks."""

import argparse
import json
from pathlib import Path


def tiou(a, b):
    intersection = max(0, min(a["end_ms"], b["end_ms"]) - max(a["start_ms"], b["start_ms"]))
    union = max(a["end_ms"], b["end_ms"]) - min(a["start_ms"], b["start_ms"])
    return intersection / union if union > 0 else 0


def evaluate(predictions, labels, threshold=0.3, normal_hours=None, unknown_hours=None):
    candidates = []
    for i, p in enumerate(predictions):
        for j, t in enumerate(labels):
            if p["event_type"] == t["event_type"] and p["clip_id"] == t["clip_id"]:
                score = tiou(p, t)
                if score >= threshold:
                    candidates.append((score, i, j))
    # Deterministic greedy maximum-IoU order; duplicates for one label become FP.
    matched_p, matched_t = set(), set()
    pairs = []
    for score, i, j in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
        if i not in matched_p and j not in matched_t:
            matched_p.add(i)
            matched_t.add(j)
            pairs.append({"prediction": i, "label": j, "temporal_iou": score})
    tp = len(pairs)
    fp = len(predictions) - tp
    fn = len(labels) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    # False alerts/hour requires predictions from labeled NORMAL clips, not all FP.
    normal_fp = sum(i not in matched_p and p.get("is_normal_clip") is True for i, p in enumerate(predictions))
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "normal_fp": normal_fp,
        "false_alerts_per_normal_hour": normal_fp / normal_hours
        if normal_hours and normal_hours > 0
        else None,
        "normal_observed_hours": normal_hours,
        "unknown_hours": unknown_hours,
        "pairs": pairs,
        "matching": "greedy descending temporal IoU, same clip/type, one-to-one",
        "tiou_threshold": threshold,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--predictions", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--normal-hours", type=float)
    p.add_argument("--unknown-hours", type=float)
    args = p.parse_args()
    result = evaluate(
        json.loads(args.predictions.read_text()),
        json.loads(args.labels.read_text()),
        normal_hours=args.normal_hours,
        unknown_hours=args.unknown_hours,
    )
    with open(args.output, "x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, indent=2))
