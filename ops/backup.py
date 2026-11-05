import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def backup(database, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix+'.partial')
    with temporary.open('wb') as output:
        process = subprocess.run(['docker','compose','exec','-T','db','pg_dump','-U','postgres','-Fc',database],
                                 cwd=ROOT,stdout=output,stderr=subprocess.PIPE)
    if process.returncode:
        raise RuntimeError(process.stderr.decode(errors='replace'))
    temporary.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix+'.json').write_text(json.dumps({'database':database,'sha256':digest},indent=2),encoding='utf-8')
    return digest


def restore(database, source):
    source = Path(source)
    metadata = json.loads(source.with_suffix(source.suffix+'.json').read_text(encoding='utf-8'))
    if hashlib.sha256(source.read_bytes()).hexdigest() != metadata['sha256']:
        raise ValueError('Backup checksum mismatch')
    subprocess.run(['docker','compose','exec','-T','db','createdb','-U','postgres',database],cwd=ROOT,check=True)
    with source.open('rb') as data:
        subprocess.run(['docker','compose','exec','-T','db','pg_restore','-U','postgres','--exit-on-error',
                        '--no-owner','--no-privileges','-d',database],cwd=ROOT,stdin=data,check=True)
    subprocess.run(['docker','compose','exec','-T','db','psql','-U','postgres','-d',database,'-v','ON_ERROR_STOP=1',
                    '-f','/defense/sql/08_access.sql'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('backup','restore'))
    parser.add_argument('--database',default='inventory_case2')
    parser.add_argument('--file',default='backups/inventory.dump')
    args=parser.parse_args()
    (backup if args.action=='backup' else restore)(args.database,args.file)
    print(args.action+' completed: '+args.database)
