import os
import glob
import polars as pl
from google import genai
import json
import random
import logging
import re
import time
import hashlib
from dotenv import load_dotenv
from src.amazon import AmazonProcessor

load_dotenv()

logger = logging.getLogger(__name__)

def check_and_update_keywords_hash(reference_dir: str, personal_keywords: list[str] = None, category_hints: list[str] = None) -> bool:
    """
    Checks if personal keywords or category hints have changed since the last run by comparing SHA256 hashes.
    If changed, invalidates (deletes) the merchant_cache.json file to force recategorization.
    Returns True if cache was invalidated due to keywords change, False otherwise.
    """
    combined_data = {
        "personal_keywords": sorted(personal_keywords or []),
        "category_hints": sorted(category_hints or [])
    }
    keywords_json = json.dumps(combined_data)
    current_hash = hashlib.sha256(keywords_json.encode('utf-8')).hexdigest()
    
    hash_path = os.path.join(reference_dir, '.keywords_hash')
    cache_path = os.path.join(reference_dir, 'merchant_cache.json')
    invalidated = False
    
    if os.path.exists(hash_path):
        try:
            with open(hash_path, 'r') as f:
                prev_hash = f.read().strip()
            if prev_hash != current_hash:
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    logger.info("personal_soft_filter_keywords.txt changed since last run. Invalidated merchant cache.")
                invalidated = True
        except Exception as e:
            logger.warning(f"Error checking keywords hash: {e}")
    
    try:
        with open(hash_path, 'w') as f:
            f.write(current_hash)
    except Exception as e:
        logger.warning(f"Failed to update keywords hash file: {e}")
        
    return invalidated

class BankParser:
    file_pattern: str
    
    def parse(self, filepath: str) -> pl.DataFrame:
        raise NotImplementedError

class RogersParser(BankParser):
    file_pattern = 'rogers_transactions*.csv'
    
    def parse(self, filepath: str) -> pl.DataFrame:
        df = pl.read_csv(filepath, null_values=[""], infer_schema_length=0)
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.with_columns(
            pl.coalesce([
                pl.col('Date').str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col('Date').str.strptime(pl.Date, "%m/%d/%Y", strict=False),
                pl.col('Date').str.strptime(pl.Date, "%Y/%m/%d", strict=False)
            ]).alias('Date'),
            (pl.col('Amount').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False) * -1).alias('Amount'),
            pl.lit('rogers_cc').alias('Account')
        )
        df = df.drop_nulls(subset=['Date'])
        
        if "Details" in df.columns:
            df = df.rename({"Details": "Transaction Details"})
        elif "Merchant Name" in df.columns:
            df = df.rename({"Merchant Name": "Transaction Details"})
            
        if "Reference Number" in df.columns:
            df = df.with_columns(pl.col("Reference Number").str.strip_chars('"').alias("Reference Number"))
        else:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("Reference Number"))
            
        return df.select(["Date", "Transaction Details", "Amount", "Account", "Reference Number"])

class SimpliiParser(BankParser):
    file_pattern = 'simplii*.csv'
    
    def parse(self, filepath: str) -> pl.DataFrame:
        df = pl.read_csv(filepath, null_values=[""], infer_schema_length=0)
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.with_columns(
            pl.coalesce([
                pl.col('Date').str.strptime(pl.Date, "%m/%d/%Y", strict=False),
                pl.col('Date').str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col('Date').str.strptime(pl.Date, "%Y/%m/%d", strict=False)
            ]).alias('Date'),
            pl.col('Funds Out').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col('Funds In').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False).fill_null(0.0)
        )
        df = df.drop_nulls(subset=['Date'])
        df = df.with_columns(
            (pl.col('Funds In') - pl.col('Funds Out')).alias('Amount'),
            pl.lit('simplii').alias('Account'),
            pl.lit(None, dtype=pl.Utf8).alias('Reference Number')
        )
        return df.select(["Date", "Transaction Details", "Amount", "Account", "Reference Number"])

