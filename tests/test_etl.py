import os
import polars as pl
from src.etl import process_bank_data, call_gemini_categorization, format_output

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
            'COSTCO WHOLESALE': {'category': 'Food & Dining: Groceries', 'flag': 'Unsure about bulk store', 'suggested_filter': 'No', 'filter_reason': ''}
        }
    
    # 1. Process Bank Data
    df = process_bank_data(raw_dir)
    
    assert len(df) == 6
    assert df.schema['Date'] == pl.Date
    
    # Verify amounts
    costco = df.filter(pl.col('Transaction Details') == 'COSTCO WHOLESALE')
    assert costco['Amount'][0] == -113.60
    
    payment = df.filter(pl.col('Transaction Details') == 'PAYMENT THANK YOU')
    assert payment['Amount'][0] == 200.00
    
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
