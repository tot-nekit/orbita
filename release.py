"""Create a source release with an explicit allowlist; never include runtime secrets."""
import argparse
import hashlib
import re
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = ('.dockerignore', '.gitignore', '.env.example', 'Caddyfile', 'Dockerfile',
         'LICENSE', 'README.md', 'START_HERE.txt', 'Start.command', 'backup.py',
         'compose.yaml', 'configure.py', 'hosted.py', 'manage.py', 'package.json',
         'package-lock.json', 'prepare_hosted.py', 'release.py', 'requirements.txt',
         'run.py', 'scheduled.py', 'setup_webhook.py', 'start-bot.sh', 'start.bat',
         'start.sh', 'test.py')
FOLDERS = ('app', 'resources', 'docs', 'tests', '.github')
BLOCKED = {'.env', '__pycache__', '.DS_Store', 'node_modules', 'data', 'backups'}
SENSITIVE = re.compile(rb'sk-[A-Za-z0-9_-]{28,}|[0-9]{8,12}:[A-Za-z0-9_-]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')


def sources():
    candidates = [ROOT / p for p in FILES]
    for folder in FOLDERS:
        candidates.extend((ROOT / folder).rglob('*'))
    result = []
    for path in sorted(candidates):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if BLOCKED.intersection(relative.parts) or path.suffix in ('.pyc', '.sqlite3', '.db', '.log', '.key', '.pem', '.zip', '.tgz'):
            continue
        data = path.read_bytes()
        if SENSITIVE.search(data):
            raise ValueError('Possible credential in release source: ' + str(relative))
        result.append((path, relative))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--version', default='3.3.0')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('version must be numeric')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    files = sources()
    zip_path = output / ('orbita-v' + args.version + '.zip')
    tar_path = output / ('orbita-v' + args.version + '-source.tar.gz')
    for path in (zip_path, tar_path):
        if path.exists():
            raise FileExistsError('Release already exists: ' + str(path))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, relative in files:
            archive.write(path, str(Path('orbita') / relative))
    with tarfile.open(tar_path, 'w:gz') as archive:
        for path, relative in files:
            archive.add(path, arcname=str(relative), recursive=False)
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip():
            raise ValueError('Release integrity check failed')
    print('Source files:', len(files))
    for path in (zip_path, tar_path):
        print(path)
        print('SHA256:', hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
