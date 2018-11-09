SHELL = bash

lint:
	flake8 | tee >(wc -l)
	mypy --strict testbench | tee >(wc -l)
