#!/bin/bash

# Connect to server

## Run on server 

## Start databases
### cd stack
### docker compose up

## Start API manually
### cd victor/ai_consumer_goods/
### source venv/bin/activate
### uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

# Connect to EY services (api - mongodb - neo4j - postgres)
# ssh -i ~/.ssh/ec2ey.pem -N -L 8000:127.0.0.1:8000 -L 27017:127.0.0.1:27017 -L 3005:127.0.0.1:3000 -L 7687:127.0.0.1:7687 ubuntu@54.237.55.135
#
# Connect to EY services (databases only; API runs locally)
#
# Ports forwarded to localhost:
# - MongoDB:   27017
# - Neo4j Bolt: 7687
# - Neo4j HTTP: 7474
# - Postgres:  5432
#
# Required env for authenticated MongoDB (example):
#   export MONGO_URI="localhost:27017"
#   export MONGO_USER1="admin"
#   export MONGO_USER1_PASSWORD="..."
#   export MONGO_AUTH_SOURCE="admin"
#   export MONGO_DATABASE="test"

ssh -i ~/.ssh/ec2ey.pem -N \
  -L 27017:127.0.0.1:27017 \
  -L 7687:127.0.0.1:7687 \
  -L 7474:127.0.0.1:7474 \
  -L 5432:127.0.0.1:5432 \
  ubuntu@54.237.55.135

