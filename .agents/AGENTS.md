# Finance ETL Project Rules

1. **Test-Driven Development (TDD)**: Always update and run tests in `tests/test_etl.py` before modifying pipeline logic. Ensure mock data correctly simulates the raw data inputs.
2. **Data Processing Framework**: Always use `polars` for efficient data transformations.
3. **Strict Output Schema**: Any changes to the final output must adhere to the 12-column consolidated ledger layout:
   - `Year Month` (Blank)
   - `Date` (YYYY-MM-DD)
   - `Transaction Details` (Cleaned merchant)
   - `Amount` (Float, Income is positive, Expense is negative)
   - `Category` (LLM determined based on Categories.csv)
   - `Account` (Source account)
   - `Note` (Blank)
   - `Reporting Category` (Blank)
   - `LLM Categorization Flag` (Brief 3-5 word explanation if difficult to determine)
   - `Suggested Filter` ("Yes" or "No")
   - `Filter Reason` (Context if "Yes")
   - `Additional Row Notes` (Blank)
4. **Gemini API Integration**: The categorization engine uses `gemini-3.6-flash`. Always maintain `thinking_level="LOW"` and `response_mime_type="application/json"`. Do not use custom sampling parameters (like `temperature`, `top_k`, `top_p`). 
5. **Contextual Prompts**: Always extract allowed categories from `data/reference/Jenn Mike Finance Tracker - Categories.csv` and sample historical mappings from `data/reference/Jenn Mike Finance Tracker - Mike Transaction Register.csv` to inject as context into the LLM prompt.
6. **Documentation Enforcement**: Ensure a `README.md` file is established in the root directory. Review it and update it as appropriate after every prompt is completed. It must always clearly explain how the project works, what it does, and how to use it.
7. **Git Branching**: Always create a new appropriately named branch for each change before making modifications.
