Welcome to ExampleLib
=====================

ExampleLib is a small library for parsing structured logs.

Quickstart
----------

Install via pip and import the parser::

    pip install examplelib

Then::

    from examplelib import parse
    result = parse(open("server.log"))

The parser yields one event per line. Events are dicts with a
``timestamp``, ``level``, and ``message`` key.

Parser configuration
--------------------

Configure the parser by passing a ``ParserConfig`` instance::

    from examplelib import parse, ParserConfig
    cfg = ParserConfig(strict=True, encoding="utf-8")
    result = parse(open("server.log"), cfg)

When ``strict=True``, malformed lines raise ``ParseError`` instead of
being silently dropped.

Streaming mode
--------------

For large files use ``stream_parse`` which yields incrementally without
loading the whole file into memory.

Error handling
--------------

ExampleLib raises three exception types:

- ``ParseError`` — a single line failed to parse.
- ``EncodingError`` — file is not valid UTF-8 (or the configured encoding).
- ``ConfigError`` — invalid ``ParserConfig`` value.
