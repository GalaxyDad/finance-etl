import os
from src.etl import process_bank_data, call_gemini_categorization, format_output

def main():
    raw_dir = "data/raw"
    reference_dir = "data/reference"
    processed_dir = "data/processed"
    
    print("Processing bank data...")
    df = process_bank_data(raw_dir)
    
    if len(df) == 0:
        print("No raw data found.")
        return
        
    unique_merchants = df['Transaction Details'].unique().to_list()
    print(f"Found {len(unique_merchants)} unique merchants. Calling Gemini for categorization...")
    
    mapping = call_gemini_categorization(unique_merchants, reference_dir)
    
    print("Formatting output...")
    output_df = format_output(df, mapping)
    
    output_path = os.path.join(processed_dir, "consolidated_ledger.csv")
    output_df.write_csv(output_path)
    print(f"Success! Exported {len(output_df)} rows to {output_path}")

if __name__ == "__main__":
    main()
