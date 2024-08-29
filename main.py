import os
import shutil
import json
import pandas as pd
from typing import Union
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime


app = FastAPI()

origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1:5500",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


UPLOAD_FOLDER = "uploads"
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def process_payment_file(file_path):
    df = pd.read_excel(file_path)
    return df[["Date", "Vendor Name", "Amount"]].to_dict("records")


@app.post("/validate-file")
async def validate_file(
    payment_file: UploadFile = File(...), json_file: UploadFile = File(...)
):
    try:
        payment_path = os.path.join(UPLOAD_FOLDER, payment_file.filename)
        with open(payment_path, "wb") as buffer:
            shutil.copyfileobj(payment_file.file, buffer)

        print(payment_path)

        json_path = os.path.join(UPLOAD_FOLDER, json_file.filename)
        with open(json_path, "wb") as buffer:
            shutil.copyfileobj(json_file.file, buffer)

        print(json_path)
        # Process payment file
        payment_data = process_payment_file(payment_path)
        print(payment_data)

        # Load JSON data
        with open(json_path) as json_file:
            json_data = json.load(json_file)

        # Compare data
        mismatches = []
        for invoice in json_data["invoices"]:
            matching_payment = None
            mismatch_reasons = []

            for payment in payment_data:
                print(f"Name comparison {payment['Vendor Name']} {invoice['vendorName']}")
                if payment["Vendor Name"] != invoice["vendorName"]:
                    mismatch_reasons.append("Vendor Name")
                    continue

                payment_date = payment["Date"].date()
                invoice_date = pd.to_datetime(invoice["invoiceDate"]).date()
                print(f"Date comparison {payment_date} {invoice_date}")
                if payment_date != invoice_date:
                    mismatch_reasons.append("Date")
                    continue

                payment_amount = float(payment["Amount"])
                invoice_amount = float(invoice["amountPayable"])
                print(f"Amount comparison {payment_amount} {invoice_amount}")
                if payment_amount != invoice_amount:
                    mismatch_reasons.append("Amount")
                    continue
            
                
                print("--------------------------------")

                # If we get here, we've found a match
                matching_payment = payment
                break

            if not matching_payment:
                mismatch_detail = {
                    "vendorName": invoice["vendorName"],
                    "invoiceDate": invoice["invoiceDate"],
                    "amountPayable": invoice["amountPayable"],
                    "reason": "No matching payment found",
                    "mismatchedFields": mismatch_reasons
                }
        mismatches.append(mismatch_detail)
        print("mismatches")
        print(mismatches)

        response = {
            "message": "Files processed successfully",
            "payment_file": payment_file.filename,
            "json_file": json_file.filename,
            "mismatches": mismatches,
        }

        print(response)

        return response
    except Exception as e:
        print(e)
        return {"error": f"An error occurred: {str(e)}"}
