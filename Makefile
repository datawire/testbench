SHELL = bash

lint:
	flake8 | tee >(wc -l)
	mypy --strict mkosi | grep -v -e 'GeneratorContextManager' | tee >(wc -l)
