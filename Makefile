# Fairy Agent 本地开发命令
# Windows 下若无 make，可直接执行各目标中的等价命令。

.PHONY: install format lint test

install:
	pip install -e ".[dev]"

format:
	ruff format .
	ruff check --fix .

lint:
	ruff format --check .
	ruff check .
	mypy src

test:
	pytest
