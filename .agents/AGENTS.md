# Finance ETL Project Rules

1. **Test-Driven Development (TDD) & Unit Verification**: Always update and run the test suite (e.g. `PYTHONPATH=. pytest tests/`) after any code changes and ensure it runs properly and passes cleanly with the new code before finalizing changes. Ensure mock data correctly simulates the raw data inputs.
2. **Data Processing Framework**: Always use `polars` for efficient data transformations.
3. **Strict Output Schema**: Any changes to the final output must adhere to the 13-column consolidated ledger layout. A firm rule is that new columns must not be mixed with the columns at the beginning, and the beginning columns' schema and order must be maintained exactly:
   - `Year Month` (Blank)
   - `Date` (YYYY-MM-DD)
   - `Transaction Details` (Cleaned merchant)
   - `Amount` (Float, Income is negative, Expense is positive)
   - `Category` (LLM determined based on Categories.csv)
   - `Account` (Source account)
   - `Note` (Blank)
   - `Reporting Category` (Blank)
   - `Categorization Explanation` (Brief explanation 10 words or less from Gemini)
   - `LLM Categorization Flag` (Brief 3-5 word explanation if difficult to determine)
   - `Suggested Filter` ("Yes" or "No")
   - `Filter Reason` (Context if "Yes")
   - `Additional Row Notes` (Blank)
4. **Gemini API Integration**: The categorization engine uses `gemini-3.6-flash`. Always maintain `thinking_level="LOW"` and `response_mime_type="application/json"`. Do not use custom sampling parameters (like `temperature`, `top_k`, `top_p`). 
5. **Contextual Prompts**: Always extract allowed categories from `data/reference/Jenn Mike Finance Tracker - Categories.csv` and sample historical mappings from `data/reference/Jenn Mike Finance Tracker - Mike Transaction Register.csv` to inject as context into the LLM prompt.
6. **Documentation Enforcement**: Ensure a `README.md` file is established in the root directory. Review it and update it as appropriate after every prompt is completed. It must always clearly explain how the project works, what it does, and how to use it.
7. **Git Branching**: Always create a new appropriately named branch for each change before making modifications. Never touch the `main` branch; it is strictly merged and managed manually unless explicitly asked in a prompt to change `main`.
8. **Defensive CSV Parsing**: Raw bank statements often have unpredictable formats. When reading raw data with Polars (`pl.read_csv`), always:
   - Strip whitespace from column names immediately (e.g., `df.rename({col: col.strip() for col in df.columns})`) to prevent `ColumnNotFoundError`.
   - Pass `infer_schema_length=0` to force all columns to parse as Strings initially, preventing integer overflow on long reference IDs.
   - Strip currency symbols and commas before casting to numeric types (e.g., `pl.col('Amount').str.replace_all(r'[\$,]', '').cast(pl.Float64)`).
   - Handle inconsistent date formats gracefully by using `pl.coalesce()` with multiple `strptime` patterns (e.g., `%Y-%m-%d` and `%m/%d/%Y`).
9. **Gemini SDK Limitations**: The legacy `google.generativeai` package strictly validates configuration fields. Do not pass unsupported or experimental parameters (like `thinking_level`) to `GenerationConfig` or `generate_content`, as the SDK will crash with an `Unknown field` error.
10. **Standard Logging**: Never use `print()` statements for pipeline output or error reporting. Always use Python's built-in `logging` module. Configure the logger to simultaneously write to a standard log file (e.g., `pipeline.log`) using a `FileHandler` and output to the terminal using a `StreamHandler` explicitly set to `sys.stdout`.
11. **LLM Batch Chunking & Incremental Caching**: When calling Gemini to process large datasets (e.g., categorizing hundreds of merchants), never send the entire list in a single prompt. Chunk the requests into smaller batches (e.g., 50 items) and perform incremental saves to the cache after each successful chunk. This ensures fault tolerance against API timeouts or malformed JSON responses.
12. **Entity Normalization for LLM Caching**: Before checking the cache or sending data to the LLM for categorization, proactively normalize the input (e.g., strip unique Amazon order hashes, store IDs, and extra whitespace). This drastically increases cache hits and cuts down on redundant API calls. Maintain a mapping of the original string to the normalized string so the final dataset retains the original transaction names.
13. **Python Execution Pathing**: When executing the pipeline (`python src/main.py`) or running the test suite (`pytest tests/`), always prepend `PYTHONPATH=.` to the command from the root directory to ensure the `src` module imports resolve correctly.
14. **Transaction Reconciliation**: When matching third-party vendor exports (e.g., Amazon orders) to raw bank statement charges, always account for credit card posting delays by implementing a trailing date window (e.g., bank date can be up to 7 days after the vendor ship/refund date) rather than requiring exact date matches.
15. **End-to-End (E2E) Real Data Testing**: As further testing to ensure system robustness, after unit tests pass, if actual real data is available (e.g., raw bank CSVs in `data/raw/` and reference CSVs in `data/reference/`), run an end-to-end execution of the full pipeline (e.g., `PYTHONPATH=. python src/main.py` or `PYTHONPATH=. python src/main.py --dry-run`). Ensure that the new code works properly with real data and verify that generated output files in `data/processed/` are reasonable, accurate, and structurally intact.
16. **Data Dictionary Maintenance**: Any time you make a change or touch the project, you must evaluate if `DATA_DICTIONARY.md` requires updating (e.g., adding new parsers, modifying the output schema, or changing reference data files) and update it accordingly to ensure it remains evergreen.
