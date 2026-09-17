#!/usr/bin/env python3
"""
Training script for SentenceCompletenessDetector logistic regression model.

This script:
1. Loads labeled training data from complete.txt and incomplete.txt
2. Extracts features using SentenceCompletenessDetector._extract_features()
3. Trains a logistic regression model
4. Exports weights to _sentence_model_weights.json
5. Reports accuracy metrics
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # AIServer/src/

from common.Paths import AISERVER_DIR
from interfaces.voice.microphones.speech_analysis.SentenceCompletenessDetector import (
    SentenceCompletenessDetector,
    MODEL_THRESHOLD,
)


def load_training_data(data_dir: Path) -> Tuple[List[str], List[int]]:
    """Load training examples and labels from complete.txt and incomplete.txt."""
    complete_file = data_dir / "complete.txt"
    incomplete_file = data_dir / "incomplete.txt"

    sentences = []
    labels = []

    # Load complete sentences (label=1)
    if complete_file.exists():
        with open(complete_file, "r", encoding="utf-8") as f:
            for line in f:
                sentence = line.strip()
                if sentence:
                    sentences.append(sentence)
                    labels.append(1)

    # Load incomplete sentences (label=0)
    if incomplete_file.exists():
        with open(incomplete_file, "r", encoding="utf-8") as f:
            for line in f:
                sentence = line.strip()
                if sentence:
                    sentences.append(sentence)
                    labels.append(0)

    print(f"Loaded {len(sentences)} training examples:")
    print(f"  - Complete: {sum(labels)}")
    print(f"  - Incomplete: {len(labels) - sum(labels)}")

    return sentences, labels


def extract_all_features(sentences: List[str]) -> List[Dict[str, float]]:
    """Extract features for all sentences using SentenceCompletenessDetector."""
    features = []
    for i, sentence in enumerate(sentences):
        if (i + 1) % 100 == 0:
            print(f"  Extracted features for {i + 1}/{len(sentences)} sentences...")
        feature_dict = SentenceCompletenessDetector._extract_features(sentence)
        features.append(feature_dict)
    return features


def remove_bias_feature(features: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Drop the bias feature before vectorisation so the model intercept provides the bias."""
    return [{k: v for k, v in feat.items() if k != "bias"} for feat in features]


def format_class_weight(class_weight) -> str:
    if class_weight is None:
        return "None"
    if class_weight == "balanced":
        return "balanced"
    return ",".join(f"{cls}:{weight}" for cls, weight in sorted(class_weight.items()))


def compute_metrics(y_true: np.ndarray, probs: np.ndarray, threshold: float) -> Dict[str, float]:
    """Compute accuracy/FP/FN metrics at the configured threshold."""
    preds = (probs >= threshold).astype(int)
    accuracy = float((preds == y_true).mean()) if y_true.size else 0.0

    negatives = y_true == 0
    positives = y_true == 1
    fp = int(np.logical_and(preds == 1, negatives).sum())
    fn = int(np.logical_and(preds == 0, positives).sum())
    neg_total = int(negatives.sum()) or 1
    pos_total = int(positives.sum()) or 1

    return {
        "accuracy": accuracy,
        "fp": fp,
        "fn": fn,
        "fp_rate": fp / neg_total,
        "fn_rate": fn / pos_total,
        "preds": preds,
    }


def find_best_hyperparameters(X_train, y_train, X_val, y_val):
    """Find the hyperparameters that maximise accuracy while limiting false positives."""
    C_VALUES = [0.5, 1.0, 2.0, 3.0, 5.0]
    CLASS_WEIGHTS = [
        None,
        "balanced",
        {1: 1.0, 0: 2.0},
        {1: 1.0, 0: 3.0},
        {1: 1.0, 0: 4.0},
        {1: 1.0, 0: 5.0},
    ]

    best_key = None
    best_params = None
    best_metrics: Dict[str, float] | None = None

    print("\n5. Finding best hyperparameters (threshold-aware scoring)...")
    for C in C_VALUES:
        for class_weight in CLASS_WEIGHTS:
            model = LogisticRegression(
                C=C,
                penalty="l2",
                solver="lbfgs",
                max_iter=5000,
                random_state=42,
                class_weight=class_weight,
            )
            model.fit(X_train, y_train)

            probs = model.predict_proba(X_val)[:, 1]
            metrics = compute_metrics(y_val, probs, MODEL_THRESHOLD)

            weight_str = format_class_weight(class_weight)
            print(
                f"  C={C:4.1f}, weight={weight_str:>12s} -> "
                f"acc={metrics['accuracy'] * 100:5.2f}% | "
                f"FP={metrics['fp']:3d} ({metrics['fp_rate'] * 100:4.1f}%) | "
                f"FN={metrics['fn']:3d} ({metrics['fn_rate'] * 100:4.1f}%)"
            )

            key = (
                round(metrics["accuracy"], 6),
                -round(metrics["fp_rate"], 6),
                -round(metrics["fn_rate"], 6),
            )

            if best_key is None or key > best_key:
                best_key = key
                best_params = (C, "l2", "lbfgs", class_weight)
                best_metrics = metrics

    if not best_params or not best_metrics:
        raise RuntimeError("Failed to select hyperparameters")

    best_c, best_penalty, best_solver, best_weight = best_params
    weight_str = format_class_weight(best_weight)
    print(
        f"\nBest config -> C={best_c}, weight={weight_str}, "
        f"accuracy={best_metrics['accuracy'] * 100:5.2f}%, "
        f"FP rate={best_metrics['fp_rate'] * 100:4.1f}%, "
        f"FN rate={best_metrics['fn_rate'] * 100:4.1f}%"
    )

    return best_c, best_weight, best_penalty, best_solver


