1. Install test dependencies: `python -m pip install -r tests/requirements.txt`
2. Install the browser: `python -m playwright install chromium`
3. Start the Compose web service (`docker compose up -d web`) at `http://127.0.0.1:3001`.
4. For PostgreSQL integration tests, create a disposable database whose name ends in `_test`, apply migrations (`docker compose run --rm -e DATABASE_URL=postgresql://dispute:local_dev_only@postgres:5432/disputeshield_test api python scripts/migrate.py`), and expose it as `TEST_DATABASE_URL=postgresql://dispute:local_dev_only@127.0.0.1:5432/disputeshield_test`.
5. In PowerShell run:

```powershell
$env:TEST_DATABASE_URL = 'postgresql://dispute:local_dev_only@127.0.0.1:5432/disputeshield_test'
python -m pytest -q tests
Remove-Item Env:TEST_DATABASE_URL
```

Without `TEST_DATABASE_URL`, PostgreSQL-backed case tests are skipped. They refuse to write unless the selected database name ends in `_test`.
