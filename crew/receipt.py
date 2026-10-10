"""Write a private shell exit receipt; this command never modifies run state."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4


def write_receipt(path, intent, exit_code):
    if not re.fullmatch(r'[a-f0-9]+', intent) or type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise ValueError('invalid exit receipt identity or code')
    path = Path(path).absolute()
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = following
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError('receipt destination is not a regular file')
            raise ValueError('receipt already exists; never overwrite exit evidence')
        except FileNotFoundError:
            pass
        temporary = '.receipt-' + uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'launch_intent': intent, 'exit_code': exit_code}, stream)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.rename(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try: os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError: pass
    finally:
        os.close(directory)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 4:
            raise ValueError('receipt requires path, launch intent, exit code')
        write_receipt(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    except (OSError, ValueError):
        raise SystemExit(1)