class CIBCParser(BankParser):
    file_pattern = 'cibc*.csv'
    
    def parse(self, filepath: str) -> pl.DataFrame:
        df = pl.read_csv(filepath, has_header=False, null_values=[""], infer_schema_length=0)
        df = df.rename({
            "column_1": "Date", 
            "column_2": "Transaction Details", 
            "column_3": "Debit", 
            "column_4": "Credit", 
            "column_5": "Card"
        })
        df = df.with_columns(
            pl.coalesce([
                pl.col('Date').str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col('Date').str.strptime(pl.Date, "%m/%d/%Y", strict=False),
                pl.col('Date').str.strptime(pl.Date, "%Y/%m/%d", strict=False)
            ]).alias('Date'),
            pl.col('Debit').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col('Credit').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False).fill_null(0.0)
        )
        df = df.drop_nulls(subset=['Date'])
        df = df.with_columns(
            (pl.col('Credit') - pl.col('Debit')).alias('Amount'),
            pl.lit('cibc').alias('Account'),
            pl.lit(None, dtype=pl.Utf8).alias('Reference Number')
        )
        return df.select(["Date", "Transaction Details", "Amount", "Account", "Reference Number"])

class WSActivitiesParser(BankParser):
    file_pattern = 'ws_activities*.csv'
    
    def parse(self, filepath: str) -> pl.DataFrame:
        df = pl.read_csv(filepath, null_values=[""], infer_schema_length=0)
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.filter(
            ~pl.col('transaction_date').str.starts_with("As of") &
            pl.col('transaction_date').str.contains(r"^\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")
        )
        df = df.with_columns(
            pl.coalesce([
                pl.col('transaction_date').str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col('transaction_date').str.strptime(pl.Date, "%m/%d/%Y", strict=False),
                pl.col('transaction_date').str.strptime(pl.Date, "%Y/%m/%d", strict=False)
            ]).alias('transaction_date'),
            pl.lit('wealthsimple_activities').alias('Account'),
            pl.col('net_cash_amount').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False).alias('Amount'),
            pl.lit(None, dtype=pl.Utf8).alias('Reference Number')
        )
        df = df.drop_nulls(subset=['transaction_date'])
        df = df.rename({"description": "Transaction Details", "transaction_date": "Date"})
        return df.select(["Date", "Transaction Details", "Amount", "Account", "Reference Number"])

class WSCreditParser(BankParser):
    file_pattern = 'ws_credit-card*.csv'
    
    def parse(self, filepath: str) -> pl.DataFrame:
        df = pl.read_csv(filepath, null_values=[""], infer_schema_length=0)
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.filter(
            ~pl.col('transaction_date').str.starts_with("As of") &
            pl.col('transaction_date').str.contains(r"^\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")
        )
        df = df.with_columns(
            pl.coalesce([
                pl.col('transaction_date').str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col('transaction_date').str.strptime(pl.Date, "%m/%d/%Y", strict=False),
                pl.col('transaction_date').str.strptime(pl.Date, "%Y/%m/%d", strict=False)
            ]).alias('transaction_date'),
            pl.lit('wealthsimple_credit').alias('Account'),
            pl.when(pl.col('merchant').is_null() | (pl.col('merchant') == "")).then(pl.col('transaction_type')).otherwise(pl.col('merchant')).alias('Transaction Details'),
            pl.col('amount').str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False).alias('Amount'),
            pl.lit(None, dtype=pl.Utf8).alias('Reference Number')
        )
        df = df.drop_nulls(subset=['transaction_date'])
        df = df.rename({"transaction_date": "Date"})
        return df.select(["Date", "Transaction Details", "Amount", "Account", "Reference Number"])

BANK_PARSERS = [
    RogersParser(),
    SimpliiParser(),
    CIBCParser(),
    WSActivitiesParser(),
    WSCreditParser()
]


def _ci_glob(directory: str, pattern: str) -> list[str]:
    """Case-insensitive glob within a directory."""
    import fnmatch
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if fnmatch.fnmatch(f.lower(), pattern.lower())
    )


def process_bank_data(raw_dir):
    dfs = []
    
    for parser in BANK_PARSERS:
        files = _ci_glob(raw_dir, parser.file_pattern)
        for f in files:
            try:
                df = parser.parse(f)
                if len(df) > 0:
                    df = df.sort(["Date", "Transaction Details", "Amount", "Account"])
                    df = df.with_columns(
                        pl.col("Date").cum_count().over(["Date", "Transaction Details", "Amount", "Account"]).alias("_occur_idx")
                    )
                    dfs.append(df)
            except Exception as e:
                logger.error(f"Error parsing file {f} with {parser.__class__.__name__}: {e}")
                
    if not dfs:
        return pl.DataFrame(schema={"Date": pl.Date, "Transaction Details": pl.Utf8, "Amount": pl.Float64, "Account": pl.Utf8})
        
    combined = pl.concat(dfs)
    initial_len = len(combined)
    
    has_ref_mask = pl.col("Reference Number").is_not_null() & (pl.col("Reference Number") != "")
    has_ref = combined.filter(has_ref_mask)
    no_ref = combined.filter(~has_ref_mask)
    
    deduped_parts = []
    if len(has_ref) > 0:
        deduped_has_ref = has_ref.unique(subset=["Account", "Reference Number"], keep="first")
        deduped_parts.append(deduped_has_ref)
        
    if len(no_ref) > 0:
        deduped_no_ref = no_ref.unique(subset=["Date", "Transaction Details", "Amount", "Account", "_occur_idx"], keep="first")
        deduped_parts.append(deduped_no_ref)
        
    final_df = pl.concat(deduped_parts) if deduped_parts else pl.DataFrame()
    dropped = initial_len - len(final_df)
    if dropped > 0:
        logger.info(f"Dropped {dropped} duplicate transactions across bank files.")
        
    return final_df.select(["Date", "Transaction Details", "Amount", "Account"])


