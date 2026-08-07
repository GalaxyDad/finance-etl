import os
import polars as pl
from src.etl import process_bank_data, call_gemini_categorization, format_output, validate_pipeline, check_and_update_keywords_hash

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
    
    from src.amazon import AmazonProcessor
    # 1. Process Bank Data
    raw_df = process_bank_data(raw_dir)
    amazon_processor = AmazonProcessor(reference_dir)
    df = amazon_processor.process_transactions(raw_df)
    personal_items_profile = amazon_processor.get_personal_items_profile()
    
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
    is_valid, messages = validate_pipeline(raw_df, df, output_df)
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
    
    is_valid, messages = validate_pipeline(raw_df, raw_df, final_df)
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
        assert set(df.columns) == {"Date", "Transaction Details", "Amount", "Account", "Reference Number"}
    finally:
        os.remove(tmp_path)


def test_process_bank_data_empty_dir(tmp_path):
    empty_raw = tmp_path / "raw"
    empty_raw.mkdir()
    df = process_bank_data(str(empty_raw))
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_bank_parsers_date_format_resilience(tmp_path):
    import datetime
    from src.etl import RogersParser, SimpliiParser, CIBCParser

    # Test Rogers parser with MM/DD/YYYY format
    rogers_file = tmp_path / "rogers_transactions_alt.csv"
    rogers_file.write_text("Date,Activity Status,Merchant Name,Amount\n10/05/2023,Posted,TEST MERCHANT,50.00\n")
    rogers_df = RogersParser().parse(str(rogers_file))
    assert len(rogers_df) == 1
    assert rogers_df["Date"][0] == datetime.date(2023, 10, 5)

    # Test Simplii parser with YYYY-MM-DD format
    simplii_file = tmp_path / "simplii_alt.csv"
    simplii_file.write_text("Date,Transaction Details,Funds Out,Funds In\n2023-10-06,TEST MERCHANT,25.00,\n")
    simplii_df = SimpliiParser().parse(str(simplii_file))
    assert len(simplii_df) == 1
    assert simplii_df["Date"][0] == datetime.date(2023, 10, 6)

    # Test CIBC parser with MM/DD/YYYY format
    cibc_file = tmp_path / "cibc_alt.csv"
    cibc_file.write_text("10/07/2023,TEST MERCHANT,15.00,,5223********3915\n")
    cibc_df = CIBCParser().parse(str(cibc_file))
    assert len(cibc_df) == 1
    assert cibc_df["Date"][0] == datetime.date(2023, 10, 7)


def test_format_output_unmapped_defaults():
    import datetime
    df = pl.DataFrame({
        "Date": [datetime.date(2023, 10, 1)],
        "Transaction Details": ["UNMAPPED MERCHANT"],
        "Amount": [-10.0],
        "Account": ["rogers_cc"]
    })
    mapping = {}
    out = format_output(df, mapping)
    assert out["Category"][0] == "UNCATEGORIZED"
    assert out["LLM Categorization Flag"][0] == "LLM Failure"
    assert out["Suggested Filter"][0] == "No"
    assert out["Filter Reason"][0] == ""


def test_format_output_preserves_existing_note():
    import datetime
    df = pl.DataFrame({
        "Date": [datetime.date(2023, 10, 1)],
        "Transaction Details": ["3x Item B"],
        "Amount": [-15.50],
        "Account": ["rogers_cc"],
        "Note": ["Amazon"]
    })
    mapping = {
        "3x Item B": {"category": "Shopping", "flag": "", "suggested_filter": "No", "filter_reason": ""}
    }
    out = format_output(df, mapping)
    assert out["Transaction Details"][0] == "3x Item B"
    assert out["Note"][0] == "Amazon"
    assert out["Category"][0] == "Shopping"




def test_same_day_identical_transactions_preserved(tmp_path):
    simplii_file = tmp_path / "simplii_dups.csv"
    simplii_file.write_text("Date,Transaction Details,Funds Out,Funds In\n10/02/2023,COFFEE SHOP,3.50,\n10/02/2023,COFFEE SHOP,3.50,\n")
    df = process_bank_data(str(tmp_path))
    assert len(df) == 2


