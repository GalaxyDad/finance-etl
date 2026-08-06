import os
import polars as pl
import logging

logger = logging.getLogger(__name__)

class AmazonProcessor:
    def __init__(self, reference_dir: str):
        self.reference_dir = reference_dir
        self.orders_path = os.path.join(reference_dir, "Order History.csv")
        self.refunds_path = os.path.join(reference_dir, "Refund Details.csv")
        self.register_path = os.path.join(reference_dir, "Jenn Mike Finance Tracker - Mike Transaction Register.csv")
        
        self.orders_df = None
        self.refunds_df = None
        self.register_df = None
        self.personal_items_profile = set()
        
        self._load_data()
        self._build_profile()

    def _load_data(self):
        # Load Orders
        if os.path.exists(self.orders_path):
            try:
                df = pl.read_csv(self.orders_path, null_values=["", "Not Available", "Not Applicable"], infer_schema_length=0)
                df = df.rename({col: col.strip() for col in df.columns})
                df = df.with_columns(
                    pl.col("Ship Date").str.strptime(pl.Datetime, strict=False).dt.date(),
                    pl.col("Total Amount").str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False)
                ).drop_nulls(subset=["Ship Date", "Total Amount"])
                self.orders_df = df
            except Exception as e:
                logger.error(f"Failed to load {self.orders_path}: {e}")
                
        # Load Refunds
        if os.path.exists(self.refunds_path):
            try:
                df = pl.read_csv(self.refunds_path, null_values=["", "Not Available", "Not Applicable"], infer_schema_length=0)
                df = df.rename({col: col.strip() for col in df.columns})
                df = df.with_columns(
                    pl.col("Refund Date").str.strptime(pl.Datetime, strict=False).dt.date(),
                    pl.col("Refund Amount").str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False)
                ).drop_nulls(subset=["Refund Date", "Refund Amount"])
                self.refunds_df = df
            except Exception as e:
                logger.error(f"Failed to load {self.refunds_path}: {e}")
                
        # Load Register
        if os.path.exists(self.register_path):
            try:
                df = pl.read_csv(self.register_path, null_values=[""], infer_schema_length=0)
                df = df.rename({col: col.strip() for col in df.columns})
                
                df = df.with_columns(
                    pl.coalesce([
                        pl.col("Date").str.strptime(pl.Date, "%m/%d/%Y", strict=False),
                        pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d", strict=False)
                    ]).alias("Date"),
                    pl.col("Amount").str.replace_all(r'[\$,]', '').cast(pl.Float64, strict=False)
                ).drop_nulls(subset=["Date", "Amount"])
                
                self.register_df = df.filter(pl.col("Transaction Details").str.to_lowercase().str.contains("amazon|amzn"))
            except Exception as e:
                logger.error(f"Failed to load {self.register_path}: {e}")

    def _build_profile(self):
        """Reconcile shipments against register to find personal items (delta)."""
        if self.register_df is None or len(self.register_df) == 0:
            return
            
        register_records = self.register_df.select(["Date", "Amount"]).to_dicts()
        
        if self.orders_df is not None and len(self.orders_df) > 0:
            shipment_totals = self.orders_df.group_by(["Order ID", "Ship Date"]).agg(
                pl.col("Total Amount").sum().alias("Shipment Total"),
                pl.col("Product Name").alias("Products")
            )
            
            for row in shipment_totals.iter_rows(named=True):
                ship_date = row["Ship Date"]
                if ship_date is None:
                    continue
                    
                total = -(row["Shipment Total"])
                products = row["Products"]
                
                matched = False
                for reg in register_records:
                    if reg["Date"] is None or reg["Amount"] is None:
                        continue
                    if abs(reg["Amount"] - total) < 0.01:
                        days_diff = (reg["Date"] - ship_date).days
                        if 0 <= days_diff <= 7:
                            matched = True
                            break
                            
                if not matched:
                    for product in products:
                        if product:
                            self.personal_items_profile.add(product)

    def get_personal_items_profile(self) -> list[str]:
        return list(self.personal_items_profile)

    def process_transactions(self, bank_df: pl.DataFrame) -> pl.DataFrame:
        """
        Takes raw bank transactions. Matches generic Amazon transactions 
        to shipments/refunds and expands them into product-level rows.
        """
        if len(bank_df) == 0:
            return bank_df
            
        if self.orders_df is None and self.refunds_df is None:
            return bank_df
            
        orders_grouped = None
        if self.orders_df is not None and len(self.orders_df) > 0:
            orders_grouped = self.orders_df.group_by(["Order ID", "Ship Date"]).agg(
                pl.col("Total Amount").sum().alias("Shipment Total"),
                pl.col("Product Name"),
                pl.col("Total Amount")
            )
            
        refunds_grouped = None
        if self.refunds_df is not None and len(self.refunds_df) > 0:
            if self.orders_df is not None:
                order_products = self.orders_df.group_by("Order ID").agg(pl.col("Product Name").first())
                refunds_joined = self.refunds_df.join(order_products, on="Order ID", how="left")
            else:
                refunds_joined = self.refunds_df.with_columns(pl.lit("Amazon Refund").alias("Product Name"))
                
            refunds_grouped = refunds_joined.group_by(["Order ID", "Refund Date"]).agg(
                pl.col("Refund Amount").sum().alias("Refund Total"),
                pl.col("Product Name"),
                pl.col("Refund Amount")
            )

        new_rows = []
        used_shipments = set()
        used_refunds = set()
        
        for row in bank_df.iter_rows(named=True):
            merchant = row["Transaction Details"]
            amount = row["Amount"]
            date = row["Date"]
            
            is_amazon = isinstance(merchant, str) and ("amazon" in merchant.lower() or "amzn" in merchant.lower())
            matched = False
            
            if is_amazon and amount < 0 and orders_grouped is not None:
                for ship_row in orders_grouped.iter_rows(named=True):
                    ship_key = (ship_row["Order ID"], ship_row["Ship Date"])
                    if ship_key in used_shipments:
                        continue
                        
                    ship_date = ship_row["Ship Date"]
                    if ship_date is None:
                        continue
                        
                    ship_total = -(ship_row["Shipment Total"])
                    
                    if abs(ship_total - amount) < 0.01:
                        days_diff = (date - ship_date).days
                        if 0 <= days_diff <= 7:
                            for prod_name, prod_amount in zip(ship_row["Product Name"], ship_row["Total Amount"]):
                                new_row = dict(row)
                                new_row["Transaction Details"] = prod_name if prod_name else merchant
                                new_row["Amount"] = -prod_amount
                                new_rows.append(new_row)
                            matched = True
                            used_shipments.add(ship_key)
                            break
                            
            elif is_amazon and amount > 0 and refunds_grouped is not None:
                for ref_row in refunds_grouped.iter_rows(named=True):
                    ref_key = (ref_row["Order ID"], ref_row["Refund Date"])
                    if ref_key in used_refunds:
                        continue
                        
                    ref_date = ref_row["Refund Date"]
                    if ref_date is None:
                        continue
                        
                    ref_total = ref_row["Refund Total"]
                    
                    if abs(ref_total - amount) < 0.01:
                        days_diff = (date - ref_date).days
                        if 0 <= days_diff <= 7:
                            for prod_name, prod_amount in zip(ref_row["Product Name"], ref_row["Refund Amount"]):
                                new_row = dict(row)
                                new_row["Transaction Details"] = f"Refund: {prod_name}" if prod_name else f"Refund: {merchant}"
                                new_row["Amount"] = prod_amount
                                new_rows.append(new_row)
                            matched = True
                            used_refunds.add(ref_key)
                            break
                            
            if not matched:
                new_rows.append(dict(row))
                
        if not new_rows:
            return bank_df
            
        schema = bank_df.schema
        return pl.DataFrame(new_rows, schema=schema)