def normalize_merchant_name(name: str) -> str:
    """Normalize merchant name to reduce API calls by stripping unique hashes and extra spaces."""
    if not isinstance(name, str) or not name:
        return ""
    
    name = name.strip().upper()
    # Remove Amazon order hashes (e.g. *7U9I78QL3)
    name = re.sub(r'\*[A-Z0-9]{8,15}\b', '', name)
    # Remove #1234 style IDs
    name = re.sub(r'\#[0-9]{3,8}\b', '', name)
    # Remove multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def load_merchant_cache(reference_dir: str) -> dict:
    cache_path = os.path.join(reference_dir, 'merchant_cache.json')
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error reading cache: {e}")
    return {}


def call_gemini_categorization(unique_merchants, reference_dir, personal_items_profile=None, personal_keywords=None, category_hints=None):
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    # 0. Load Cache (check keywords hash for cache invalidation)
    check_and_update_keywords_hash(reference_dir, personal_keywords, category_hints)
    cache_path = os.path.join(reference_dir, 'merchant_cache.json')
    merchant_cache = load_merchant_cache(reference_dir)
            
    # Filter out already cached merchants using normalized names
    uncached_normalized = set()
    original_to_normalized = {}
    cache_hits = 0
    
    for m in unique_merchants:
        norm = normalize_merchant_name(m)
        original_to_normalized[m] = norm
        if norm not in merchant_cache:
            uncached_normalized.add(norm)
        else:
            cache_hits += 1
            
    uncached_merchants = list(uncached_normalized)
    
    if not uncached_merchants:
        logger.info("All merchants found in cache. Skipping Gemini API call.")
        result_mapping = {m: merchant_cache[norm] for m, norm in original_to_normalized.items() if norm in merchant_cache}
        logger.info(f"Cache: {cache_hits} hits, 0 new lookups, {len(unique_merchants)} total merchants")
        return result_mapping
    
    # 1. Read Allowed Categories
    categories = []
    cat_path = os.path.join(reference_dir, 'Jenn Mike Finance Tracker - Categories.csv')
    if os.path.exists(cat_path):
        cat_df = pl.read_csv(cat_path, has_header=False, skip_rows=1)
        categories = cat_df.select(pl.nth(2)).drop_nulls().to_series().unique().to_list()
        categories = [c for c in categories if c.strip()]
        
    # 2. Read Historical Register Context
    history_context = []
    reg_path = os.path.join(reference_dir, 'Jenn Mike Finance Tracker - Mike Transaction Register.csv')
    if os.path.exists(reg_path):
        try:
            reg_df = pl.read_csv(reg_path, ignore_errors=True)
            if len(reg_df) > 0 and 'Transaction Details' in reg_df.columns and 'Category' in reg_df.columns:
                sampled = reg_df.sample(min(100, len(reg_df)))
                for row in sampled.iter_rows(named=True):
                    merchant = row.get('Transaction Details')
                    cat = row.get('Category')
                    if merchant and cat:
                        history_context.append({"merchant": merchant, "category": cat})
        except Exception as e:
            logger.warning(f"Error reading register: {e}")
            pass

    config = {
        "response_mime_type": "application/json",
        # "thinking_level": "LOW" # SDK throws Unknown field error
    }
    
    chunk_size = 50
    for i in range(0, len(uncached_merchants), chunk_size):
        chunk = uncached_merchants[i:i + chunk_size]
        prompt = f"""
        Categorize the following list of bank transaction merchants.
        
        Allowed Categories:
        {json.dumps(categories)}
        
        Historical Context (Examples of past mappings):
        {json.dumps(history_context)}
        
        Return a JSON object where keys are the exact merchant names provided below and values are objects containing:
        - category (string): Must be one of the Allowed Categories. Prioritize the User Semantic Concepts below to determine this category.
        - categorization_explanation (string): A brief explanation (10 words or less) of why you chose this category.
        - flag (string): If the category was difficult to determine, provide a brief 3 to 5 word explanation. Otherwise, leave blank "".
        - suggested_filter (string): "Yes" if the transaction appears to be an internal transfer, credit card payment, ATM withdrawal, or declined/pending transaction. ALSO set to "Yes" if the transaction closely matches an item in the Personal Items Profile below or semantically relates to any of the concepts or categories listed in the Personal Filtering Keywords below (e.g. if the keyword is "Art supplies", flag any transaction purchasing art supplies). Otherwise, "No".
        - filter_reason (string): If suggested_filter is "Yes", state why (e.g. "Credit Card Payment", "Likely Personal Item"). Otherwise, blank "".
        
        User Semantic Concepts (Category Hints - PRIORITIZE THESE):
        {json.dumps(category_hints) if category_hints else "[]"}
        
        Personal Items Profile (Historically excluded personal purchases):
        {json.dumps(personal_items_profile) if personal_items_profile else "[]"}
        
        Personal Filtering Keywords:
        {json.dumps(personal_keywords) if personal_keywords else "[]"}
        
        Merchants to categorize:
        {json.dumps(chunk)}
        """
        
        max_retries = 3
        backoff_times = [2, 4, 8]
        
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=prompt,
                    config=config
                )
                resp_text = response.text.strip()
                if resp_text.startswith("```"):
                    resp_text = re.sub(r'^```(?:json)?\s*', '', resp_text)
                    resp_text = re.sub(r'\s*```$', '', resp_text)
                new_mappings = json.loads(resp_text)
                
                merchant_cache.update(new_mappings)
                try:
                    with open(cache_path, 'w') as f:
                        json.dump(merchant_cache, f, indent=4)
                except Exception as e:
                    logger.warning(f"Error writing cache: {e}")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Error calling Gemini for chunk {i}: {e}. Retrying in {backoff_times[attempt]}s...")
                    time.sleep(backoff_times[attempt])
                else:
                    logger.error(f"Failed calling Gemini for chunk {i} after {max_retries} attempts: {e}")

    result_mapping = {}
    for m in unique_merchants:
        norm = original_to_normalized[m]
        if norm in merchant_cache:
            result_mapping[m] = merchant_cache[norm]
            
    logger.info(f"Cache: {cache_hits} hits, {len(uncached_normalized)} new lookups, {len(unique_merchants)} total merchants")
    return result_mapping


