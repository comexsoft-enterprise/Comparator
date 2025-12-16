import pandas as pd

EXECUTION_ID = "000000000000000002"

# Read the CSV file
df = pd.read_csv(f'results/{EXECUTION_ID}/report.csv', delimiter=";")

# Write to Excel file
df.to_excel(f'results/{EXECUTION_ID}/report.xlsx', index=False)

print("CSV has been successfully converted to Excel!")
