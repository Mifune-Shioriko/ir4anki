"""Lecture state primitives. Never opens an Anki collection or scans corpus."""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

class Conflict(Exception): pass

def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def identifier(value):
 if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32,64}',value):raise ValueError('非法身份标识')
 return value

def atomic(path, data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(prefix='.',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(name,path)
  fd=os.open(path.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(name):os.unlink(name)

class Store:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
  with self.db() as db:db.execute('CREATE TABLE IF NOT EXISTS lecture_objects (namespace TEXT,key TEXT,value TEXT, PRIMARY KEY(namespace,key))')
 @contextmanager
 def db(self):
  db=sqlite3.connect(self.root/'lectures.db',timeout=30);db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA foreign_keys=ON')
  try:
   db.execute('BEGIN IMMEDIATE')
   yield db
   db.commit()
  except BaseException:db.rollback();raise
  finally:db.close()
 def get(self,ns,key,db=None):
  if db is None:
   with self.db() as conn:return self.get(ns,key,conn)
  row=db.execute('SELECT value FROM lecture_objects WHERE namespace=? AND key=?',(ns,key)).fetchone()
  return json.loads(row[0]) if row else None
 def put(self,ns,key,value,db=None):
  if db is None:
   with self.db() as conn:return self.put(ns,key,value,conn)
  db.execute('INSERT INTO lecture_objects VALUES(?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value',(ns,key,canonical(value)))
 def all(self,ns,db=None):
  if db is None:
   with self.db() as conn:return self.all(ns,conn)
  return [json.loads(row[0]) for row in db.execute('SELECT value FROM lecture_objects WHERE namespace=? ORDER BY key',(ns,))]
