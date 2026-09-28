# Generate the complete deterministic synthetic data pack (5k customers, 50k transactions, 100k events):
python scripts/generate_data.py

# Load the generated fixture into the local DisputeShield database and create
# deterministic sample cases. This is additive and idempotent; it does not erase
# existing merchants or records. Synthetic workspaces use separate IDs/names.
$env:DATABASE_URL = 'postgresql://dispute:local_dev_only@127.0.0.1:5432/disputeshield'
python scripts/load_synthetic_data.py --confirm-load
Remove-Item Env:DATABASE_URL

# Open http://localhost:3001 and select "Synthetic Merchant 1" through
# "Synthetic Merchant 10" in the merchant selector.

# Smaller quick dataset:
python scripts/generate_data.py --customers 100 --transactions 1000 --events 2500 --output data/generated/small

# Run API:
cd apps/api
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
