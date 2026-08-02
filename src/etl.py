import os
import glob
import polars as pl
import google.generativeai as genai
import json
import random
from dotenv import load_dotenv

load_dotenv()

def process_bank_data(raw_dir):
    dfs = []
    
    # Process Rogers CC
    rogers_path = os.path.join(raw_dir, 'rogers_cc.csv')
    if os.path.exists(rogers_path):
        df = pl.read_csv(rogers_path, null_values=[""])
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.with_columns(
            pl.col('Date').str.strptime(pl.Date, "%Y-%m-%d"),
            (pl.col('Amount') * -1).alias('Amount'),
            pl.lit('rogers_cc').alias('Account')
        )
        df = df.rename({"Details": "Transaction Details"}).select(["Date", "Transaction Details", "Amount", "Account"])
        dfs.append(df)
        
    # Process Simplii
    simplii_path = os.path.join(raw_dir, 'simplii.csv')
    if os.path.exists(simplii_path):
        df = pl.read_csv(simplii_path, null_values=[""])
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.with_columns(
            pl.col('Date').str.strptime(pl.Date, "%m/%d/%Y"),
            pl.col('Funds Out').fill_null(0.0),
            pl.col('Funds In').fill_null(0.0)
        )
        df = df.with_columns(
            (pl.col('Funds In') - pl.col('Funds Out')).alias('Amount'),
            pl.lit('simplii').alias('Account')
        )
        df = df.select(["Date", "Transaction Details", "Amount", "Account"])
        dfs.append(df)
        
    # Process CIBC
    cibc_path = os.path.join(raw_dir, 'cibc.csv')
    if os.path.exists(cibc_path):
        # Headerless: Date, Description, Debit, Credit, Card
        df = pl.read_csv(cibc_path, has_header=False, null_values=[""])
        df = df.rename({
            "column_1": "Date", 
            "column_2": "Transaction Details", 
            "column_3": "Debit", 
            "column_4": "Credit", 
            "column_5": "Card"
        })
        df = df.with_columns(
            pl.col('Date').str.strptime(pl.Date, "%Y-%m-%d"),
            pl.col('Debit').fill_null(0.0),
            pl.col('Credit').fill_null(0.0)
        )
        df = df.with_columns(
            (pl.col('Credit') - pl.col('Debit')).alias('Amount'),
            pl.lit('cibc').alias('Account')
        )
        df = df.select(["Date", "Transaction Details", "Amount", "Account"])
        dfs.append(df)
        
    # Process WS Activities
    ws_act_files = glob.glob(os.path.join(raw_dir, 'ws_activities*.csv'))
    for f in ws_act_files:
        df = pl.read_csv(f, null_values=[""])
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.with_columns(
            pl.col('transaction_date').str.strptime(pl.Date, "%Y-%m-%d"),
            pl.lit('wealthsimple_activities').alias('Account'),
            pl.col('net_cash_amount').cast(pl.Float64).alias('Amount')
        )
        df = df.rename({"description": "Transaction Details", "transaction_date": "Date"})
        df = df.select(["Date", "Transaction Details", "Amount", "Account"])
        dfs.append(df)
        
    # Process WS Credit
    ws_cc_files = glob.glob(os.path.join(raw_dir, 'ws_credit-card*.csv'))
    for f in ws_cc_files:
        df = pl.read_csv(f, null_values=[""])
        df = df.rename({col: col.strip() for col in df.columns})
        df = df.with_columns(
            pl.col('transaction_date').str.strptime(pl.Date, "%Y-%m-%d"),
            pl.lit('wealthsimple_credit').alias('Account'),
            pl.when(pl.col('merchant').is_null() | (pl.col('merchant') == "")).then(pl.col('transaction_type')).otherwise(pl.col('merchant')).alias('Transaction Details'),
            pl.col('amount').cast(pl.Float64).alias('Amount')
        )
        df = df.rename({"transaction_date": "Date"})
        df = df.select(["Date", "Transaction Details", "Amount", "Account"])
        dfs.append(df)
        
    if not dfs:
        return pl.DataFrame()
        
    return pl.concat(dfs)


def call_gemini_categorization(unique_merchants, reference_dir):
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    
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
            print(f"Error reading register: {e}")
            pass

    model = genai.GenerativeModel('gemini-3.6-flash')
    
    prompt = f"""
    Categorize the following list of bank transaction merchants.
    
    Allowed Categories:
    {json.dumps(categories)}
    
    Historical Context (Examples of past mappings):
    {json.dumps(history_context)}
    
    Return a JSON object where keys are the merchant names and values are objects containing:
    - category (string): Must be one of the Allowed Categories.
    - flag (string): If the category was difficult to determine, provide a brief 3 to 5 word explanation. Otherwise, leave blank "".
    - suggested_filter (string): "Yes" if the transaction appears to be an internal transfer, credit card payment, ATM withdrawal, or declined/pending transaction. Otherwise, "No".
    - filter_reason (string): If suggested_filter is "Yes", state why (e.g. "Credit Card Payment", "Internal Transfer", "Declined Transaction"). Otherwise, blank "".
    
    Merchants to categorize:
    {json.dumps(unique_merchants)}
    """
    
    generation_config = {
        "response_mime_type": "application/json",
        # "thinking_level": "LOW" # SDK throws Unknown field error
    }
    
    try:
        response = model.generate_content(prompt, generation_config=generation_config)
        return json.loads(response.text)
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return {}


def format_output(df, mapping):
    mapping_data = {
        "Transaction Details": [],
        "Category": [],
        "LLM Categorization Flag": [],
        "Suggested Filter": [],
        "Filter Reason": []
    }
    
    for merchant, data in mapping.items():
        mapping_data["Transaction Details"].append(merchant)
        mapping_data["Category"].append(data.get("category", ""))
        mapping_data["LLM Categorization Flag"].append(data.get("flag", ""))
        mapping_data["Suggested Filter"].append(data.get("suggested_filter", "No"))
        mapping_data["Filter Reason"].append(data.get("filter_reason", ""))
        
    if not mapping:
        mapping_df = pl.DataFrame({
            "Transaction Details": pl.Series(dtype=pl.Utf8),
            "Category": pl.Series(dtype=pl.Utf8),
            "LLM Categorization Flag": pl.Series(dtype=pl.Utf8),
            "Suggested Filter": pl.Series(dtype=pl.Utf8),
            "Filter Reason": pl.Series(dtype=pl.Utf8)
        })
    else:
        mapping_df = pl.DataFrame(mapping_data)
    
    df = df.join(mapping_df, on="Transaction Details", how="left")
    
    # Sort chronologically by Date, then alphabetically by Account
    df = df.sort(["Date", "Account"])
    
    # Add remaining columns and convert Date to YYYY-MM-DD string
    df = df.with_columns(
        pl.lit("").alias("Year Month"),
        pl.col("Date").dt.strftime("%Y-%m-%d"),
        pl.lit("").alias("Note"),
        pl.lit("").alias("Reporting Category"),
        pl.lit("").alias("Additional Row Notes")
    )
    
    expected_columns = [
        'Year Month', 'Date', 'Transaction Details', 'Amount', 'Category', 
        'Account', 'Note', 'Reporting Category', 'LLM Categorization Flag', 
        'Suggested Filter', 'Filter Reason', 'Additional Row Notes'
    ]
    
    return df.select(expected_columns)
