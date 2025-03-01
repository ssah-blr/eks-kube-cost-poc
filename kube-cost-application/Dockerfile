# Lightweight Python image
FROM --platform=linux/amd64 python:3.9-slim

# Set working directory inside the container
WORKDIR /app

# Copy the Python script into the container
COPY ./pod_resource_collector.py /app/pod_resource_collector.py

# Install required Python packages
RUN pip install --no-cache-dir kubernetes prometheus-client flask

# Set the entry point command
CMD ["python3", "-u", "pod_resource_collector.py"]
