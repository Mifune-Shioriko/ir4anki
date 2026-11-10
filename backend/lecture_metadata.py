"""Read existing lecture metadata without creating stores, schema or journals."""
import json
import sqlite3
from pathlib import Path

def documents(root):
    path = Path(root) / 'lectures.db'
    if not path.is_file():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute("SELECT namespace,key,value FROM lecture_objects WHERE namespace IN ('documents','revisions')").fetchall()
        revisions = {key: json.loads(value) for namespace, key, value in rows if namespace == 'revisions'}
        return [(doc, revisions.get(doc.get('reading_revision', doc['current_revision'])))
                for namespace, _, value in rows if namespace == 'documents'
                for doc in [json.loads(value)]]
    finally:
        db.close()
