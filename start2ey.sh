#!/bin/bash

# Connect to EC2 Server - EY workflow 
ssh -i ~/.ssh/ec2ey.pem ubuntu@54.237.55.135

# Connect to EC2 and pass the commands inside the 'EOF' block
ssh -i ~/.ssh/ec2ey.pem ubuntu@54.237.55.135 << 'EOF'

    # --- EVERYTHING BELOW RUNS ON THE SERVER ---

    echo "Starting Databases..."
    cd /mnt/data/stack
    # Important: Use -d (detached) so Docker runs in the background
    # and doesn't block the next command from running.
    docker compose up -d

    echo "Starting API..."
    # Use absolute paths or cd from home (~) to avoid path errors
    cd mnt/data/projects/Comparator
    source venv/bin/activate
    
    # This will run the API and occupy the terminal so you can see logs
    uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

EOF

# Within server:

# Start databases
## cd stack
## docker compose up

# Start API manually
## cd victor/ai_consumer_goods/
## source venv/bin/activate
## uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

