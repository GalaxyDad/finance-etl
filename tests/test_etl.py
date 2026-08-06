import os
import polars as pl
from src.etl import process_bank_data, call_gemini_categorization, format_output, validate_pipeline

def test_pipeline_end_to_end(mock_data_dir, monkeypatch):
    raw_dir = mock_data_dir['raw_dir']
    processed_dir = mock_data_dir['processed_dir']
    reference_dir = mock_data_dir['reference_dir']
    
    def mock_call_gemini(merchants, ref_dir):
        return {
            'WALMART SUPERCENTER': {'category': 'Food & Dining: Groceries', 'flag': '', 'suggested_filter': 'No', 'filter_reason': ''},
            'PAYMENT - THANK YOU': {'category': 'Transfer', 'flag': '', 'suggested_filter': 'Yes', 'filter_reason': 'Credit card payment'},
            'MCDONALDS RESTAURANT': {'category': 'Food & Dining: Fast Food', 'flag': '', 'suggested_filter': 'No', 'filter_reason': ''},
            'PAYROLL DEPOSIT': {'category': '_Income: Bonus', 'flag': '', 'suggested_filter': 'No', 'filter_reason': ''},
            'PAYMENT THANK YOU': {'category': 'Transfer', 'flag': '', 'suggested_filter': 'Yes', 'filter_reason': 'Credit card payment'},
            'COSTCO WHOLESALE': {'category': 'Food & Dining: Groceries', 'flag': 'Unsure about bulk store', 'suggested_filter': 'No', 'filter_reason': ''},
            'Deposit': {'category': 'Transfer', 'flag': '', 'suggested_filter': 'No', 'filter_reason': ''},
            'Mcdonalds 23192': {'category': 'Food & Dining: Fast Food', 'flag': '', 'suggested_filter': 'No', 'filter_reason': ''},
            'Payment': {'category': 'Transfer', 'flag': '', 'suggested_filter': 'Yes', 'filter_reason': 'Internal'}
        }
    
    # 1. Process Bank Data
    df, personal_items_profile = process_bank_data(raw_dir, reference_dir)
    
    assert len(df) == 9
    assert df.schema['Date'] == pl.Date
    
    # Verify amounts
    costco = df.filter(pl.col('Transaction Details') == 'COSTCO WHOLESALE')
    assert costco['Amount'][0] == -113.60
    
    payment = df.filter(pl.col('Transaction Details') == 'PAYMENT THANK YOU')
    assert payment['Amount'][0] == 200.00

    ws_act = df.filter(pl.col('Transaction Details') == 'Deposit')
    assert ws_act['Amount'][0] == 5000.0
    assert ws_act['Account'][0] == 'wealthsimple_activities'

    ws_cc = df.filter(pl.col('Transaction Details') == 'Mcdonalds 23192')
    assert ws_cc['Amount'][0] == -12.86
    assert ws_cc['Account'][0] == 'wealthsimple_credit'
    
    # Verify blank merchant fallback
    ws_payment = df.filter(pl.col('Transaction Details') == 'Payment')
    assert ws_payment['Amount'][0] == 200.00
    
    # 2. Extract unique merchants and call Gemini
    unique_merchants = df['Transaction Details'].unique().to_list()
    mapping = mock_call_gemini(unique_merchants, reference_dir)
    
    # 3. Format Output
    output_df = format_output(df, mapping)
    
    # Check blank columns
    assert output_df['Year Month'][0] == ""
    assert output_df['Reporting Category'][0] == ""
    
    # Check Flag and Filter
    walmart_out = output_df.filter(pl.col('Transaction Details') == 'WALMART SUPERCENTER')
    assert walmart_out['Category'][0] == 'Food & Dining: Groceries'
    assert walmart_out['Suggested Filter'][0] == 'No'
    
    payment_out = output_df.filter(pl.col('Transaction Details') == 'PAYMENT THANK YOU')
    assert payment_out['Suggested Filter'][0] == 'Yes'
    assert payment_out['Filter Reason'][0] == 'Credit card payment'
    
    costco_out = output_df.filter(pl.col('Transaction Details') == 'COSTCO WHOLESALE')
    assert costco_out['LLM Categorization Flag'][0] == 'Unsure about bulk store'
    
    # Check sorting
    # Data has dates: 2023-10-01, 2023-10-02, 2023-10-05, 2023-10-15, 2023-10-20, 2023-10-21
    # Sorted chronologically:
    dates = output_df['Date'].to_list()
    # Ensure they are strings "YYYY-MM-DD"
    assert type(dates[0]) == str
    assert dates == sorted(dates)
    assert dates[0] == '2023-10-01'
    
    # 4. Validate Pipeline
    is_valid, messages = validate_pipeline(df, output_df)
    assert is_valid is True
    assert "[SUCCESS]" in messages[-1]

def test_validate_pipeline_failures():
    # Create mock dataframes with mismatches
    raw_df = pl.DataFrame({
        "Date": ["2023-01-01", "2023-01-02"],
        "Transaction Details": ["Merchant A", "Merchant B"],
        "Amount": [10.0, -5.0],
        "Account": ["acc1", "acc2"]
    }).with_columns(pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d"))
    
    # Drop a row and change amount
    final_df = pl.DataFrame({
        "Date": ["2023-01-01"],
        "Transaction Details": ["Merchant A"],
        "Amount": [10.0],
        "Account": ["acc1"],
        "Category": ["UNCATEGORIZED"]
    })
    
    is_valid, messages = validate_pipeline(raw_df, final_df)
    assert is_valid is False
    assert any("Row count mismatch" in msg for msg in messages)
    assert any("Balance mismatch" in msg for msg in messages)
    assert any("missing in the final output" in msg for msg in messages)

import pytest
import glob
from src.etl import BANK_PARSERS

@pytest.mark.parametrize("parser", BANK_PARSERS, ids=lambda p: p.__class__.__name__)
def test_production_parser_drift(parser):
    raw_dir = "data/raw"
    import fnmatch
    if not os.path.isdir(raw_dir):
        pytest.skip(f"{raw_dir} does not exist.")
        
    files = sorted(
        os.path.join(raw_dir, f)
        for f in os.listdir(raw_dir)
        if fnmatch.fnmatch(f.lower(), parser.file_pattern.lower())
    )
    if not files:
        pytest.skip(f"No real files found for {parser.__class__.__name__}")
        
    filepath = files[0]
    
    import tempfile
    
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        head_lines = [f.readline() for _ in range(5)]
        
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as tmp:
        for line in head_lines:
            if line:
                tmp.write(line)
        tmp_path = tmp.name
        
    try:
        df = parser.parse(tmp_path)
        assert set(df.columns) == {"Date", "Transaction Details", "Amount", "Account"}
    finally:
        os.remove(tmp_path)
