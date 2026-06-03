
import fitz
import base64
import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def analyze_first_page(pdf_path):
    doc = fitz.open(pdf_path)
    page = doc[0]

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    png_bytes = pix.tobytes("png")

    image_base64 = base64.b64encode(png_bytes).decode()

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": """
This is a Safeway ad.

Tell me:
- what products are on the front page
- what appears to be a major promotion

Keep answer short.
"""
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_base64}"
                    }
                ]
            }
        ]
    )

    return response.output_text
