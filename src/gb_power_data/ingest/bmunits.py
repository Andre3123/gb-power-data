import sys

from pathlib import Path

import pandas as pd
from elexon_bmrs import BMRSClient

RAW_DIR=Path("data/raw")

def fetch_bmunits() -> pd.DataFrame:
    client = BMRSClient()
    records= client.get_reference_bmunits_all()
    rows=[r.model_dump() if hasattr(r,"model_dump") else r for r in records]
    df=pd.DataFrame(rows)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RAW_DIR/"bmunits_reference.parquet", index=False)
    return df 

def search(df: pd.DataFrame, keywords: list[str]) ->pd.DataFrame:
    text = df.fillna("").astype(str).agg(" | ".join, axis=1).str.lower()
    mask=pd.Series(False, index=df.index)
    for kw in keywords:
        mask |= text.str.contains(kw.lower(), regex =False)
    return df[mask]

KEY_COLUMNS = {
    "elexonBmUnit": "BMU ID",
    "bmUnitName": "Name",
    "leadPartyName": "Lead party",
    "generationCapacity": "Export capacity (MW)",
    "demandCapacity": "Import capacity (MW)",
    "gspGroupName": "Region (GSP group)",
    "bmUnitType": "Type (E = embedded, T = transmission)",
    "fpnFlag": "Submits physical notifications",
}


def main() -> None:
    keywords = sys.argv[1:] or ["pillswood", "harmony", "creyke"]
    df = fetch_bmunits()
    hits = search(df, keywords)
    print(f"\n{len(df)} BM units in Elexon's list; {len(hits)} match {keywords}\n")
    if hits.empty:
        return
    view = hits[list(KEY_COLUMNS)].rename(columns=KEY_COLUMNS)
    print(view.set_index("BMU ID").T.to_string())

