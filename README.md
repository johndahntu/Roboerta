# RoboertaAI

RoboertaAI is a Render-ready Python 3 app for loading a weekly grocery ad, parsing it with OpenAI, and comparing either a planner upload or a mobile camera photo against that ad.

## What it does

- Parses uploaded weekly ad PDF or image files into structured ad items.
- Stores the active weekly ad in SQLite and clears the old ad data when a new weekly ad is loaded.
- Accepts either a planner upload or a camera photo, but not both.
- Generates a grouped report with Front Page, Price Lock, Just 4 U, $5 Friday, Member Price, and Regular item matches.
- Keeps reports for three days unless deleted earlier.
- Supports printing and marking matched items as done.

## Local run

1. Create a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Set `OPENAI_API_KEY` in your environment.
4. Start the app with `uvicorn app.main:app --reload`.

## Render

Use the included `render.yaml` and set `OPENAI_API_KEY` in the Render dashboard.