#!/bin/zsh
# Script to sync current workspace code to EC2 instance

# === CONFIGURATION ===
EC2_USER="ec2-user"           # Change to 'ubuntu' if using Ubuntu AMI
EC2_HOST="<your-ec2-public-dns>"  # e.g., ec2-xx-xx-xx-xx.compute-1.amazonaws.com
EC2_KEY="/path/to/your-key.pem"   # Path to your SSH private key
REMOTE_DIR="~/fastapi-s3-sync"    # Directory on EC2 to sync code to

# === SYNC CODE ===
rsync -avz --exclude 'venv' --exclude '.git*' --exclude '__pycache__' -e "ssh -i $EC2_KEY" ./ $EC2_USER@$EC2_HOST:$REMOTE_DIR

# === OPTIONAL: Restart FastAPI app ===
# Uncomment the following lines to restart the app after upload
# ssh -i $EC2_KEY $EC2_USER@$EC2_HOST "pkill -f 'uvicorn' || true"
# ssh -i $EC2_KEY $EC2_USER@$EC2_HOST "cd $REMOTE_DIR && source venv/bin/activate && nohup venv/bin/uvicorn main:app --host 0.0.0.0 --port 80 > app.log 2>&1 &"

echo "Code sync complete!"
