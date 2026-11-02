#!/usr/bin/env python3
"""Reserve stable display-family codes across one user's projects."""
import argparse
import json
from pathlib import Path
import sqlite3


def reserve(store, client, parent):
    for value in (client, parent):
        if not isinstance(value, str) or not value or len(value) > 512 or not value.isprintable():
            raise ValueError('Client and native parent ID must be printable nonempty strings of at most 512 characters')
    store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(store, timeout=5)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS families ('
            'number INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT NOT NULL, '
            'parent TEXT NOT NULL, UNIQUE(client, parent))')
        row = connection.execute('SELECT number FROM families WHERE client=? AND parent=?', (client, parent)).fetchone()
        if row is None:
            cursor = connection.execute('INSERT INTO families(client, parent) VALUES (?, ?)', (client, parent))
            number = cursor.lastrowid
        else:
            number = row[0]
        connection.commit()
        return 'W' + str(number)
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True)
    parser.add_argument('--parent', required=True, help='Stable native parent identity, never its title or project path')
    args = parser.parse_args()
    try:
        store = Path.home() / '.vibe-coding-workflow' / 'workstreams-families.sqlite3'
        print(json.dumps(dict(ok=True, family=reserve(store, args.client, args.parent))))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
