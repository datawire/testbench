SHELL = bash

lint:
	flake8 | tee >(wc -l)
	mypy --ignore-missing-imports --strict mkosi | grep -v -e 'GeneratorContextManager' -e 'Unexpected keyword argument "follow_symlinks" for "stat" of "DirEntry"' | tee >(wc -l)
