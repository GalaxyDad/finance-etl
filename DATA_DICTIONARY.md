# Data Dictionary

This document defines the expected schema for all input (raw and reference) and output (processed) files in the `finance-etl` pipeline. 

> [!IMPORTANT]  
> **Evergreen Policy**: This Data Dictionary must be kept evergreen. Any changes to file formats, new bank parsers, or modifications to the final output schema must be updated in this document to reflect the current state of the system.

## 1. Input Data (`data/raw/`)

These are the raw bank statements parsed by the ETL.

### 1.1 Rogers Credit Card (`rogers_transactions*.csv`)
* **Format**: CSV
* **Key Columns**:
  * `Date`: Date of transaction (YYYY-MM-DD, MM/DD/YYYY, or YYYY/MM/DD).
  * `Transaction Details` (or `Merchant Name`): Description of the transaction.
  * `Amount`: Transaction amount (negative for purchases, positive for payments).
  * `Reference Number`: Unique transaction ID (used for deduplication).

### 1.2 Simplii Bank (`simplii*.csv`)
* **Format**: CSV
* **Key Columns**:
  * `Date`: Date of transaction.
  * `Transaction Details`: Description of the transaction.
  * `Funds Out`: Amount withdrawn/spent (float).
  * `Funds In`: Amount deposited (float).

### 1.3 CIBC Bank (`cibc*.csv`)
* **Format**: CSV, no header
* **Key Columns** (mapped internally):
  * `Column 1` -> `Date`: Date of transaction.
  * `Column 2` -> `Transaction Details`: Description.
  * `Column 3` -> `Debit`: Amount withdrawn/spent.
  * `Column 4` -> `Credit`: Amount deposited.
  * `Column 5` -> `Card`: Masked card number.

### 1.4 Wealthsimple Activities (`Wallet*.csv`)
* **Format**: CSV
* **Key Columns**:
  * `date`: Date of transaction.
  * `description`: Description of transaction.
  * `amount`: Transaction amount.

### 1.5 Wealthsimple Credit (`Wealthsimple-credit-card*.csv`)
* **Format**: CSV
* **Key Columns**:
  * `transaction_date`: Date of transaction.
  * `details` or `type`: Description of transaction.
  * `amount`: Transaction amount.

## 2. Reference Data (`data/reference/`)

These files provide taxonomy, historical context, and rules for LLM categorization.

### 2.1 Allowed Categories (`Jenn Mike Finance Tracker - Categories.csv`)
* **Format**: CSV
* **Description**: Master list of allowed categories for LLM mapping.
* **Key Columns**: Contains categories in the 3rd column (index 2, skipping 1 row of headers).

### 2.2 Historical Register (`Jenn Mike Finance Tracker - Mike Transaction Register.csv`)
* **Format**: CSV
* **Description**: Past categorized transactions. Used for few-shot prompting and personal item profile building.
* **Key Columns**:
  * `Date`: Date of the transaction.
  * `Transaction Details`: Merchant name or item description.
  * `Category`: Assigned category.
  * `Amount`: Transaction amount.

### 2.3 Amazon Orders (`Order History.csv`)
* **Format**: CSV
* **Description**: Export of Amazon order history used for line-item expansion.
* **Key Columns**: 
  * `Order ID`: Amazon order identifier.
  * `Ship Date`: Shipment date (used to match bank charges within a -3 to +7 day window).
  * `Carrier Name & Tracking Number`: Shipment grouping key.
  * `Total Amount`: Total cost of the shipment.
  * `Product Name`: Name of the individual item.
  * `Original Quantity`: Number of items purchased.

### 2.4 Amazon Refunds (`Refund Details.csv`)
* **Format**: CSV
* **Description**: Export of Amazon returns/refunds.
* **Key Columns**:
  * `Order ID`: Amazon order identifier.
  * `Refund Date`: Date refund was issued.
  * `Refund Amount`: Amount refunded.

### 2.5 Text Rule Files
* `personal_soft_filter_keywords.txt`: List of custom keywords/semantic concepts to soft-filter as personal items.
* `personal_soft_filter_exclusions.txt`: List of concepts to explicitly exclude from being soft-filtered.
* `category_hints.txt`: Custom instructions to override or steer LLM categorizations.

## 3. Output Data (`data/processed/`)

### 3.1 Consolidated Ledger (`consolidated_ledger_*.csv`)
* **Format**: CSV
* **Description**: The final, categorized, and consolidated dataset designed for the Google Sheet register.
* **Strict Schema** (Must remain in this exact order):
  1. `Year Month`: (Always Blank).
  2. `Date`: YYYY-MM-DD.
  3. `Transaction Details`: Cleaned merchant name or expanded product name.
  4. `Amount`: Float (Income is negative, Expense is positive).
  5. `Category`: LLM determined based on Categories.csv.
  6. `Account`: Source account name.
  7. `Note`: (Blank or populated with details like "Amazon").
  8. `Reporting Category`: (Always Blank).
  9. `Categorization Explanation`: Brief explanation (10 words or less) from Gemini.
  10. `LLM Categorization Flag`: Brief explanation if categorization was difficult.
  11. `Suggested Filter`: "Yes" or "No".
  12. `Filter Reason`: Context if Suggested Filter is "Yes".
  13. `Additional Row Notes`: (Always Blank).
