"""Append-only primary-paper review decisions; imported records remain intact."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from .db import connect,dumps

def evidence_digest(row):
    return hashlib.sha256(json.dumps(dict(row),sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def ensure_review_schema(db):
    with connect(db) as con:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='usable_evidence'").fetchone():return
        schema=Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
        con.executescript(schema[schema.index('CREATE TABLE IF NOT EXISTS evidence_reviews'):])

def apply_reviews(path,db):
    pack=json.loads(Path(path).read_text(encoding='utf-8'))
    if pack.get('format')!='ec-evidence-review-v1':raise ValueError('Unknown review pack format')
    ensure_review_schema(db);added=0
    with connect(db) as con:
        for r in pack['reviews']:
            original=con.execute('SELECT * FROM evidence WHERE id=?',(r['evidence_id'],)).fetchone()
            if not original or evidence_digest(original)!=r['evidence_sha256']:raise ValueError('Evidence changed since review; recheck required')
            if r['decision'] not in {'verified','qualified','partial','excluded','unavailable','needs_review'}:raise ValueError('Unknown review decision')
            if not r.get('locations') or not r.get('source_url','').startswith('https://') or not r.get('conclusion'):raise ValueError('Review requires source, location and conclusion')
            if r['decision'] in {'verified','qualified'} and (r.get('verification_level') not in {'full_text_checked','full_text_checked_with_limits'} or not r.get('document_sha256')):raise ValueError('Full-text review requires a document hash')
            values=(r['id'],r['evidence_id'],pack['reviewed_at'],r['decision'],dumps(r))
            prior=con.execute('SELECT * FROM evidence_reviews WHERE id=?',(r['id'],)).fetchone()
            if prior and tuple(prior)!=values:raise ValueError('Review history cannot be overwritten')
            cur=con.execute('INSERT OR IGNORE INTO evidence_reviews VALUES(?,?,?,?,?)',values);added+=cur.rowcount
    return {'added':added,'pack_reviews':len(pack['reviews']),'batch':pack['batch_id']}
