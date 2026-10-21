# EM Fintech Radar

A weekly market-scanning agent for FX, payments and stablecoins in Latin America, Africa, the Gulf and Southeast Asia, built with Claude Code.

It reads regulator, central bank and deal feeds, drops what it has already seen, has Claude score each item for an EM fintech advisory firm, and writes a one-page brief: the one thing that matters, the "so what" for a fund, a payments company or a licence decision, possible mandates, and two questions to ask an operator.

## Run it

Needs Python 3 and Claude Code signed in with a Claude account.

```bash
pip3 install pyyaml
python3 radar.py            # writes out/radar-<week>.md and .html
python3 radar.py --no-llm   # scan only, prints the raw list
```

Claude is called through Claude Code in headless mode (`claude -p`), so the run uses the Claude plan. No API key is needed or used.

## Example

`examples/radar-2026-W43.md` is an unedited brief from the run of 21 October 2026.

## Change what it watches

- `sources.yaml`: one entry per feed or search. Adding a regulator is three lines.
- `prompts/triage.md`: what counts as relevant (scores 0 to 3).
- `prompts/brief.md`: the format of the brief.

Tests: `python3 tests/test_radar.py` (offline).

## Limits

- Google News queries catch most regulator releases but can miss a circular that gets no coverage. Direct feeds are better where a regulator publishes one.
- The brief is a first draft for a human to check. Every fact links to its source so checking takes minutes.
