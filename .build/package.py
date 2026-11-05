from pathlib import Path
import hashlib
import zipfile

ROOT=Path(__file__).resolve().parents[1]
files=set()
for pattern in ('*.md','*.pdf','*.py','*.sql','requirements.txt','docker-compose.yml','.gitattributes','.gitignore','.dockerignore'):
    files.update(p for p in ROOT.glob(pattern) if p.is_file())
for directory in ('sql','docker','tests','benchmarks','ops'):
    files.update(p for p in (ROOT/directory).rglob('*') if p.is_file()
                 and '__pycache__' not in p.parts)
for name in ('build_report.py','refresh_evidence.py','package.py','verify_defense_race.py'):
    files.add(ROOT/'.build'/name)
archive=ROOT/'Case_2_Inventory_Management.zip'
manifest=[]
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
    for path in sorted(files):
        relative=path.relative_to(ROOT).as_posix()
        data=path.read_bytes()
        output.writestr(relative,data)
        manifest.append(hashlib.sha256(data).hexdigest()+'  '+relative)
    output.writestr('SHA256SUMS.txt','\n'.join(manifest)+'\n')
with zipfile.ZipFile(archive) as result:
    assert result.testzip() is None
    for path in files:
        assert result.read(path.relative_to(ROOT).as_posix())==path.read_bytes()
print(f'Packaged and verified {len(files)} files: {archive}')