def test_rogers_reference_number_deduplication(tmp_path):
    rogers_file1 = tmp_path / "rogers_transactions1.csv"
    rogers_file1.write_text("Date,Merchant Name,Amount,Reference Number\n2023-10-01,STORE A,10.00,\"REF123\"\n2023-10-02,STORE B,20.00,\"REF124\"\n")
    rogers_file2 = tmp_path / "rogers_transactions2.csv"
    rogers_file2.write_text("Date,Merchant Name,Amount,Reference Number\n2023-10-02,STORE B,20.00,\"REF124\"\n2023-10-03,STORE C,30.00,\"REF125\"\n")
    df = process_bank_data(str(tmp_path))
    assert len(df) == 3


def test_validate_pipeline_amazon_balance_mismatch():
    raw_df = pl.DataFrame({
        "Date": ["2023-01-01"],
        "Transaction Details": ["Amazon"],
        "Amount": [-100.0],
        "Account": ["rogers_cc"]
    }).with_columns(pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d"))

    post_expansion_df = pl.DataFrame({
        "Date": ["2023-01-01"],
        "Transaction Details": ["Item 1"],
        "Amount": [-50.0],
        "Account": ["rogers_cc"]
    }).with_columns(pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d"))

    final_df = pl.DataFrame({
        "Date": ["2023-01-01"],
        "Transaction Details": ["Item 1"],
        "Amount": [-50.0],
        "Account": ["rogers_cc"],
        "Category": ["Groceries"]
    })

    is_valid, messages = validate_pipeline(raw_df, post_expansion_df, final_df)
    assert is_valid is False
    assert any("Amazon expansion balance mismatch" in msg for msg in messages)

def test_call_gemini_categorization_personal_keywords(mock_data_dir, monkeypatch):
    from unittest.mock import MagicMock
    from src.etl import call_gemini_categorization
    import os
    
    reference_dir = mock_data_dir['reference_dir']
    
    mock_client = MagicMock()
    mock_response = MagicMock()
    # Provide a dummy JSON response so the parser doesn't fail
    mock_response.text = '{"TEST MERCHANT": {"category": "Transfer", "flag": "", "suggested_filter": "No", "filter_reason": ""}}'
    mock_client.models.generate_content.return_value = mock_response
    
    # Mock genai.Client
    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client
    monkeypatch.setattr('src.etl.genai', mock_genai)
    
    unique_merchants = ['TEST MERCHANT']
    personal_keywords = ['custom_word_1', 'custom_word_2']
    
    # Clear cache before running to force API call
    cache_path = os.path.join(reference_dir, 'merchant_cache.json')
    if os.path.exists(cache_path):
        os.remove(cache_path)
    
    # Call function
    call_gemini_categorization(unique_merchants, reference_dir, personal_items_profile=[], personal_keywords=personal_keywords)
    
    # Extract the prompt used
    call_args = mock_client.models.generate_content.call_args
    prompt_used = call_args.kwargs['contents']
    
    assert "Personal Filtering Keywords:" in prompt_used
    assert "custom_word_1" in prompt_used
    assert "custom_word_2" in prompt_used

def test_keywords_cache_invalidation(tmp_path):
    ref_dir = str(tmp_path)
    cache_file = tmp_path / "merchant_cache.json"
    cache_file.write_text('{"MERCHANT": {"category": "Groceries"}}')
    
    # Run 1: First set of keywords
    keywords_v1 = ["Art supplies", "Gaming"]
    invalidated = check_and_update_keywords_hash(ref_dir, keywords_v1)
    assert invalidated is False
    assert cache_file.exists()
    
    # Run 2: Same keywords -> cache should NOT be invalidated
    invalidated = check_and_update_keywords_hash(ref_dir, keywords_v1)
    assert invalidated is False
    assert cache_file.exists()
    
    # Run 3: Changed keywords -> cache should be invalidated (file removed)
    keywords_v2 = ["Art supplies", "Gaming", "Video games"]
    invalidated = check_and_update_keywords_hash(ref_dir, keywords_v2)
    assert invalidated is True
    assert not cache_file.exists()

    # Re-create cache for next test
    cache_file.write_text('{"MERCHANT": {"category": "Groceries"}}')
    
    # Run 4: Reordering keywords -> cache should NOT be invalidated (hash should be identical)
    keywords_v3 = ["Gaming", "Video games", "Art supplies"]
    invalidated = check_and_update_keywords_hash(ref_dir, keywords_v3)
    assert invalidated is False
    assert cache_file.exists()

    # Run 5: Removing all keywords -> cache should be invalidated
    keywords_v4 = []
    invalidated = check_and_update_keywords_hash(ref_dir, keywords_v4)
    assert invalidated is True
    assert not cache_file.exists()

