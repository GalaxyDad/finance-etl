# Personal Finance ETL

This project is a local Python ETL (Extract, Transform, Load) pipeline designed to process raw bank statements, categorize transactions using the Gemini LLM, and output a consolidated ledger formatted specifically for the "Jenn Mike Finance Tracker" Google Sheet.

## What It Does
1. **Extracts**: Reads raw bank transaction CSVs (Rogers CC, Simplii, and CIBC) from `data/raw/`.
2. **Transforms**: 
   - Normalizes dates to the strict `YYYY-MM-DD` format.
   - Standardizes amounts (Income as positive floats, Expenses as negative floats).
   - Extracts unique merchants and queries the Gemini 3.6 Flash API to assign categories. This API call is contextualized heavily using a predefined allowed categories list (`Categories.csv`) and historical examples (`Mike Transaction Register.csv`).
3. **Optimizes**: 
   - Uses defensive CSV parsing to reliably ingest bank statements regardless of type-inference challenges or unexpected formats.
   - Normalizes merchant names (e.g. stripping Amazon order hashes) to cut down redundant LLM categorization calls by up to 70%.
   - Chunks Gemini API requests into batches of 50 with incremental cache saves to ensure fault tolerance.
4. **Validates**: Automatically performs strict validation checks comparing the raw extracted data against the formatted output. It verifies that row counts and total balances match to ensure no transactions are dropped during formatting. Mismatches or dropped transactions are clearly flagged and displayed to the user.
5. **Loads**: Joins the LLM categorization back to the main DataFrame, sorts the data chronologically by Date (and alphabetically by Account), and formats it into a strict 12-column output.
6. **Logs**: Standard logs and error messages are written both to the console and to `pipeline.log` for easy troubleshooting.

## How to Use It

### 1. Setup the Environment
Create a virtual environment and install dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Credentials
Copy `.env.example` to `.env` and insert your Gemini API Key:
```text
GEMINI_API_KEY=your_actual_key_here
```

### 3. Provide Data
- Drop your raw bank statement CSVs into `data/raw/`. Supported banks are: Rogers, Simplii, CIBC, and Wealthsimple (`ws_activities*.csv` and `ws_credit-card*.csv`).
- Ensure your reference files (`Jenn Mike Finance Tracker - Categories.csv` and `Jenn Mike Finance Tracker - Mike Transaction Register.csv`) are located in `data/reference/`.

### 4. Verification & Execution
To verify the logic safely without making API calls, run the test suite:
```bash
pytest tests/ -v
```

To execute the pipeline safely without making API calls or exporting data (useful to review cache hit rates or merchant extraction):
```bash
PYTHONPATH=. python src/main.py --dry-run
```

To run the pipeline and generate a uniquely timestamped output file in `data/processed/`:
```bash
PYTHONPATH=. python src/main.py
```

If you need to force a fresh recategorization by skipping the local cache, use:
```bash
PYTHONPATH=. python src/main.py --no-cache
```

## Testing
This project strictly follows Test-Driven Development (TDD). Tests are located in `tests/test_etl.py` and mock the API calls and raw data ingestion.
