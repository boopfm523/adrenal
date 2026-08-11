# Generated-analysis safety evaluation

HealthCurve gates changes to the generated-analysis prompt, schema, and selected local
model with the all-synthetic cases in `evals/analysis/gold-v1.json`. The checked-in
baseline must cite only supplied record IDs, disclose missingness, refuse medication
guidance, and contain no number absent from deterministic computed input.

Run `uv run python scripts/evaluate_analysis.py`. CI runs the same command. The
repository may use an explicitly synthetic validator fixture while no configured model
is installed; it is not evidence of model quality. Once the selected model is present,
run `uv run python scripts/evaluate_analysis.py --record`. Recording resolves the
model's immutable digest, evaluates every case, and refuses to overwrite the baseline
unless all safety cases pass. Review every synthetic response before committing it.
Lowering the exact threshold is not an acceptable way to bless a regression.
