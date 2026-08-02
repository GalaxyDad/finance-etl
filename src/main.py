import os
import sys
import logging
from datetime import datetime
import argparse
import uuid
import glob
from src.etl import process_bank_data, call_gemini_categorization, format_output, validate_pipeline, normalize_merchant_name
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

def main():
    parser = argparse.ArgumentParser(description="Finance ETL Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction without calling Gemini or exporting data")
    parser.add_argument("--no-cache", action="store_true", help="Ignore existing merchant cache and force recategorization")
    parser.add_argument("--keep-last", type=int, default=3, help="Number of recent output files to keep in processed directory")
    args = parser.parse_args()

    logger = setup_logger()
    
    raw_dir = "data/raw"
    reference_dir = "data/reference"
    processed_dir = "data/processed"
    
    logger.info("Processing bank data...")
    df = process_bank_data(raw_dir)
    
    if len(df) == 0:
        logger.info("No raw data found.")
        return
        
    unique_merchants = df['Transaction Details'].unique().to_list()
    
    if args.dry_run:
        logger.info(f"[DRY-RUN] Found {len(unique_merchants)} unique merchants. Skipping Gemini API call and export.")
        logger.info(f"[DRY-RUN] Total rows extracted: {len(df)}")
        
        # Print breakdown by account
        account_counts = df['Account'].value_counts()
        for row in account_counts.iter_rows(named=True):
            logger.info(f"[DRY-RUN]   {row['Account']}: {row['count']} rows")
            
        # Calculate actual API calls needed
        normalized_merchants = {normalize_merchant_name(m) for m in unique_merchants}
        
        cache_path = os.path.join(reference_dir, 'merchant_cache.json')
        cached_count = 0
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    merchant_cache = json.load(f)
                cached_count = sum(1 for nm in normalized_merchants if nm in merchant_cache)
            except Exception:
                pass
                
        uncached_count = len(normalized_merchants) - cached_count
        logger.info(f"[DRY-RUN] Merchant Cache Breakdown:")
        logger.info(f"[DRY-RUN]   Raw Unique Merchants: {len(unique_merchants)}")
        logger.info(f"[DRY-RUN]   Normalized Unique Merchants: {len(normalized_merchants)}")
        logger.info(f"[DRY-RUN]   Merchants in Cache: {cached_count}")
        logger.info(f"[DRY-RUN]   API Calls Needed: {uncached_count}")
        
        return
        
    if args.no_cache:
        cache_path = os.path.join(reference_dir, 'merchant_cache.json')
        if os.path.exists(cache_path):
            os.remove(cache_path)
            logger.info("Cleared existing merchant cache (--no-cache passed).")

    logger.info(f"Found {len(unique_merchants)} unique merchants. Calling Gemini for categorization...")
    
    mapping = call_gemini_categorization(unique_merchants, reference_dir)
    
    logger.info("Formatting output...")
    output_df = format_output(df, mapping)
    
    logger.info("Running validation checks...")
    is_valid, messages = validate_pipeline(df, output_df)
    for msg in messages:
        if "[FLAG]" in msg:
            logger.warning(msg)
        else:
            logger.info(msg)
            
    # Calculate Cache Hit Rate
    normalized_merchants = {normalize_merchant_name(m) for m in unique_merchants}
    cache_path = os.path.join(reference_dir, 'merchant_cache.json')
    cached_count = 0
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                merchant_cache = json.load(f)
            cached_count = sum(1 for nm in normalized_merchants if nm in merchant_cache)
        except Exception:
            pass
            
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
    
    account_counts = output_df['Account'].value_counts()
    for row in account_counts.iter_rows(named=True):
        logger.info(f"  {row['Account']}: {row['count']} rows")
    logger.info("========================")
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    filename = f"consolidated_ledger_{timestamp}_{unique_id}.csv"
    output_path = os.path.join(processed_dir, filename)
    os.makedirs(processed_dir, exist_ok=True)
    output_df.write_csv(output_path)
    logger.info(f"Success! Exported {len(output_df)} rows to {output_path}")

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
