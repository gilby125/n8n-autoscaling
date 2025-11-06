# Use an official Python runtime as a parent image
# Build version: 2025-11-06-v2
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the Python script into the container
COPY ./monitor/monitor_redis_queue.py .

# Install any needed packages specified in requirements.txt
# For this script, we only need 'redis'
RUN pip install --no-cache-dir redis

# Define environment variables that can be overridden at runtime
# These defaults should work with the existing docker-compose.yml
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379
ENV QUEUE_NAME_PREFIX=bull
ENV QUEUE_NAME=jobs
ENV POLL_INTERVAL_SECONDS=5

# Run monitor_redis_queue.py when the container launches
CMD ["python", "-u", "monitor_redis_queue.py"]