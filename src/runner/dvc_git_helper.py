import os
import sys
import argparse
import subprocess
from pathlib import Path

try:
    from ruamel.yaml import YAML
except ImportError:
    # We expect this to be run via uv run --with ruamel.yaml
    pass

def log_info(msg):
    print(f"ℹ️  [DVC-Git-Helper] {msg}")

def log_warn(msg):
    print(f"⚠️  [DVC-Git-Helper] {msg}")

def log_success(msg):
    print(f"✅ [DVC-Git-Helper] {msg}")

def inject_cache_false(dvc_yaml_path):
    if not os.path.exists(dvc_yaml_path):
        log_info(f"{dvc_yaml_path} not found, skipping injection.")
        return

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(dvc_yaml_path, 'r') as f:
        data = yaml.load(f)

    if not data:
        log_info("Empty dvc.yaml.")
        return

    modified = False

    def process_entries(entries, container, key_in_container):
        nonlocal modified
        if isinstance(entries, list):
            for i, entry in enumerate(entries):
                if isinstance(entry, str):
                    container[key_in_container][i] = {entry: {'cache': False}}
                    modified = True
                elif isinstance(entry, dict):
                    for filename, config in entry.items():
                        if isinstance(config, dict):
                            if config.get('cache') is not False:
                                config['cache'] = False
                                modified = True
                        else:
                            entry[filename] = {'cache': False}
                            modified = True
        elif isinstance(entries, dict):
            for filename, config in entries.items():
                if isinstance(config, dict):
                    if config.get('cache') is not False:
                        config['cache'] = False
                        modified = True
                else:
                    entries[filename] = {'cache': False}
                    modified = True

    # Process stages
    if 'stages' in data:
        for stage_name, stage in data['stages'].items():
            for key in ['metrics', 'plots']:
                if key in stage:
                    process_entries(stage[key], stage, key)

    # Process top-level metrics and plots
    for key in ['metrics', 'plots']:
        if key in data:
            process_entries(data[key], data, key)

    if modified:
        with open(dvc_yaml_path, 'w') as f:
            yaml.dump(data, f)
        log_success(f"Injected 'cache: false' into {dvc_yaml_path} metrics/plots.")
    else:
        log_info("No changes needed in dvc.yaml.")

def get_cache_false_paths(dvc_yaml_path):
    if not os.path.exists(dvc_yaml_path):
        return []

    yaml = YAML()
    with open(dvc_yaml_path, 'r') as f:
        data = yaml.load(f)

    paths = set()
    if not data:
        return []

    def extract_from_entries(entries):
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    for path, config in entry.items():
                        if isinstance(config, dict) and config.get('cache') is False:
                            paths.add(path)
        elif isinstance(entries, dict):
            for path, config in entries.items():
                if isinstance(config, dict) and config.get('cache') is False:
                    paths.add(path)

    if 'stages' in data:
        for stage in data['stages'].values():
            for key in ['metrics', 'plots']:
                if key in stage:
                    extract_from_entries(stage[key])

    for key in ['metrics', 'plots']:
        if key in data:
            extract_from_entries(data[key])

    return list(paths)

def sync_metrics():
    dvc_yaml_path = 'dvc.yaml'
    paths = get_cache_false_paths(dvc_yaml_path)

    if not paths:
        log_info("No metrics/plots with cache: false found to sync.")
        return

    added_any = False
    for path in paths:
        if not os.path.isfile(path):
            continue

        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb < 5:
            subprocess.run(['git', 'add', path], check=True)
            log_info(f"Staged {path} ({size_mb:.2f} MB)")
            added_any = True
        else:
            log_warn(f"WARNING: Le fichier {path} (déclaré comme metric/plot) dépasse 5 Mo. Il ne sera synchronisé ni sur Git, ni sur le réseau P2P. Si vous souhaitez conserver ce fichier, déplacez-le sous la clé outs: dans votre dvc.yaml.")

    if added_any:
        # Check if there are actual changes staged
        res = subprocess.run(['git', 'diff', '--cached', '--quiet'])
        if res.returncode != 0:
            log_info("Committing and pushing auto-synced metrics...")
            subprocess.run(['git', 'config', 'user.name', 'cluster-ci-bot'], check=True)
            subprocess.run(['git', 'config', 'user.email', 'bot@cluster-ci.io'], check=True)
            subprocess.run(['git', 'commit', '-m', 'chore(ci): auto-sync metrics [skip ci]'], check=True)
            # Try to push to current branch
            subprocess.run(['git', 'push', 'origin', 'HEAD'], check=True)
            log_success("Metrics synced successfully.")
        else:
            log_info("No changes to commit.")
    else:
        log_info("No valid files to sync found.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    subparsers.add_parser('inject')
    subparsers.add_parser('sync')

    args = parser.parse_args()

    if args.command == 'inject':
        inject_cache_false('dvc.yaml')
    elif args.command == 'sync':
        sync_metrics()
    else:
        parser.print_help()
