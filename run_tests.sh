#!/usr/bin/env bash
# roda a suite de testes com o python do venv do Applio
cd "$(dirname "$0")" || exit 1
exec Applio/.venv/bin/python -m unittest discover -s tests -t . "$@"
