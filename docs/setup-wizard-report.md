# Setup Wizard Report

## Run

- Timestamp: `2026-06-06T16:04:56+00:00`
- Selected role: `model-runner`

## Values Written

- No environment values were written.

## Checks Passed

- Operating system: Windows-11-10.0.26200-SP0
- Ollama command: ollama command found
- Ollama reachable: installed models: qwen2.5:14b
- Configured model: qwen2.5:14b is installed

## Checks Failed

- None.

## Warnings

- Do not expose Ollama directly to the public internet.
- The wizard does not change Windows Firewall or router settings.

## Next Commands

- `ollama pull qwen2.5:14b`
- `python scripts/setup_wizard.py --diagnostics`
