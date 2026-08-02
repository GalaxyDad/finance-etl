import os
import sys
import logging
from datetime import datetime
import argparse
import uuid
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
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    filename = f"consolidated_ledger_{timestamp}_{unique_id}.csv"
    output_path = os.path.join(processed_dir, filename)
    os.makedirs(processed_dir, exist_ok=True)
    output_df.write_csv(output_path)
    logger.info(f"Success! Exported {len(output_df)} rows to {output_path}")

if __name__ == "__main__":
    main()
