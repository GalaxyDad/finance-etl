import os
import polars as pl
from datetime import date
from src.amazon import AmazonProcessor

def test_amazon_processor(mock_data_dir):
    ref_dir = mock_data_dir['reference_dir']
    
    # Write some mock data for AmazonProcessor
    orders_data = """ASIN,Order ID,Ship Date,Total Amount,Product Name,Original Quantity
123,O-1,2023-10-01,10.00,Item A,1
456,O-1,2023-10-01,15.50,Item B,3
789,O-2,2023-10-15,50.00,Personal Item,1
"""
    with open(os.path.join(ref_dir, "Order History.csv"), 'w') as f:
        f.write(orders_data)
        
    refunds_data = """Order ID,Refund Date,Refund Amount
O-1,2023-10-05,10.00
"""
    with open(os.path.join(ref_dir, "Refund Details.csv"), 'w') as f:
        f.write(refunds_data)
        
    register_data = """Date,Amount,Transaction Details,Category
2023-10-02,25.50,Amazon.ca,Shopping
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
    item_b = result.filter(pl.col("Transaction Details") == "3x Item B")
    assert item_b["Amount"][0] == -15.50
    
    # Check O-2 expansion
    pers_item = result.filter(pl.col("Transaction Details") == "Personal Item")
    assert pers_item["Amount"][0] == -50.00
    
    # Check Refund
    refund = result.filter(pl.col("Transaction Details").str.contains("Refund"))
    assert refund["Amount"][0] == 10.00
    
    # Check unrelated item
    other = result.filter(pl.col("Transaction Details") == "OTHER")
    assert other["Amount"][0] == -100.00
