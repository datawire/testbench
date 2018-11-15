SHELL = bash

lint:
	flake8 | tee >(wc -l)
	mypy --strict mkosi | grep -v -e 'GeneratorContextManager' | tee >(wc -l)
.PHONY: lint

DOCKER_IMAGE = gcr.io/datawireio/testbench-mkosi

docker-image:
	docker build -t $(DOCKER_IMAGE) docker/
docker-push:
	docker push $(DOCKER_IMAGE)
.PHONY: docker-image docker-push
