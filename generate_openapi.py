import json
from app.main import app

# This forces the schema to generate even if openapi_url is None
openapi_schema = app.openapi()

# Save it to a file
with open("openapi.json", "w") as f:
    json.dump(openapi_schema, f, indent=2)

print("✅ openapi.json has been updated successfully!")