def format_output(df, mapping):
    mapping_data = {
        "Transaction Details": [],
        "Category": [],
        "Categorization Explanation": [],
        "LLM Categorization Flag": [],
        "Suggested Filter": [],
        "Filter Reason": []
    }
    
    for merchant, data in mapping.items():
        mapping_data["Transaction Details"].append(merchant)
        mapping_data["Category"].append(data.get("category", ""))
        mapping_data["Categorization Explanation"].append(data.get("categorization_explanation", ""))
        mapping_data["LLM Categorization Flag"].append(data.get("flag", ""))
        mapping_data["Suggested Filter"].append(data.get("suggested_filter", "No"))
        mapping_data["Filter Reason"].append(data.get("filter_reason", ""))
        
    if not mapping:
        mapping_df = pl.DataFrame({
            "Transaction Details": pl.Series(dtype=pl.Utf8),
            "Category": pl.Series(dtype=pl.Utf8),
            "Categorization Explanation": pl.Series(dtype=pl.Utf8),
            "LLM Categorization Flag": pl.Series(dtype=pl.Utf8),
            "Suggested Filter": pl.Series(dtype=pl.Utf8),
            "Filter Reason": pl.Series(dtype=pl.Utf8)
        })
    else:
        mapping_df = pl.DataFrame(mapping_data)
    
    df = df.join(mapping_df, on="Transaction Details", how="left")

    null_cat_mask = pl.col("Category").is_null()
    
    missing_count = len(df.filter(null_cat_mask))
    if missing_count > 0:
        logger.warning(f"Found {missing_count} transactions with no category mapping. Defaulting to UNCATEGORIZED.")

    # If the df already had Suggested Filter and Filter Reason (e.g., from amazon.py)
    # The left join with suffix "_llm" would rename the mapping columns to "Suggested Filter_llm", etc.
    # Wait, the join above did not specify a suffix, so the default is "_right".
    
    if "Suggested Filter_right" in df.columns:
        suggested_filter_col = pl.when(pl.col("Suggested Filter").is_not_null() & (pl.col("Suggested Filter") != "")).then(pl.col("Suggested Filter")).otherwise(pl.col("Suggested Filter_right")).fill_null("No")
        filter_reason_col = pl.when(pl.col("Filter Reason").is_not_null() & (pl.col("Filter Reason") != "")).then(pl.col("Filter Reason")).otherwise(pl.col("Filter Reason_right")).fill_null("")
        
        df = df.with_columns(
            suggested_filter_col.alias("Suggested Filter"),
            filter_reason_col.alias("Filter Reason")
        ).drop(["Suggested Filter_right", "Filter Reason_right"])
    else:
        df = df.with_columns(
            pl.col("Suggested Filter").fill_null("No"),
            pl.col("Filter Reason").fill_null("")
        )
    
    df = df.with_columns(
        pl.when(null_cat_mask).then(pl.lit("UNCATEGORIZED")).otherwise(pl.col("Category")).alias("Category"),
        pl.when(null_cat_mask).then(pl.lit("LLM Failure")).otherwise(pl.col("LLM Categorization Flag")).alias("LLM Categorization Flag")
    )
    
    # Sort chronologically by Date, then alphabetically by Account
    df = df.sort(["Date", "Account"])
    
    # Add remaining columns and convert Date to YYYY-MM-DD string
    note_col = pl.col("Note").fill_null("") if "Note" in df.columns else pl.lit("")
    df = df.with_columns(
        pl.lit("").alias("Year Month"),
        pl.col("Date").dt.strftime("%Y-%m-%d"),
        note_col.alias("Note"),
        pl.lit("").alias("Reporting Category"),
        pl.lit("").alias("Additional Row Notes")
    )
    
    expected_columns = [
        'Year Month', 'Date', 'Transaction Details', 'Amount', 'Category', 
        'Categorization Explanation', 'Account', 'Note', 'Reporting Category', 'LLM Categorization Flag', 
        'Suggested Filter', 'Filter Reason', 'Additional Row Notes'
    ]
    
    return df.select(expected_columns)