def train_final_model(X, y, C, class_weight, penalty, solver):
    """Train final model on full dataset."""
    weight_str = format_class_weight(class_weight)
    print(f"  Training with C={C}, penalty={penalty}, solver={solver}, class_weight={weight_str}")
    print(f"  on all {len(X)} examples...")

    model = LogisticRegression(
        C=C,
        penalty=penalty,
        solver=solver,
        max_iter=5000,
        random_state=42,
        class_weight=class_weight,
    )
    model.fit(X, y)

    probs = model.predict_proba(X)[:, 1]
    metrics = compute_metrics(y, probs, MODEL_THRESHOLD)
    print(
        f"  Training accuracy at threshold {MODEL_THRESHOLD:.2f}: {metrics['accuracy']:.4f} "
        f"(FP={metrics['fp']}, FN={metrics['fn']})"
    )

    return model


def export_weights(model: LogisticRegression, vectorizer: DictVectorizer, output_path: Path):
    """Export model weights to JSON file."""
    weights = {}

    # Get feature names and coefficients
    feature_names = vectorizer.get_feature_names_out()
    coefficients = model.coef_[0]

    # Map feature names to weights
    for name, coef in zip(feature_names, coefficients):
        weights[name] = float(coef)

    # Add bias term
    weights["bias"] = float(model.intercept_[0])

    # Sort by feature name for readability
    weights = dict(sorted(weights.items()))

    # Write to file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2)

    print(f"\nExported {len(weights)} weights to {output_path}")
    print(f"  - Features: {len(weights) - 1}")
    print(f"  - Bias: {weights['bias']:.4f}")


def main():
    """Main training pipeline."""
    print("=" * 70)
    print("SentenceCompletenessDetector Model Training")
    print("=" * 70)

    # Paths
    script_dir = Path(__file__).parent
    data_dir = AISERVER_DIR / "tests/sentence_completeness/data/sentence_completeness"
    output_path = script_dir / "_sentence_model_weights.json"

    if not data_dir.is_dir():
        print(f"The training corpus ({data_dir}) is not part of this checkout — nothing to train on.")
        print("The shipped _sentence_model_weights.json stays as it is.")
        return

    # Load data
    print("\n1. Loading training data...")
    sentences, labels = load_training_data(data_dir)

    if len(sentences) == 0:
        print("ERROR: No training data found!")
        return

    # Extract features
    print("\n2. Extracting features...")
    features = extract_all_features(sentences)

    # Vectorize features (convert dict to feature matrix)
    print("\n3. Vectorizing features...")
    vector_features = remove_bias_feature(features)
    vectorizer = DictVectorizer(sparse=False)
    X = vectorizer.fit_transform(vector_features)
    y = np.asarray(labels, dtype=int)

    print(f"  Feature matrix shape: {X.shape}")
    print(f"  Number of unique features: {len(vectorizer.get_feature_names_out())}")

    # Split into train/test for hyperparameter tuning
    print("\n4. Splitting train/validation sets for hyperparameter tuning...")
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"  Training set: {len(X_train)} examples")
    print(f"  Validation set: {len(X_val)} examples")

    # Find best hyperparameters
    best_c, best_weight, best_penalty, best_solver = find_best_hyperparameters(
        X_train, y_train, X_val, y_val
    )

    # Train final model on FULL dataset with best hyperparameters
    print("\n6. Training final model on full dataset...")
    model = train_final_model(X, y, best_c, best_weight, best_penalty, best_solver)

    # Evaluate final model metrics on the complete dataset
    print("\n7. Evaluating model on full dataset...")
    probs = model.predict_proba(X)[:, 1]
    metrics = compute_metrics(y, probs, MODEL_THRESHOLD)
    preds = metrics.pop("preds", None)
    print(
        f"  Overall accuracy: {metrics['accuracy'] * 100:5.2f}% "
        f"(FP={metrics['fp']}, FN={metrics['fn']})"
    )

    if preds is not None:
        complete_mask = y == 1
        incomplete_mask = y == 0
        complete_correct = int(np.logical_and(preds == 1, complete_mask).sum())
        incomplete_correct = int(np.logical_and(preds == 0, incomplete_mask).sum())
        complete_total = int(complete_mask.sum()) or 1
        incomplete_total = int(incomplete_mask.sum()) or 1
        print(
            f"  Complete sentences:   {complete_correct}/{complete_total} "
            f"({complete_correct / complete_total * 100:5.2f}%)"
        )
        print(
            f"  Incomplete sentences: {incomplete_correct}/{incomplete_total} "
            f"({incomplete_correct / incomplete_total * 100:5.2f}%)"
        )

    # Export weights
    print("\n8. Exporting weights...")
    export_weights(model, vectorizer, output_path)

    print("\n" + "=" * 70)
    print("Training complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
