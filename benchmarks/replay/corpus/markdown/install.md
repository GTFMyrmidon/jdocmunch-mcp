# Installation

This page covers installing the package across supported platforms.

## Prerequisites

You need Python 3.10 or later and pip. On macOS we recommend the
official python.org installer; the system Python lacks `ensurepip`.

## Quick install

```bash
pip install example-package
```

Verify the install:

```bash
example-cli --version
```

## From source

Clone the repo and install in editable mode:

```bash
git clone https://github.com/example/repo.git
cd repo
pip install -e .[dev]
```

## Troubleshooting

If `pip install` fails with a build error mentioning `wheel`, upgrade
pip first:

```bash
pip install --upgrade pip wheel
```

If the binary is not on your PATH after install, add the Python user
scripts directory to PATH or reinstall with `--user` removed.
