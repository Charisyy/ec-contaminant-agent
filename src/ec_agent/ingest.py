from __future__ import annotations
import hashlib,json
from pathlib import Path
from .db import connect,initialize,dumps,now

def sid(key,smiles): return key or 'smiles-'+hashlib.sha256(smiles.encode()).hexdigest()

def import_catalog(snapshot, db):
    snapshot=Path(snapshot); raw=snapshot.read_bytes(); data=json.loads(raw)
    headers=data['headers']; records=data['rows']
    ids=[r[0] for r in records]
    if len(set(ids))!=len(ids): raise ValueError('Duplicate entity IDs in input')
    selected={r[7] for s in data['source_sheets'] if s['name']=='47种代表物' for r in s['values'][1:] if r[0]}
    if not selected.issubset(set(ids)): raise ValueError('Experiment candidates reference missing entities')
    initialize(db); digest=hashlib.sha256(raw).hexdigest(); import_id=digest[:24]
    with connect(db) as con:
        con.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?,?,?)',(import_id,now(),data['source_sha256'],digest,dumps({'count':len(records),'scope':'all_catalog','procurement_flag_only':len(selected),'filename':snapshot.name})))
        def insert_structure(key,smiles,inchi=None,formula=None,mw=None):
            structure_id=sid(key,smiles)
            con.execute('INSERT OR IGNORE INTO structures(id,inchikey,smiles,inchi,formula,molecular_weight) VALUES(?,?,?,?,?,?)',(structure_id,key or structure_id,smiles,inchi,formula,mw))
            return structure_id
        for i,r in enumerate(records,2):
            structure_id=insert_structure(r[23],r[21],r[22],r[19],r[20]) if r[21] else None
            payload=dict(zip(headers,r))
            values=(r[0],r[1],r[34],r[2] or None,r[13] or None,r[18],r[3],r[4],r[5],int(r[12]=='是'),int(r[0] in selected),r[24],r[11],r[29],r[7],r[9],r[8],i,structure_id,import_id,dumps(payload),now())
            fields='id,name,original_name,cas,original_cas,english_name,category,subcategory,entity_type,measured,experiment_candidate,identity_status,original_review,issue,source_databases,source_record_ids,source_record_count,original_row,selected_structure_id,import_id,payload_json,updated_at'
            update=','.join(f'{f}=excluded.{f}' for f in fields.split(',') if f!='id')
            con.execute(f'INSERT INTO entities({fields}) VALUES({",".join("?" for _ in values)}) ON CONFLICT(id) DO UPDATE SET {update}',values)
            # Selected mapping refreshes with the input snapshot; evidence and human reviews persist.
            con.execute("DELETE FROM entity_structures WHERE entity_id=? AND role='selected'",(r[0],))
            if structure_id:
                con.execute('INSERT OR REPLACE INTO entity_structures VALUES(?,?,?,?,?,?)',(r[0],structure_id,'selected',r[17],r[30],r[31]))
        for alt in data['alternatives']:
            eid,name,query,cid,title,formula,mw,smiles,key,reason,url=alt
            if not smiles: continue
            structure_id=insert_structure(key,smiles,formula=formula,mw=float(mw) if mw else None)
            role='candidate' if '多候选' in reason else 'representative_only'
            con.execute('INSERT OR REPLACE INTO entity_structures VALUES(?,?,?,?,?,?)',(eid,structure_id,role,cid,url,'2026-10-17'))
        con.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',('pubchem-structure-snapshot','structure_database','https://pubchem.ncbi.nlm.nih.gov/',now(),'retrieved 2026-10-17',digest,dumps({'row_urls':'entity_structures.source_url','method':'CAS/name candidates; not all manually verified','original_sha256':data['source_sha256']})))
    return {'import_id':import_id,'entities':len(records),'experiment_candidates':len(selected),'snapshot_sha256':digest}
