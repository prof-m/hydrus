#Makefile for running the docker images via the compose files, without having to type everything each time!

server-local:
	docker compose -f compose.base.yaml -f compose.local.yaml up hydrus-server

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