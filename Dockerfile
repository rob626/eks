FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install kubectl
RUN apt-get update && \
    apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY reroll_nodes.py .

# Make script executable
RUN chmod +x reroll_nodes.py

# Set the entrypoint
ENTRYPOINT ["python", "reroll_nodes.py"]

# Default to showing help
CMD ["--help"]
