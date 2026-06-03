
from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
import json, os, uuid

app = FastAPI(title="Roboerta")
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

@app.get("/")
def root():
    return {"app":"Roboerta","status":"running"}

@app.post("/upload-flyer")
async def upload_flyer(file: UploadFile = File(...)):
    flyer_path = os.path.join(DATA_DIR, f"{uuid.uuid4()}_{file.filename}")
    with open(flyer_path, "wb") as f:
        f.write(await file.read())

    sample = {
      "items":[
        {"product":"Persil Laundry Detergent","price":8.99,"offer_type":"just_for_u"},
        {"product":"Open Nature Water Crackers","price":3.99,"offer_type":"price_lock"}
      ]
    }

    with open(os.path.join(DATA_DIR,"weekly_ad.json"),"w") as f:
        json.dump(sample,f,indent=2)

    return {"message":"flyer uploaded","items_parsed":2}

@app.post("/scan-display")
async def scan_display(file: UploadFile = File(...)):
    try:
        with open(os.path.join(DATA_DIR,"weekly_ad.json")) as f:
            return json.load(f)
    except:
        return {"error":"No flyer loaded"}

@app.get("/ad-items")
def ad_items():
    try:
        with open(os.path.join(DATA_DIR,"weekly_ad.json")) as f:
            return json.load(f)
    except:
        return {"items":[]}
