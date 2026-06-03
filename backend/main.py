
from fastapi import FastAPI, UploadFile, File
import os

from flyer_parser import analyze_first_page

app = FastAPI(title="Roboerta")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

@app.get("/")
def root():
    return {"app":"Roboerta","status":"running"}

@app.post("/parse-flyer")
async def parse_flyer(file: UploadFile = File(...)):
    flyer_path = os.path.join(DATA_DIR, file.filename)

    with open(flyer_path, "wb") as f:
        f.write(await file.read())

    result = analyze_first_page(flyer_path)

    return {
        "success": True,
        "analysis": result
    }
