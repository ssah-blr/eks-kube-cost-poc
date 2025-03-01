# Lightweight Python image
FROM python:3.9-slim

# Set working directory in container
WORKDIR /app

# Copy application code into the container
COPY kube_resource_server.py /app/kube_resource_server.py

# Install dependencies
RUN pip install --no-cache-dir flask boto3

# Expose the port the app runs on
EXPOSE 5001

# Run the Flask app
CMD ["python3", "-u", "kube_resource_server.py"]
