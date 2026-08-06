import polars as pl

new_rows = [
    {"id": 1, "val": "A"},
    {"id": 2, "val": "B", "extra": "Yes"}
]
schema = {"id": pl.Int64, "val": pl.Utf8, "extra": pl.Utf8}
df = pl.DataFrame(new_rows, schema=schema)
print(df)
