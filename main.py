import uvicorn

import os
import shutil
import json
import pandas as pd
from typing import Union
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import re
from thefuzz import fuzz

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_similarity_score(str1: str, str2: str) -> float:
    str1_cleaned = re.sub(r"\W+", "", str1).lower()
    str2_cleaned = re.sub(r"\W+", "", str2).lower()

    similarity_ratio = fuzz.ratio(str1_cleaned, str2_cleaned) / 100.0
    partial_ratio = fuzz.partial_ratio(str1_cleaned, str2_cleaned) / 100.0
    token_set_ratio = fuzz.token_set_ratio(str1, str2) / 100.0

    significant_tokens_1 = set(re.findall(r"\b\w+\b", str1.lower()))
    significant_tokens_2 = set(re.findall(r"\b\w+\b", str2.lower()))
    common_tokens = significant_tokens_1.intersection(significant_tokens_2)

    if common_tokens and (
        len(common_tokens)
        >= min(len(significant_tokens_1), len(significant_tokens_2)) * 0.75
    ):
        boosted_score = 0.9
        combined_score = max(
            (similarity_ratio * 0.3) + (partial_ratio * 0.3) + (token_set_ratio * 0.4),
            boosted_score,
        )
    else:
        combined_score = (
            (similarity_ratio * 0.3) + (partial_ratio * 0.3) + (token_set_ratio * 0.4)
        )

    return combined_score


@app.post("/validate-file")
async def validate_file(
    payment_file: UploadFile = File(...), json_file: UploadFile = File(...)
):
    try:
        payment_path = os.path.join(UPLOAD_FOLDER, payment_file.filename)
        with open(payment_path, "wb") as buffer:
            shutil.copyfileobj(payment_file.file, buffer)

        json_path = os.path.join(UPLOAD_FOLDER, json_file.filename)
        with open(json_path, "wb") as buffer:
            shutil.copyfileobj(json_file.file, buffer)

        with open(json_path) as file:
            json_data = json.load(file)
        invoice_data = pd.json_normalize(json_data["invoices"])
        invoice_data["invoiceDate"] = pd.to_datetime(
            invoice_data["invoiceDate"], dayfirst=True
        ).dt.date
        invoice_data["amountPayable"] = invoice_data["amountPayable"].astype(float)
        invoice_data["vendorName"] = invoice_data["vendorName"].str.casefold()

        payment_data = pd.read_excel(payment_path)
        payment_data.rename(
            columns={
                "Vendor Name": "vendorName",
                "Invoice ID": "invoiceId",
                "Date": "invoiceDate",
                "Amount": "amountPayable",
            },
            inplace=True,
        )
        payment_data["invoiceDate"] = pd.to_datetime(
            payment_data["invoiceDate"], dayfirst=True
        ).dt.date
        payment_data["amountPayable"] = payment_data["amountPayable"].astype(float)
        payment_data["vendorName"] = payment_data["vendorName"].str.casefold()

        similarity_threshold = 0.8
        potential_matches = []

        for _, inv_row in invoice_data.iterrows():
            for _, pay_row in payment_data.iterrows():
                if (
                    inv_row["amountPayable"] == pay_row["amountPayable"]
                    and inv_row["invoiceDate"] == pay_row["invoiceDate"]
                ):
                    similarity_score = get_similarity_score(
                        inv_row["vendorName"], pay_row["vendorName"]
                    )
                    if similarity_score >= similarity_threshold:
                        combined_row = {
                            **inv_row.to_dict(),
                            **pay_row.to_dict(),
                            "similarityScore": similarity_score,
                        }
                        potential_matches.append(combined_row)

        potential_matches_df = pd.DataFrame(potential_matches)
        columns_to_check = ["invoiceId", "invoiceDate", "amountPayable", "vendorName"]

        # Remove duplicates in potential_matches_df based on the columns
        potential_matches_df = potential_matches_df.drop_duplicates(
            subset=columns_to_check
        )
        potential_matches_df = potential_matches_df.sort_values(
            by="similarityScore", ascending=False
        )

        # ?
        merged_data_with_similarity = pd.merge(
            invoice_data,
            payment_data,
            how="outer",
            on=["invoiceDate", "amountPayable", "vendorName"],
            indicator="mismatch_indicator",
        )
        matched_keys = potential_matches_df[columns_to_check].drop_duplicates()
        merged_data_with_similarity = pd.merge(
            merged_data_with_similarity,
            matched_keys,
            on=columns_to_check,
            how="left",
            indicator="potential_match_indicator",
        )

        no_match_conditions = (
            merged_data_with_similarity["mismatch_indicator"] != "both"
        ) & (merged_data_with_similarity["potential_match_indicator"] == "left_only")
        no_matches = merged_data_with_similarity[no_match_conditions]
        no_matches = no_matches.drop(columns=["potential_match_indicator"])

        certain_matches = potential_matches_df[
            potential_matches_df["similarityScore"] == 1.0
        ]
        potentially_matched = potential_matches_df[
            (potential_matches_df["similarityScore"] < 1.0)
            & (potential_matches_df["similarityScore"] >= similarity_threshold)
        ]

        cleaned_no_matches = no_matches[~no_matches['filehash'].isin(potential_matches_df['filehash'])]
        certain_matches_list = [
            {
                "invoiceId": row["invoiceId"],
                "hash": row["filehash"] if pd.notna(row["filehash"]) else "",
            }
            for _, row in certain_matches.iterrows()
        ]
        potentially_matched_list = [
            {
                "invoiceId": row["invoiceId"],
                "hash": row["filehash"] if pd.notna(row["filehash"]) else "",
                "confidence": int(row["similarityScore"] * 100),
                "warning": "Vendor name is not an exact match.",
            }
            for _, row in potentially_matched.iterrows()
        ]
        unmatched_list = [
            {
                "invoiceId": row["invoiceId"] if pd.notna(row["invoiceId"]) else "",
                "hash": row["filehash"] if pd.notna(row["filehash"]) else "",
                "vendorName": row["vendorName"],
            }
            for _, row in cleaned_no_matches.iterrows()
        ]

        output = {
            "certainMatch": certain_matches_list,
            "potentiallyMatched": potentially_matched_list,
            "unmatched": unmatched_list,
        }
        
        return output
    except Exception as e:
        return {"error": f"An error occurred: {str(e)}"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)