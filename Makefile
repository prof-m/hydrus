#Makefile for running the docker images via the compose files, without having to type everything each time!

.PHONY: list
list:
	@LC_ALL=C $(MAKE) -pRrq -f $(lastword $(MAKEFILE_LIST)) : 2>/dev/null | awk -v RS= -F: '/^# File/,/^# Finished Make data base/ {if ($$1 !~ "^[#.]") {print $$1}}' | sort | egrep -v -e '^[^[:alnum:]]' -e '^$@$$'

server-local:
	docker compose -f compose.base.yaml -f compose.local.yaml up hydrus-server

server-local-n:
	docker compose -f compose.nginx-server.base.yaml -f compose.local.yaml up

server-test:
	docker compose -f compose.base.yaml -f compose.test.yaml up hydrus-server

server-cloud:
	docker compose -f compose.base.yaml -f compose.cloud.yaml up hydrus-server

server-down:
	docker compose -f compose.base.yaml down hydrus-server

docker-clean: server-down
	docker image rm ghcr.io/hydrusnetwork/hydrus:server
	docker image prune -f
	docker container prune -f