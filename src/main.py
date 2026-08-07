import os
import sys
import logging
import polars as pl
from datetime import datetime
import argparse
import uuid
import glob
from src.etl import process_bank_data, call_gemini_categorization, format_output, validate_pipeline, normalize_merchant_name, load_merchant_cache
from src.amazon import AmazonProcessor
import json

def setup_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Create handlers
    file_handler = logging.FileHandler('pipeline.log')
    console_handler = logging.StreamHandler(sys.stdout)
    
    # Create formatters and add it to handlers
    log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(log_format)
    console_handler.setFormatter(log_format)
    
    # Add handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def _load_text_config(reference_dir: str, filename: str, header_lines: list[str], logger: logging.Logger) -> list[str]:
    """Load a line-per-entry config file, auto-generating a template if missing."""
    items = []
    filepath = os.path.join(reference_dir, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    items.append(line)
    else:
        logger.info(f"{filename} not found. Creating a template.")
        try:
            with open(filepath, 'w') as f:
                for h in header_lines:
                    f.write(h + "\n")
        except Exception as e:
            logger.warning(f"Failed to create {filename}: {e}")
    return items

def main():
    parser = argparse.ArgumentParser(description="Finance ETL Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction without calling Gemini or exporting data")
    parser.add_argument("--no-cache", action="store_true", help="Ignore existing merchant cache and force recategorization")
    parser.add_argument("--keep-last", type=int, default=3, help="Number of recent output files to keep in processed directory")
    parser.add_argument("--date-from", type=str, help="Filter transactions starting from this date (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=str, help="Filter transactions up to this date (YYYY-MM-DD)")
    parser.add_argument("--abort-on-validation-failure", action="store_true", help="Abort export if pipeline validation fails")
    args = parser.parse_args()

    logger = setup_logger()
    
    raw_dir = "data/raw"
    reference_dir = "data/reference"
    processed_dir = "data/processed"
    
    logger.info("Processing bank data...")
    raw_df = process_bank_data(raw_dir)
    
    if len(raw_df) == 0:
        logger.info("No raw data found.")
        return
        
    if args.date_from:
        raw_df = raw_df.filter(pl.col('Date') >= pl.lit(args.date_from).str.strptime(pl.Date, "%Y-%m-%d"))
        logger.info(f"Applied date-from filter: {args.date_from}. Rows remaining: {len(raw_df)}")
    if args.date_to:
        raw_df = raw_df.filter(pl.col('Date') <= pl.lit(args.date_to).str.strptime(pl.Date, "%Y-%m-%d"))
        logger.info(f"Applied date-to filter: {args.date_to}. Rows remaining: {len(raw_df)}")
        
    if len(raw_df) == 0:
        logger.info("No data left after date filtering.")
        return
        
    amazon_processor = AmazonProcessor(reference_dir)
    df = amazon_processor.process_transactions(raw_df)
    personal_items_profile = amazon_processor.get_personal_items_profile()
        
    unique_merchants = df['Transaction Details'].unique().to_list()
    
    personal_keywords = _load_text_config(
        reference_dir, 'personal_keywords.txt',
        [
            "# Add personal keywords or concepts here, one per line.",
            "# The Gemini LLM will soft-filter any transactions semantically matching these concepts (e.g. 'Art supplies').",
            "# Example:",
            "# Art supplies"
        ],
        logger
    )
    logger.info(f"Loaded {len(personal_keywords)} personal filtering keyword(s).")

    category_hints = _load_text_config(
        reference_dir, 'category_hints.txt',
        [
            "# Add semantic category hints here, one per line.",
            "# The Gemini LLM will prioritize these concepts when choosing a category.",
            "# Example:",
            "# Any time there is a McDonald's related transaction then we should use the category that best approximates fast-food"
        ],
        logger
    )
    logger.info(f"Loaded {len(category_hints)} category hint(s).")
    
    if args.dry_run:
        logger.info(f"[DRY-RUN] Found {len(unique_merchants)} unique merchants. Skipping Gemini API call and export.")
        logger.info(f"[DRY-RUN] Total rows extracted: {len(df)}")
        
        # Print breakdown by account
        account_counts = df['Account'].value_counts()
        for row in account_counts.iter_rows(named=True):
            logger.info(f"[DRY-RUN]   {row['Account']}: {row['count']} rows")
            
        # Calculate actual API calls needed
        normalized_merchants = {normalize_merchant_name(m) for m in unique_merchants}
        
        merchant_cache = load_merchant_cache(reference_dir)
        cached_count = sum(1 for nm in normalized_merchants if nm in merchant_cache)
                
        uncached_count = len(normalized_merchants) - cached_count
        logger.info(f"[DRY-RUN] Merchant Cache Breakdown:")
        logger.info(f"[DRY-RUN]   Raw Unique Merchants: {len(unique_merchants)}")
        logger.info(f"[DRY-RUN]   Normalized Unique Merchants: {len(normalized_merchants)}")
        logger.info(f"[DRY-RUN]   Merchants in Cache: {cached_count}")
        logger.info(f"[DRY-RUN]   API Calls Needed: {uncached_count}")
        logger.info(f"[DRY-RUN] Configuration:")
        logger.info(f"[DRY-RUN]   Personal Keywords: {len(personal_keywords)}")
        logger.info(f"[DRY-RUN]   Category Hints: {len(category_hints)}")
        
        return
        
    if args.no_cache:
        cache_path = os.path.join(reference_dir, 'merchant_cache.json')
        if os.path.exists(cache_path):
            os.remove(cache_path)
            logger.info("Cleared existing merchant cache (--no-cache passed).")

    logger.info(f"Found {len(unique_merchants)} unique merchants. Calling Gemini for categorization...")
    
    mapping = call_gemini_categorization(unique_merchants, reference_dir, personal_items_profile, personal_keywords, category_hints)
    
    logger.info("Formatting output...")
    output_df = format_output(df, mapping)
    
    logger.info("Running validation checks...")
    is_valid, messages = validate_pipeline(raw_df, df, output_df)
    for msg in messages:
        if "[FLAG]" in msg:
            logger.warning(msg)
        else:
            logger.info(msg)
            
    if args.abort_on_validation_failure and not is_valid:
        logger.error("Validation failed and --abort-on-validation-failure is set. Aborting export.")
        sys.exit(1)
            
    # Calculate Cache Hit Rate
    normalized_merchants = {normalize_merchant_name(m) for m in unique_merchants}
    merchant_cache = load_merchant_cache(reference_dir)
    cached_count = sum(1 for nm in normalized_merchants if nm in merchant_cache)
            
    # Calculate summary metrics from output_df
    # output_df['Date'] is likely string "YYYY-MM-DD" per schema, but could be Date type. 
    # To be safe, we'll stringify or just min/max directly.
    min_date = output_df['Date'].min()
    max_date = output_df['Date'].max()
    
    total_income = output_df.filter(output_df['Amount'] > 0)['Amount'].sum()
    total_expense = output_df.filter(output_df['Amount'] < 0)['Amount'].sum()
    
    flagged_merchants = len(output_df.filter(output_df['Suggested Filter'] == 'Yes'))
    
    logger.info("=== POST-RUN SUMMARY ===")
    logger.info(f"Date Range: {min_date} to {max_date}")
    logger.info(f"Total Income: ${total_income:.2f}")
    logger.info(f"Total Expenses: ${total_expense:.2f}")
    logger.info(f"Cache Hit Rate: {cached_count}/{len(normalized_merchants)} merchants cached")
    logger.info(f"Flagged Merchants (Suggested Filter='Yes'): {flagged_merchants}")
    logger.info("Row Count by Account:")
    
    account_counts = output_df.group_by('Account').agg([
        pl.col('Amount').count().alias('count'),
        pl.col('Amount').sum().alias('total')
    ])
    for row in account_counts.iter_rows(named=True):
        logger.info(f"  {row['Account']}: {row['count']} rows, Net Total: ${row['total']:.2f}")
    logger.info("========================")
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    filename = f"consolidated_ledger_{timestamp}_{unique_id}.csv"
    output_path = os.path.join(processed_dir, filename)
    os.makedirs(processed_dir, exist_ok=True)
    output_df.write_csv(output_path)
    logger.info(f"Success! Exported {len(output_df)} rows to {output_path}")

    manifest = {
        "timestamp": timestamp,
        "input_files_total_rows": len(df),
        "input_account_rows": {row['Account']: row['count'] for row in account_counts.iter_rows(named=True)},
        "total_output_rows": len(output_df),
        "cache_hits": cached_count,
        "cache_misses": len(normalized_merchants) - cached_count,
        "validation_passed": is_valid,
        "output_filename": filename
    }
    
    manifest_path = os.path.join(processed_dir, 'run_manifest.jsonl')
    try:
        with open(manifest_path, 'a') as f:
            f.write(json.dumps(manifest) + '\n')
    except Exception as e:
        logger.error(f"Failed to write run manifest: {e}")

    # Cleanup old processed files
    processed_files = glob.glob(os.path.join(processed_dir, 'consolidated_ledger_*.csv'))
    processed_files.sort(key=os.path.getmtime)
    if len(processed_files) > args.keep_last:
        files_to_delete = processed_files[:-args.keep_last]
        for f in files_to_delete:
            os.remove(f)
        logger.info(f"Cleaned up {len(files_to_delete)} old output file(s) in {processed_dir}.")

if __name__ == "__main__":
    main()
