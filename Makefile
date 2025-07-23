cs:
	flake8 .
black:
	black .


install:
	pip install -e .

pkg:
	python multi-build.py
build: pkg

clear-dist:
	python -c "import shutil, os; shutil.rmtree('dist', ignore_errors=True); os.makedirs('dist', exist_ok=True)"
clr-dist: clear-dist


publish:
	python -c "import os,subprocess;t=os.getenv('PYPI_TOKEN');subprocess.run(['python', '-m', 'twine', 'upload', 'dist/*', '-u', '__token__', '-p', t], check=True)"

upload: publish
test:
	pytest --log-cli-level=INFO
tests: test