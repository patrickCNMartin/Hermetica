# -----------------------------------------------------------------------------#
# IMPORT LIBS
# -----------------------------------------------------------------------------#
import sqlite3
import json

# -----------------------------------------------------------------------------#
# FUNCS
# -----------------------------------------------------------------------------#
def generate_protocol_lock(protocols:list, db_version:str, db:str):
    """Generate a lock file from a list of protocols and a database connection.
        Note that this version will only generate a lock file for the used
        protocols. The actual pipeline/workflow will be handled in a
        different set of functions.
    """
    # I think I need to add a protocol version hash. 
    # The main reason is to have a clean mechanism to pull the correct database
    # version. Pulling by date is great at the user level but in the backgroud
    # it might not be ideal. What if I have to make changes outside of 
    # cron cycles? Same date different databases. 
    db = sqlite3.connect(db)
    dated_db = db.execute(
        "SELECT "
    )
    return 0