# Personal Finance ETL

This project is a local Python ETL (Extract, Transform, Load) pipeline designed to process raw bank statements, categorize transactions using the Gemini LLM, and output a consolidated ledger formatted specifically for the "Jenn Mike Finance Tracker" Google Sheet.

## What It Does
1. **Extracts**: Reads raw bank transaction CSVs (Rogers CC, Simplii, and CIBC) from `data/raw/` and detailed Amazon exports (`Order History.csv`, `Refund Details.csv`) from `data/reference/`.
2. **Transforms**: 
   - Normalizes dates to the strict `YYYY-MM-DD` format.
   - Standardizes amounts (Income as positive floats, Expenses as negative floats).
   - Reconciles generic Amazon bank transactions with detailed Amazon exports, automatically expanding them into individual product rows for precise tracking. Itemized product descriptions are placed into the `Note` field while preserving the original bank transaction description in `Transaction Details`.
   - Extracts unique merchants and queries the Gemini 3.6 Flash API to assign categories. This API call is contextualized heavily using a predefined allowed categories list (`Categories.csv`) and historical examples (`Mike Transaction Register.csv`).
3. **Optimizes**: 
   - Uses defensive CSV parsing with multi-format date coalescing (`%Y-%m-%d`, `%m/%d/%Y`, `%Y/%m/%d`) across bank statement parsers and Amazon exports (`Order History.csv`, `Refund Details.csv`) to guarantee format resilience and prevent silent transaction drops.
   - Normalizes merchant names (e.g. stripping Amazon order hashes) to cut down redundant LLM categorization calls by up to 70%.
   - Chunks Gemini API requests into batches of 50 with incremental cache saves to ensure fault tolerance.
4. **Soft-Filtering**: Automatically establishes a "Personal Items Profile" by identifying past Amazon shipments that were excluded from the historical register. It sorts and caps this context deterministically before leveraging Gemini to recognize and flag similar personal purchases in new transactions, significantly reducing manual review.
5. **Validates**: Automatically performs strict multi-stage validation checks comparing the raw extracted data against post-expansion data and final formatted output. It verifies that raw vs. post-expansion balances match (within $0.05 to account for itemized tax rounding) and that post-expansion vs. final formatted row counts and balances match to ensure no transactions are dropped or corrupted. Mismatches or dropped transactions are clearly flagged and displayed to the user.
6. **Deduplicates**: Preserves legitimate same-day identical transactions (e.g., buying two identical coffees on the same day) using file-scoped occurrence indexing, while using unique `Reference Number` fields where available (e.g. Rogers CC) to safely eliminate duplicate records across overlapping file exports.
7. **Loads**: Joins the LLM categorization back to the main DataFrame, sorts the data chronologically by Date (and alphabetically by Account), and formats it into a strict 12-column output with guaranteed fallback schema defaults for unmapped items. The pipeline also automatically cleans up old processed files, keeping only the most recent runs.
8. **Logs**: Standard logs and error messages are written both to the console and to `pipeline.log` for easy troubleshooting. At the end of every run, a helpful post-run summary report is output to provide an at-a-glance view of the processed data.

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
- Ensure reference CSV files are placed in `data/reference/`:
  - `Jenn Mike Finance Tracker - Categories.csv` (Allowed category taxonomy)
  - `Jenn Mike Finance Tracker - Mike Transaction Register.csv` (Historical shared expense register)
  - `Order History.csv` (Amazon purchases export)
  - `Refund Details.csv` (Amazon returns export)

## Amazon Data & Reference Files

### Data Update Expectations & Workflow
The pipeline processes reference files dynamically from `data/reference/`:
* **Fresh Amazon Exports**: To expand new bank charges into itemized product descriptions, export fresh data from your Amazon account (*Accounts & Lists -> Request Your Information -> Orders*) and place/overwrite `Order History.csv` and `Refund Details.csv` in `data/reference/`.
* **Fallback Behavior**: If you do not update the Amazon export files, the pipeline will not crash or fail. Any new Amazon bank charges will simply fail to match a shipment and will **safely remain generic** (e.g., `"AMZN Mktp CA"`), falling back to standard LLM categorization without line-item expansion.
* **Updating Historical Register**: Periodically updating `Jenn Mike Finance Tracker - Mike Transaction Register.csv` allows the system to continuously learn. As new shared expenses are approved, the "Personal Items Profile" automatically updates to recognize personal vs. shared items more accurately.

### Amazon Reconciliation Process
1. **Personal Profile Construction**: Reconciles `Order History.csv` shipments against `Mike Transaction Register.csv` within a `[-3, +7]` day window (`bank_date - ship_date`). Any product in `Order History.csv` that was *never* entered into the shared register is tagged as a **Personal Item**. Items are sorted alphabetically and capped to 50 entries to ensure deterministic Gemini context injection across runs.
2. **Transaction Expansion**: For incoming generic Amazon bank debits/credits (`data/raw/`), matches exact amounts to shipments/refunds within the `[-3, +7]` day window. Matched bank rows are expanded into individual product rows (e.g., `-$15.50 3x Item B`), and all Amazon transactions populate `"Amazon"` (or `"Amazon Refund"`) in the `Note` column to explicitly tag Amazon as the vendor.
3. **LLM Soft-Filtering**: Enriched product descriptions are categorized by Gemini. If an item matches the Personal Items Profile, Gemini sets `Suggested Filter` to `"Yes"` and `Filter Reason` to `"Likely Personal Item"`.

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

By default, the script cleans up old output files and keeps the last 3 runs. To change this, pass `--keep-last N`:
```bash
PYTHONPATH=. python src/main.py --keep-last 5
```

If you need to force a fresh recategorization by skipping the local cache, use:
```bash
PYTHONPATH=. python src/main.py --no-cache
```

### 5. Post-Processing & Importing the Output File
Once the pipeline runs, it outputs a timestamped CSV file in `data/processed/` (e.g., `processed_ledger_20260806_120000.csv`). Follow these steps to review and import your data:

1. **Open the Output File**: Open the generated CSV in `data/processed/`.
2. **Review Suggested Filters & Flags**:
   * Filter by `Suggested Filter` = `"Yes"` to quickly identify internal transfers, credit card payments, or **personal Amazon items**.
   * Review `LLM Categorization Flag` for any brief explanations where Gemini was uncertain about a categorization.
3. **Import into Google Sheet**: Copy and paste the 12-column ledger rows directly into your shared **Jenn Mike Finance Tracker** Google Sheet register.
4. **Update Reference Register (Optional)**: After reviewing and finalizing your transactions in the shared Google Sheet, export/copy the updated register back to `data/reference/Jenn Mike Finance Tracker - Mike Transaction Register.csv` to keep historical LLM context and the Personal Items Profile up to date.

## Testing
This project strictly follows Test-Driven Development (TDD). Tests are located in the `tests/` directory (e.g., `tests/test_etl.py`, `tests/test_amazon.py`) and mock the API calls and raw data ingestion.
