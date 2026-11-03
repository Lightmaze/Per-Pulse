# Research tools

Optional tooling for posture studies. It is not packaged with Per Pulse and the
product does not import it.

- `per_pulse_research/posture_evaluation.py`, `posture_transfer.py`,
  `posture_study.py`, `blinded_review.py`: study runners and review helpers.
- `posture-evaluation.md`: how to run them.
- `tests/`: their tests.

Run from the repository root with the development install active:

```sh
PYTHONPATH=research python -m per_pulse_research.posture_study --help
python -m pytest research/tests
```

The default `python -m pytest` run covers the product only.
