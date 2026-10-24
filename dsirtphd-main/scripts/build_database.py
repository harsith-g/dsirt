import csv
import json
import re

# Read candidate list
with open("data/candidates.json", "r", encoding="utf-8") as f:
    candidates = json.load(f)

print(f"Loaded {len(candidates)} candidates.")
