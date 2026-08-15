#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Make stdout and stderr able to carry Japanese, whatever shell started us.

Python takes the encoding of stdout from the locale, not from the source file.
On this machine PowerShell reports UTF-8 and Git Bash reports cp1252, so

    python tools/build.py --use-dora-block --refactor

- the documented command - worked in one shell and died in the other with
UnicodeEncodeError the moment it printed a yaku name. It died at the *end*,
after jangou-en.{nes,bps,ips} were already written: a traceback sitting on top
of three correct artifacts, which looks like a broken build and is not one.

stderr matters too. `encode()` reports an untranslatable character with %r, so
a kanji left in script/yaku-en.txt would crash the error message meant to
explain it.

errors="replace" rather than "strict": a console that cannot show a kanji
should print a substitute, not take the build down. Importing this module is
the whole interface - every entry point in tools/ does it before its first
print.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass          # Python < 3.7, or a stream someone else already wrapped
