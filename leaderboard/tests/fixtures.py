"""Fixtures: the sample `mmeb score` output (2B retriever, k=10, GPT-5.6 Sol predictions, pre-judged)."""

SAMPLE_METRICS = \
{
    "questions": 1613,
    "n": 1613,
    "submitted": 1613,
    "missing": 0,
    "correct": 849,
    "accuracy": 52.63,
    "observed_accuracy": 52.63,
    "statuses": {
        "graded": 1613,
        "missing": 0,
        "invalid": 0,
        "unsupported": 0,
        "error": 0
    },
    "unknown_prediction_ids": [],
    "complete": True,
    "categories": {
        "Evidence Reasoning": {
            "n": 1102,
            "submitted": 1102,
            "missing": 0,
            "correct": 646,
            "accuracy": 58.62,
            "observed_accuracy": 58.62
        },
        "Conflict Resolution": {
            "n": 424,
            "submitted": 424,
            "missing": 0,
            "correct": 146,
            "accuracy": 34.43,
            "observed_accuracy": 34.43
        },
        "Unanswerability": {
            "n": 87,
            "submitted": 87,
            "missing": 0,
            "correct": 57,
            "accuracy": 65.52,
            "observed_accuracy": 65.52
        }
    },
    "tasks": {
        "Extraction": {
            "n": 268,
            "submitted": 268,
            "missing": 0,
            "correct": 181,
            "accuracy": 67.54,
            "observed_accuracy": 67.54
        },
        "Attribution": {
            "n": 80,
            "submitted": 80,
            "missing": 0,
            "correct": 49,
            "accuracy": 61.25,
            "observed_accuracy": 61.25
        },
        "Computation": {
            "n": 310,
            "submitted": 310,
            "missing": 0,
            "correct": 180,
            "accuracy": 58.06,
            "observed_accuracy": 58.06
        },
        "Comparison": {
            "n": 212,
            "submitted": 212,
            "missing": 0,
            "correct": 100,
            "accuracy": 47.17,
            "observed_accuracy": 47.17
        },
        "Interpretation": {
            "n": 232,
            "submitted": 232,
            "missing": 0,
            "correct": 136,
            "accuracy": 58.62,
            "observed_accuracy": 58.62
        },
        "Cross-source": {
            "n": 212,
            "submitted": 212,
            "missing": 0,
            "correct": 77,
            "accuracy": 36.32,
            "observed_accuracy": 36.32
        },
        "Temporal": {
            "n": 212,
            "submitted": 212,
            "missing": 0,
            "correct": 69,
            "accuracy": 32.55,
            "observed_accuracy": 32.55
        },
        "Unanswerability": {
            "n": 87,
            "submitted": 87,
            "missing": 0,
            "correct": 57,
            "accuracy": 65.52,
            "observed_accuracy": 65.52
        }
    },
    "answerability": {
        "answerable": {
            "n": 1526,
            "submitted": 1526,
            "missing": 0,
            "correct": 792,
            "accuracy": 51.9,
            "observed_accuracy": 51.9
        },
        "unanswerable": {
            "n": 87,
            "submitted": 87,
            "missing": 0,
            "correct": 57,
            "accuracy": 65.52,
            "observed_accuracy": 65.52
        }
    },
    "evidence_modality": {
        "text": {
            "n": 693,
            "submitted": 693,
            "missing": 0,
            "correct": 460,
            "accuracy": 66.38,
            "observed_accuracy": 66.38
        },
        "image": {
            "n": 563,
            "submitted": 563,
            "missing": 0,
            "correct": 263,
            "accuracy": 46.71,
            "observed_accuracy": 46.71
        },
        "multi": {
            "n": 270,
            "submitted": 270,
            "missing": 0,
            "correct": 69,
            "accuracy": 25.56,
            "observed_accuracy": 25.56
        }
    },
    "evidence_count": {
        "1": {
            "n": 749,
            "submitted": 749,
            "missing": 0,
            "correct": 495,
            "accuracy": 66.09,
            "observed_accuracy": 66.09
        },
        "2": {
            "n": 495,
            "submitted": 495,
            "missing": 0,
            "correct": 222,
            "accuracy": 44.85,
            "observed_accuracy": 44.85
        },
        "3+": {
            "n": 282,
            "submitted": 282,
            "missing": 0,
            "correct": 75,
            "accuracy": 26.6,
            "observed_accuracy": 26.6
        }
    },
    "retrieval": None,
    "reference_retrieval": {
        "retriever": "2b",
        "k": 10,
        "answerable_questions": 1526,
        "evidence_recall": 41.31,
        "complete_evidence_questions": 471,
        "micro_evidence_recall": 35.63
    },
    "judge": {
        "pre_judged": True
    },
    "label": "2b_k10.jsonl"
}
