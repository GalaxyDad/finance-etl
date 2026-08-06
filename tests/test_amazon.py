import os
import polars as pl
from datetime import date
from src.amazon import AmazonProcessor

def test_amazon_processor(mock_data_dir):
    ref_dir = mock_data_dir['reference_dir']
    
    # Write some mock data for AmazonProcessor
    orders_data = """ASIN,Order ID,Ship Date,Total Amount,Product Name,Original Quantity,Carrier Name & Tracking Number
123,O-1,2023-10-01,10.00,Item A,1,TBA123
456,O-1,2023-10-01,15.50,Item B,3,TBA123
789,O-2,2023-10-15,50.00,Personal Item,1,TBA456
"""
    with open(os.path.join(ref_dir, "Order History.csv"), 'w') as f:
        f.write(orders_data)
        
    refunds_data = """Order ID,Refund Date,Refund Amount
O-1,2023-10-05,10.00
"""
    with open(os.path.join(ref_dir, "Refund Details.csv"), 'w') as f:
        f.write(refunds_data)
        
    register_data = """Date,Amount,Transaction Details,Category
2023-10-02,-25.50,Amazon.ca,Shopping
"""
    with open(os.path.join(ref_dir, "Jenn Mike Finance Tracker - Mike Transaction Register.csv"), 'w') as f:
        f.write(register_data)

    processor = AmazonProcessor(ref_dir)
    
    # O-1 total is 25.50 on 2023-10-01. Register has 25.50 on 2023-10-02. Match = Shared.
    # O-2 total is 50.00 on 2023-10-15. Register doesn't have it. No match = Personal.
    
    profile = processor.get_personal_items_profile()
    assert "Personal Item" in profile
    assert "Item A" not in profile
    assert "Item B" not in profile
    
    # Test transaction expansion
    bank_df = pl.DataFrame({
        "Date": [date(2023, 9, 30), date(2023, 10, 16), date(2023, 10, 6), date(2023, 10, 20)],
        "Transaction Details": ["AMAZON.CA", "AMAZON", "AMZN REFUND", "OTHER"],
        "Amount": [-25.50, -50.00, 10.00, -100.00],
        "Account": ["Credit", "Credit", "Credit", "Debit"]
    })
    
    result = processor.process_transactions(bank_df)
    
    assert len(result) == 5 # O-1 expanded to 2 items, O-2 is 1 item, Refund is 1 item, OTHER is 1 item
    
    # Check O-1 expansion
    item_a = result.filter(pl.col("Transaction Details") == "Item A")
    assert item_a["Amount"][0] == -10.00
    assert item_a["Note"][0] == "Amazon"
    assert item_a["Suggested Filter"][0] == "Yes"
    assert item_a["Filter Reason"][0] == "Refunded Item"

    item_b = result.filter(pl.col("Transaction Details") == "3x Item B")
    assert item_b["Amount"][0] == -15.50
    assert item_b["Note"][0] == "Amazon"
    assert item_b["Suggested Filter"][0] is None
    
    # Check O-2 expansion
    pers_item = result.filter(pl.col("Transaction Details") == "Personal Item")
    assert pers_item["Amount"][0] == -50.00
    assert pers_item["Note"][0] == "Amazon"
    
    # Check Refund
    refund = result.filter(pl.col("Transaction Details").str.contains("Refund"))
    assert refund["Amount"][0] == 10.00
    assert refund["Note"][0] == "Amazon Refund"
    assert refund["Suggested Filter"][0] == "Yes"
    assert refund["Filter Reason"][0] == "Refunded Item"
    
    # Check unrelated item
    other = result.filter(pl.col("Transaction Details") == "OTHER")
    assert other["Amount"][0] == -100.00
    assert other["Note"][0] == ""


def test_amazon_processor_date_parsing_fallbacks(mock_data_dir):
    ref_dir = mock_data_dir['reference_dir']
    
    # Orders with MM/DD/YYYY and YYYY/MM/DD formats
    orders_data = """ASIN,Order ID,Ship Date,Total Amount,Product Name,Original Quantity,Carrier Name & Tracking Number
123,O-10,10/15/2023,29.99,Slash Item 1,1,TBA789
456,O-11,2023/10/20,49.99,Slash Item 2,1,TBA999
"""
    with open(os.path.join(ref_dir, "Order History.csv"), 'w') as f:
        f.write(orders_data)
        
    # Refunds with MM/DD/YYYY format
    refunds_data = """Order ID,Refund Date,Refund Amount
O-10,10/18/2023,29.99
"""
    with open(os.path.join(ref_dir, "Refund Details.csv"), 'w') as f:
        f.write(refunds_data)

    processor = AmazonProcessor(ref_dir)
    
    assert processor.orders_df is not None
    assert len(processor.orders_df) == 2
    assert processor.orders_df["Ship Date"][0] == date(2023, 10, 15)
    assert processor.orders_df["Ship Date"][1] == date(2023, 10, 20)
    
    assert processor.refunds_df is not None
    assert len(processor.refunds_df) == 1
    assert processor.refunds_df["Refund Date"][0] == date(2023, 10, 18)


def test_amazon_processor_deterministic_profile(mock_data_dir):
    ref_dir = mock_data_dir['reference_dir']
    processor = AmazonProcessor(ref_dir)
    
    # Add 60 unordered items to personal_items_profile
    items = [f"Item {i:02d}" for i in range(60, 0, -1)] # Reverse order
    processor.personal_items_profile = set(items)
    
    profile = processor.get_personal_items_profile()
    assert len(profile) == 50
    assert profile == sorted(profile)
    assert profile[0] == "Item 01"
    assert profile[49] == "Item 50"

