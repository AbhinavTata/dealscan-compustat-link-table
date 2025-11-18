import pandas as pd
import numpy as np
import pycountry
import json
from concurrent.futures import ThreadPoolExecutor
from time import time
import threading
from rapidfuzz import fuzz


def country_code(country_name):
    """Convert country name to ISO alpha-3 code."""
    try:
        code = pycountry.countries.lookup(country_name).alpha_3
        return code.strip().lower()
    except LookupError:
        return None


def fuzzy_string_match(str1, str2):
    """
    Perform fuzzy matching between two strings word by word.
    Returns True if all corresponding words have similarity >= 80%.
    """
    words1 = str1.strip().split()
    words2 = str2.strip().split()
    
    if len(words1) != len(words2):
        return False
    
    for w1, w2 in zip(words1, words2):
        if fuzz.ratio(w1, w2) < 80:
            return False
    
    return True


def get_borrower_id(dealscan_ids, dealscan):
    """Extract borrower information for matching dealscan IDs."""
    ids = []
    companies = []
    for ID in dealscan_ids:
        record = dealscan[dealscan["ID"] == ID].iloc[0]
        ids.append(record['Borrower_Id'])
        companies.append({
            "ID": ID,
            "Borrower ID": record['Borrower_Id'],
            "Borrower Name": record['Borrower_Name'],
            "Additional Borrowers": record['Additional_Borrowers'],
            "Parent Name": record['Parent'],
            "Parent Ticker": record['Parent_Ticker'],
            "Ticker": record['Ticker'],
            "City": record['City'],
            "Country": record['Country'],
        })
    return companies


def find_matching_dealscan_ids(comp, ticker_dict, name_dict, dealscan):
    """
    Find matching Dealscan IDs for a given Compustat record based on ticker,
    country, and fuzzy matching on company name.
    """
    comp_tic = str(comp['TIC']).strip().lower()
    comp_conml = str(comp['CONML']).strip().lower()
    comp_conm = str(comp['CONM']).strip().lower()

    # first try ticker matching
    ticker_match_ids = [k for k, v in ticker_dict.items() if v == comp_tic]
    if ticker_match_ids:
        final_ticker_ids = []
        for ID in ticker_match_ids:
            country = dealscan[dealscan["ID"] == ID]['Country'].iloc[0]
            if country_code(country) == str(comp['LOC']).strip().lower():
                final_ticker_ids.append(ID)
        if final_ticker_ids:
            return get_borrower_id(final_ticker_ids, dealscan)

    # fall back to name matching
    name_match_ids = [k for k, v in name_dict.items() if 
                      fuzzy_string_match(comp_conml, v) or 
                      fuzzy_string_match(comp_conm, v)]
    if name_match_ids:
        return get_borrower_id(name_match_ids, dealscan)
    
    return None


def process_compustat_record(comp_tuple, ticker_dict, name_dict, dealscan, match_dict, lock):
    """Process a single Compustat record and update match dictionary."""
    _, comp = comp_tuple
    deal_ids = find_matching_dealscan_ids(comp, ticker_dict, name_dict, dealscan)
    if deal_ids:
        with lock:
            match_dict[comp['GVKEY']] = deal_ids
    return None


def main():
    print("Loading data...")
    
    # Load and preprocess dealscan data
    dealscan = pd.read_csv("../data/dealscan.csv")
    dealscan.fillna(0, inplace=True)
    dealscan['Tranche_Maturity_Date'] = pd.to_datetime(
        dealscan['Tranche_Maturity_Date'], format='%d/%m/%Y', errors='coerce'
    ).dt.date
    dealscan['Tranche_Active_Date'] = pd.to_datetime(
        dealscan['Tranche_Active_Date'], format='%d/%m/%Y'
    )
    dealscan['Deal_Input_Date'] = pd.to_datetime(
        dealscan['Deal_Input_Date'], format='%d/%m/%Y'
    ).dt.date
    dealscan['Deal_Active_Date'] = pd.to_datetime(
        dealscan['Deal_Active_Date'], format='%d/%m/%Y'
    ).dt.date
    
    # create unique ID column
    dealscan['ID'] = (
        dealscan['Lender_Name'].astype(str) + "_" +
        dealscan['LPC_Tranche_ID'].astype(str) + "_" +
        dealscan['Tranche_Active_Date'].astype(str)
    )
    
    print(f"Duplicate IDs check (should be 0): {dealscan['ID'].duplicated().sum()}")
    print(f"Tranche_Active_Date dtype: {dealscan['Tranche_Active_Date'].dtype}")
    
    dealscan['Year'] = dealscan['Tranche_Active_Date'].astype(str).str[:4].astype(int)

    # load compustat data
    compustat = pd.read_csv("../data/compustat.csv")
    
    # precompute dictionaries for faster lookups
    print("Building lookup dictionaries...")
    name_dict = {k: v.strip().lower() for k, v in zip(dealscan['ID'], dealscan['Borrower_Name'])}
    ticker_dict = {k: str(v).strip().lower() for k, v in zip(dealscan['ID'], dealscan['Ticker'])}
    
    # thread-safe matching
    match_dict = {}
    lock = threading.Lock()
    
    print("Starting parallel matching process...")
    start_time = time()
    
    with ThreadPoolExecutor() as executor:
        list(executor.map(
            lambda comp_tuple: process_compustat_record(
                comp_tuple, ticker_dict, name_dict, dealscan, match_dict, lock
            ),
            compustat.iterrows()
        ))
    
    total_time = time() - start_time
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Average time per record: {total_time / len(compustat):.4f} seconds")
    print(f"Total matches found: {len(match_dict)}")
    
    # create borrower_id_dict
    borrower_id_dict = {
        key: list(set(item['Borrower ID'] for item in value)) 
        for key, value in match_dict.items()
    }
    
    # identify duplicates for manual review
    manual_check = {k: v for k, v in borrower_id_dict.items() if len(v) > 1}
    print(f"Records requiring manual review: {len(manual_check)}")
    
    # apply manual review results
    print("Applying manual review corrections...")
    duplicates = pd.read_csv("../data/duplicates.csv")
    duplicates.fillna(0, inplace=True)
    
    for _, row in duplicates.iterrows():
        if row['Match'] == -1:
            borrower_id_dict.pop(row['GVKEY'], None)
        elif row['Match'] != 0:
            borrower_id_dict[row['GVKEY']] = row['Match']
    
    # clean up single-element lists
    for k, v in borrower_id_dict.items():
        if isinstance(v, list) and len(v) == 1:
            borrower_id_dict[k] = v[0]
    
    # convert to ints
    borrower_id_dict = {
        int(k): int(v) if isinstance(v, (int, np.integer)) else [int(i) for i in v]
        for k, v in borrower_id_dict.items()
    }
    
    # save to JSON
    output_file = "borrower_id_dict.json"
    with open(output_file, 'w') as f:
        json.dump(borrower_id_dict, f, indent=2)
    
    print(f"\nSuccess! Saved {len(borrower_id_dict)} matches to {output_file}")


if __name__ == "__main__":
    main()