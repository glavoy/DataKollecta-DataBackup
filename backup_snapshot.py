#!/usr/bin/env python3
"""Explicit full-snapshot preparation, separate from the scheduled CSV utility.
Default output is encrypted. No production restore is supported by this tool.
Scheduling, key custody and platform recovery: DK-SOP-001 §7 (DataKollecta-Validation).
"""
import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from snapshot_integrity import digest, verify_manifest, write_manifest

LOOPBACK={'localhost','127.0.0.1','::1'}

def database_environment(url):
    p=urlparse(url)
    if p.scheme not in ('postgres','postgresql') or not p.hostname or not p.path.strip('/'):
        raise ValueError('A valid PostgreSQL connection URL is required')
    env=dict(os.environ)
    env.update(PGHOST=p.hostname,PGPORT=str(p.port or 5432),PGUSER=unquote(p.username or 'postgres'),PGDATABASE=unquote(p.path.strip('/')))
    if p.password is not None: env['PGPASSWORD']=unquote(p.password)
    env['PGSSLMODE']=parse_qs(p.query).get('sslmode',['disable' if p.hostname in LOOPBACK else 'verify-full'])[0]
    return env

def run(command,env=None):
    result=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        # Do not echo database errors containing clinical values or credentials.
        raise RuntimeError(f'{Path(command[0]).name} failed with exit status {result.returncode}; investigate using controlled administrator logs')
    return result.stdout

def export_storage(root):
    from export_data import get_client
    client=get_client()
    count=0
    for bucket in client.storage.list_buckets():
        bucket_id=bucket.id
        if Path(bucket_id).name != bucket_id: raise ValueError('Unsafe bucket identifier')
        store=client.storage.from_(bucket_id)
        def walk(prefix):
            nonlocal count
            offset=0
            while True:
                entries=store.list(prefix,{'limit':100,'offset':offset,'sortBy':{'column':'name','order':'asc'}})
                for entry in entries:
                    name=entry['name']
                    if Path(name).name!=name or name in ('.','..'): raise ValueError('Unsafe storage path')
                    key=f'{prefix}/{name}' if prefix else name
                    if entry.get('id') is None: walk(key)
                    else:
                        dest=root/'storage'/bucket_id/key
                        dest.parent.mkdir(parents=True,exist_ok=True)
                        dest.write_bytes(store.download(key)); count+=1
                if len(entries)<100: break
                offset+=100
        walk('')
    return count

def create_snapshot(output,recipient=None,synthetic=False,skip_storage=False):
    url=os.environ['DATAKOLLECTA_SNAPSHOT_DATABASE_URL']; env=database_environment(url)
    if synthetic and env['PGHOST'] not in LOOPBACK: raise ValueError('Unencrypted synthetic snapshots are restricted to loopback sources')
    if not synthetic and not recipient: raise ValueError('An approved GPG recipient is required for real snapshots')
    if not synthetic and skip_storage: raise ValueError('Real snapshots must include Storage objects')
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('snapshot-%Y%m%dT%H%M%S%fZ')
    with tempfile.TemporaryDirectory(prefix='.snapshot-',dir=output) as temp:
        temp=Path(temp); contents=temp/'contents'; contents.mkdir(mode=0o700)
        run(['pg_dump','--format=custom','--no-owner','--file',str(contents/'database.dump')],env)
        storage_count=0 if skip_storage else export_storage(contents)
        coverage={'database':'Complete accessible database dump including schema and metadata','storage_objects':storage_count,'storage_included':not skip_storage,'classification':'Synthetic engineering fixture' if synthetic else 'Snapshot candidate requiring reviewed reconciliation','consistency':'Database dump is transaction-consistent; Storage is enumerated separately. Freeze package changes during backup and reconcile inventory.'}
        write_manifest(contents,coverage)
        bundle=temp/'bundle.tar'
        with tarfile.open(bundle,'w') as archive:
            for file in sorted(contents.rglob('*')):
                if file.is_file(): archive.add(file,arcname=file.relative_to(contents).as_posix())
        suffix='.tar' if synthetic else '.tar.gpg'; staged=temp/(name+suffix)
        if synthetic: bundle.replace(staged)
        else: run(['gpg','--batch','--yes','--trust-model','always','--recipient',recipient,'--encrypt','--output',str(staged),str(bundle)])
        staged.chmod(0o600); final=output/staged.name; staged.replace(final)
        receipt={'file':final.name,'bytes':final.stat().st_size,'sha256':digest(final),'coverage':coverage,'created_at':datetime.now(timezone.utc).isoformat()}
        (output/(final.name+'.receipt.json')).write_text(json.dumps(receipt,indent=2))
        return receipt

def unpack_snapshot(bundle,destination):
    root=Path(destination).resolve()
    with tarfile.open(bundle) as archive:
        for member in archive.getmembers():
            path=root/member.name
            if not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts or not path.resolve().is_relative_to(root):
                raise ValueError('Unsafe snapshot archive member')
            path.parent.mkdir(parents=True,exist_ok=True)
            stream=archive.extractfile(member)
            with path.open('wb') as out:
                while block:=stream.read(1024*1024): out.write(block)
    return verify_manifest(root)

def restore_snapshot(bundle,confirm_database):
    # No clean/drop/overwrite option: only an empty local destination is allowed.
    env=database_environment(os.environ['DATAKOLLECTA_RESTORE_DATABASE_URL'])
    if env['PGHOST'] not in LOOPBACK or env['PGDATABASE'] in ('postgres','template0','template1') or env['PGDATABASE']!=confirm_database:
        raise ValueError('Restore requires an explicitly confirmed empty loopback-only test database')
    count=run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-c',"select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname not like 'pg_%' and n.nspname <> 'information_schema' and c.relkind in ('r','p','v','m','S');"],env).decode().strip()
    if count!='0': raise ValueError('Restore destination is not empty')
    bundle=Path(bundle); receipt=json.loads(Path(str(bundle)+'.receipt.json').read_text())
    if receipt['sha256']!=digest(bundle) or receipt['bytes']!=bundle.stat().st_size: raise ValueError('Archive receipt integrity check failed')
    with tempfile.TemporaryDirectory(prefix='datakollecta-restore-') as temp:
        temp=Path(temp); plain=bundle
        if bundle.suffix=='.gpg':
            plain=temp/'bundle.tar';run(['gpg','--batch','--decrypt','--output',str(plain),str(bundle)])
        content=temp/'contents';content.mkdir(mode=0o700);manifest=unpack_snapshot(plain,content)
        run(['pg_restore','--exit-on-error','--single-transaction','--no-owner','--dbname',env['PGDATABASE'],str(content/'database.dump')],env)
        # Restored storage files need a reviewed target-provider import. Never
        # silently claim that a database restore restored Storage objects.
        return {'database_restore':'Completed to isolated local target','storage_import':'Not performed; provider import and reconciliation required','manifest':manifest}

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='operation',required=True)
    create=sub.add_parser('create');create.add_argument('--output',required=True);create.add_argument('--recipient');create.add_argument('--synthetic-local',action='store_true');create.add_argument('--skip-storage',action='store_true')
    restore=sub.add_parser('restore-local');restore.add_argument('bundle');restore.add_argument('--confirm-database',required=True)
    args=p.parse_args()
    result=create_snapshot(args.output,args.recipient,args.synthetic_local,args.skip_storage) if args.operation=='create' else restore_snapshot(args.bundle,args.confirm_database)
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