def validate_pipeline(raw_df: pl.DataFrame, post_expansion_df: pl.DataFrame, final_df: pl.DataFrame):
    is_valid = True
    messages = []
    
    # Pre-expansion checks
    raw_count = len(raw_df)
    raw_balance = raw_df["Amount"].fill_null(0.0).sum()
    messages.append(f"[INFO] Pre-expansion validation: {raw_count} raw rows extracted. Balance: ${raw_balance:.2f}")
    
    # Post-expansion checks
    post_count = len(post_expansion_df)
    final_count = len(final_df)
    
    post_balance = post_expansion_df["Amount"].fill_null(0.0).sum()
    final_balance = final_df["Amount"].fill_null(0.0).sum()
    
    if abs(raw_balance - post_balance) > 0.05:
        messages.append(f"[FLAG] Amazon expansion balance mismatch! Raw balance: ${raw_balance:.2f}, Post-expansion balance: ${post_balance:.2f}")
        is_valid = False
        
    uncategorized = final_df.filter(pl.col("Category") == "UNCATEGORIZED")
    if len(uncategorized) > 0:
        messages.append(f"[FLAG] {len(uncategorized)} transactions are missing LLM categorization (defaulted to UNCATEGORIZED).")
    
    if post_count != final_count:
        messages.append(f"[FLAG] Row count mismatch! Expected {post_count} rows (post-expansion), but got {final_count} rows.")
        is_valid = False
        
    if abs(post_balance - final_balance) > 0.01:
        messages.append(f"[FLAG] Balance mismatch! Expected post-expansion balance: {post_balance:.2f}, Final balance: {final_balance:.2f}")
        is_valid = False
        
    post_compare = post_expansion_df.with_columns(pl.col("Date").dt.strftime("%Y-%m-%d"))
    base_cols = ["Date", "Transaction Details", "Amount", "Account"]
    
    try:
        missing_rows = post_compare.join(final_df, on=base_cols, how="anti")
        if len(missing_rows) > 0:
            messages.append(f"[FLAG] The following {len(missing_rows)} transactions from the post-expansion data are missing in the final output:")
            messages.append(str(missing_rows))
            is_valid = False
    except Exception as e:
        messages.append(f"[FLAG] Could not verify missing transactions due to error: {e}")
        is_valid = False
        
    if is_valid:
        messages.append("[SUCCESS] Pipeline validation passed! Row counts and balances match.")
        
    return is_valid, messages

