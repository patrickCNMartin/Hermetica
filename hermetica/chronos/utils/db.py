# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
from hermetica.chronos.utils.request_utils import get_protocol_list

def initialize_db(db:str, base_url:str, headers:str):
    # We initialize an empty data base
    # Should we create a back up of the initial pull?  
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protocol_versions (
            hash TEXT PRIMARY KEY,
            protocol_id TEXT NOT NULL,
            protocol_guid TEXT NOT NULL,
            protocol TEXT NOT NULL,
            valid_from DATE NOT NULL,
            deprecated_at DATE
        )
    """)
    conn.commit()
    # Next we add the original first batch of data.
    
    return 0