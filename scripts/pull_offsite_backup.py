"""Compatibility entry for the shared verified-transfer-and-delete backup task."""
import argparse
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    shared = Path('D:/Projects/DeveloperGovernance/server-maintenance/offsite.py')
    if not shared.is_file():
        raise RuntimeError('Shared server-maintenance backup client is missing')
    sys.argv = [str(shared), '--graspmemoedu-destination', str(args.destination)]
    runpy.run_path(str(shared), run_name='__main__')


if __name__ == '__main__':
    main()
