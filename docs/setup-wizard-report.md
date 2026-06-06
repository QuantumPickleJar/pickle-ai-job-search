# Setup Wizard Report

This file is replaced by `scripts/setup_wizard.py` after a non-diagnostics setup run.

No machine role has been configured by the committed repository state. Run diagnostics without changing files:

```bash
python scripts/setup_wizard.py --diagnostics
```

Then select the appropriate setup role:

```bash
python scripts/setup_wizard.py --role model-runner
python scripts/setup_wizard.py --role service-host
python scripts/setup_wizard.py --role all-in-one
```

Generated reports mask sensitive values such as `APP_API_KEY`.
