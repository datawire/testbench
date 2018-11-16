SHELL = bash

lint:
	flake8 | tee >(wc -l)
	mypy --strict mkosi testbench_tap | grep -v -e 'GeneratorContextManager' | tee >(wc -l)
.PHONY: lint

DOCKER_IMAGE = gcr.io/datawireio/testbench-mkosi

docker-image:
	docker build -t testbench-mkosi-build -f docker/Dockerfile.build docker/
	docker run --rm --volume $(CURDIR)/docker:/dest testbench-mkosi-build sh -c 'cp -t /dest -- rpmbuild/RPMS/x86_64/*'
	docker build -t $(DOCKER_IMAGE) docker/
docker-push:
	docker push $(DOCKER_IMAGE)
.PHONY: docker-image docker-push
