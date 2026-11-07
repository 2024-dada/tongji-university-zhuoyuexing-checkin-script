"""Build a clean portable distribution without modifying live user data."""
import hashlib
import json
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(sys.executable).resolve().parent
OUT = ROOT / '发布'
OUT.mkdir(exist_ok=True)
STAGE = OUT / ('build-' + uuid.uuid4().hex[:8]) / '卓越星助手'
STAGE.mkdir(parents=True)
guide = ROOT / 'docs' / '使用说明.docx'
if not guide.is_file():
    raise FileNotFoundError('Human-readable Word guide is missing: ' + str(guide))
shutil.copy2(guide, STAGE / '使用说明.docx')
PROGRAM = STAGE / '程序文件'
PROGRAM.mkdir()
driver = ROOT / 'runtime-tools' / 'edgedriver' / 'msedgedriver.exe'
if driver.is_file():
    driver_target = PROGRAM / 'runtime-tools' / 'edgedriver'
    driver_target.mkdir(parents=True)
    shutil.copy2(driver, driver_target / driver.name)
    notices = driver.parent / 'Driver_Notes'
    if notices.is_dir():
        shutil.copytree(notices, driver_target / 'Driver_Notes')
for name in ('app.py', 'launcher.pyw'):
    shutil.copy2(ROOT / name, PROGRAM / name)
for name in ('checkin', 'automation'):
    shutil.copytree(ROOT / name, PROGRAM / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
# Ship a standalone maintenance guide, offline tests, and the whitelisted builder.
readme = ROOT / 'docs' / 'portable-readme.md'
if not readme.is_file():
    readme = ROOT / 'README.md'
shutil.copy2(readme, PROGRAM / 'README.md')
agent_guide = ROOT / 'agent.md'
if not agent_guide.is_file():
    raise FileNotFoundError('AI maintenance guide is missing: ' + str(agent_guide))
shutil.copy2(agent_guide, PROGRAM / 'agent.md')
(PROGRAM / 'tests').mkdir()
for test in sorted((ROOT / 'tests').glob('test_*.py')):
    shutil.copy2(test, PROGRAM / 'tests' / test.name)
(PROGRAM / 'tools').mkdir()
shutil.copy2(Path(__file__), PROGRAM / 'tools' / 'build_portable.py')
(PROGRAM / '.zhuoyuex-checkin-installation').write_bytes(b'zhuoyuex-checkin-installation-v1\n')
(PROGRAM / '照片').mkdir()
(PROGRAM / '打卡记录.txt').write_bytes(b'')
from checkin.batch import PAGE_THEMES
for theme in PAGE_THEMES:
    record = PROGRAM / '打卡记录' / (theme + '打卡记录.txt')
    record.parent.mkdir(exist_ok=True)
    record.write_bytes(b'')
(PROGRAM / '打卡记录' / '.已迁移').write_text('empty portable records', encoding='utf-8')
(PROGRAM / 'config.json').write_text(json.dumps(dict(wecom_exe='', model='deepseek-flash', minimum_chars=80, allow_fake_ip_dns=False), ensure_ascii=False, indent=2), 'utf-8')
facts=json.loads((ROOT / '教师资料.json').read_text('utf-8-sig'))
public={name: facts[name] for name in ('杜建忠','林思劼','黄宏伟') if name in facts}
(PROGRAM / '教师资料.json').write_text(json.dumps(public,ensure_ascii=False,indent=2),'utf-8')
(STAGE / '启动卓越星助手.cmd').write_text('@echo off\r\ncd /d "%~dp0程序文件"\r\nstart "" "%~dp0程序文件\\runtime\\pythonw.exe" "%~dp0程序文件\\launcher.pyw"\r\n', 'mbcs')
rt=PROGRAM / 'runtime';rt.mkdir()
for name in ('python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt'):
    shutil.copy2(RUNTIME / name, rt / name)
shutil.copytree(RUNTIME/'DLLs',rt/'DLLs',ignore=shutil.ignore_patterns('_test*','*_test.pyd','__pycache__'))
shutil.copytree(RUNTIME/'tcl',rt/'tcl',ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(RUNTIME/'Lib',rt/'Lib',ignore=shutil.ignore_patterns('site-packages','__pycache__','*.pyc','test','tests','idlelib','ensurepip'))
sp=rt/'Lib'/'site-packages';sp.mkdir()
for name in ('PIL','pillow-12.3.0.dist-info'):
    shutil.copytree(RUNTIME/'Lib'/'site-packages'/name,sp/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
files=[p for p in STAGE.rglob('*') if p.is_file()]
assert (PROGRAM/'打卡记录.txt').stat().st_size==0
assert not (PROGRAM/'.private').exists()
assert (PROGRAM/'.zhuoyuex-checkin-installation').read_bytes() == b'zhuoyuex-checkin-installation-v1\n'
assert not any('capture-' in p.name or p.name in ('启动日志.txt','打卡记录.txt.bak') for p in files)
# The resume cursor belongs to the user's own machine, never to a share package.
assert not any(p.name == '续跑记忆.json' or p.name.startswith('续跑记忆-损坏-') for p in files)
# A per-file manifest makes a recipient's extracted package verifiable.
manifest={str(p.relative_to(STAGE)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(PROGRAM/'文件校验.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
zip_path=OUT / ('卓越星助手-便携版-' + uuid.uuid4().hex[:8] + '.zip')
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
    for path in sorted(STAGE.rglob('*')):
        if path.is_file():
            archive.write(path, str(Path('卓越星助手') / path.relative_to(STAGE)))
with zipfile.ZipFile(zip_path) as archive:
    assert archive.read('卓越星助手/程序文件/打卡记录.txt') == b''
    assert '卓越星助手/使用说明.docx' in archive.namelist()
    assert '卓越星助手/启动卓越星助手.cmd' in archive.namelist()
    assert '卓越星助手/程序文件/checkin/shizhi.py' in archive.namelist()
    for theme in PAGE_THEMES:
        assert archive.read('卓越星助手/程序文件/打卡记录/' + theme + '打卡记录.txt') == b''
    assert '卓越星助手/程序文件/README.md' in archive.namelist()
    assert '卓越星助手/程序文件/agent.md' in archive.namelist()
    assert not any('/.private/' in n or n.endswith('.dpapi') for n in archive.namelist())
print(json.dumps(dict(zip=str(zip_path),stage=str(STAGE),size_mb=round(zip_path.stat().st_size/1024/1024,1)),ensure_ascii=True))

# Remove only this build's generated staging folder after the archive passed validation.
stage_parent = STAGE.parent.resolve()
output_root = OUT.resolve()
stage_suffix = stage_parent.name.removeprefix('build-')
if (not STAGE.parent.is_symlink() and stage_parent.parent == output_root
        and stage_parent.name.startswith('build-') and len(stage_suffix) == 8
        and all(char in '0123456789abcdef' for char in stage_suffix)):
    shutil.rmtree(stage_parent)
