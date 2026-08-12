API Reference
=============

This page lists the public surface of ExampleLib.

parse function
--------------

.. function:: parse(stream, config=None)

   Parse a file-like object into a list of event dicts. ``config`` is
   an optional ``ParserConfig``; when omitted the default config is
   used.

   Returns a list of dicts. Each dict has keys ``timestamp``, ``level``,
   ``message``.

stream_parse generator
----------------------

.. function:: stream_parse(stream, config=None)

   Like ``parse`` but yields events lazily. Use this for files that
   don't fit in memory.

ParserConfig dataclass
----------------------

.. class:: ParserConfig(strict=False, encoding="utf-8", line_limit=None)

   :param strict: Raise ``ParseError`` on malformed lines.
   :param encoding: Byte encoding of the input file.
   :param line_limit: Stop after this many lines. ``None`` means
                      unlimited.
