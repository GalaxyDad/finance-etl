import pytest
import os
import tempfile
import csv

@pytest.fixture
def mock_data_dir():
    with tempfile.TemporaryDirectory() as tmpdirname:
        raw_dir = os.path.join(tmpdirname, 'raw')
        processed_dir = os.path.join(tmpdirname, 'processed')
        reference_dir = os.path.join(tmpdirname, 'reference')
        os.makedirs(raw_dir)
        os.makedirs(processed_dir)
        os.makedirs(reference_dir)
        
        # Mock Rogers CC Data
        rogers_path = os.path.join(raw_dir, 'rogers_cc.csv')
        with open(rogers_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Transaction Date', 'Details', 'Amount', 'Rewards'])
            writer.writerow(['2023-10-01', '2023-10-01', 'WALMART SUPERCENTER', '45.67', '0.45'])
            writer.writerow(['2023-10-05', '2023-10-04', 'PAYMENT - THANK YOU', '-100.00', '0.00'])

        # Mock Simplii Data
        simplii_path = os.path.join(raw_dir, 'simplii.csv')
        with open(simplii_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Transaction Details', 'Funds Out', 'Funds In'])
            writer.writerow(['10/02/2023', 'MCDONALDS RESTAURANT', '12.50', ''])
            writer.writerow(['10/15/2023', 'PAYROLL DEPOSIT', '', '2500.00'])
            
        # Mock CIBC Data (No header)
        cibc_path = os.path.join(raw_dir, 'cibc.csv')
        with open(cibc_path, 'w', newline='') as f:
            writer = csv.writer(f)
            # Date, Description, Debit, Credit, Card Number
            writer.writerow(['2023-10-20', 'PAYMENT THANK YOU', '', '200.00', '5223********3915'])
            writer.writerow(['2023-10-21', 'COSTCO WHOLESALE', '113.60', '', '5223********3915'])

        # Mock Categories
        categories_path = os.path.join(reference_dir, 'Jenn Mike Finance Tracker - Categories.csv')
        with open(categories_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Category', 'Jenn Mike Category', 'Unnamed: 2', 'Unnamed: 3', 'Period'])
            writer.writerow(['Bonus', '_Income', '_Income: Bonus', '', '2024-01'])
            writer.writerow(['Groceries', 'Food & Dining', 'Food & Dining: Groceries', '', '2024-01'])
            writer.writerow(['Fast Food', 'Food & Dining', 'Food & Dining: Fast Food', '', '2024-01'])
            
        # Mock Transaction Register
        register_path = os.path.join(reference_dir, 'Jenn Mike Finance Tracker - Mike Transaction Register.csv')
        with open(register_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Year Month', 'Date', 'Transaction Details', 'Amount', 'Category', 'Account', 'Note', 'Reporting Category'])
            writer.writerow(['2024-01', '1/3/2024', 'MCDONALDS RESTAURANT', '51.89', 'Food & Dining: Fast Food', 'Tangerince CC', '', 'Food & Dining'])

        yield {
            'base_dir': tmpdirname,
            'raw_dir': raw_dir,
            'processed_dir': processed_dir,
            'reference_dir': reference_dir
        }
