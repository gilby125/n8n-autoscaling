import os
import time
import redis
import docker
import subprocess
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration from Environment Variables ---
REDIS_HOST = os.getenv('REDIS_HOST')
REDIS_PORT = int(os.getenv('REDIS_PORT'))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD') # Added for completeness
QUEUE_NAME_PREFIX = os.getenv('QUEUE_NAME_PREFIX')
QUEUE_NAME = os.getenv('QUEUE_NAME')

N8N_WORKER_SERVICE_NAME = os.getenv('N8N_WORKER_SERVICE_NAME')
COMPOSE_PROJECT_NAME = os.getenv('COMPOSE_PROJECT_NAME') # e.g., "n8n-workers"
COMPOSE_FILE_PATH = os.getenv('COMPOSE_FILE_PATH') # Path inside this container

# Deployment mode: 'compose' for Docker Compose, 'swarm' for Docker Swarm/Dockploy
DEPLOYMENT_MODE = os.getenv('DEPLOYMENT_MODE', 'compose').lower()

MIN_REPLICAS = int(os.getenv('MIN_REPLICAS'))
MAX_REPLICAS = int(os.getenv('MAX_REPLICAS'))
SCALE_UP_QUEUE_THRESHOLD = int(os.getenv('SCALE_UP_QUEUE_THRESHOLD'))
SCALE_DOWN_QUEUE_THRESHOLD = int(os.getenv('SCALE_DOWN_QUEUE_THRESHOLD'))

POLLING_INTERVAL_SECONDS = int(os.getenv('POLLING_INTERVAL_SECONDS'))
COOLDOWN_PERIOD_SECONDS = int(os.getenv('COOLDOWN_PERIOD_SECONDS'))

last_scale_time = 0

def get_redis_connection():
    """Establishes a connection to Redis."""
    logging.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)

def get_queue_length(r_conn):
    """Gets the length of the specified BullMQ waiting queue."""
    key_to_check = f"{QUEUE_NAME_PREFIX}:{QUEUE_NAME}:wait"
    length = None
    try:
        length = r_conn.llen(key_to_check)
        if length is not None:
            return length
        
        # Try BullMQ v4+ pattern
        key_to_check_v4 = f"{QUEUE_NAME_PREFIX}:{QUEUE_NAME}:waiting"
        length = r_conn.llen(key_to_check_v4)
        if length is not None:
            logging.info(f"Using BullMQ v4+ key pattern '{key_to_check_v4}' for queue length.")
            return length

        # Try legacy pattern (sometimes just the queue name for older Bull versions or simple lists)
        key_to_check_legacy = f"{QUEUE_NAME_PREFIX}:{QUEUE_NAME}"
        length = r_conn.llen(key_to_check_legacy)
        if length is not None:
            logging.info(f"Using legacy key pattern '{key_to_check_legacy}' for queue length.")
            return length
        
        logging.warning(f"Queue key patterns ('{key_to_check}', '{key_to_check_v4}', '{key_to_check_legacy}') not found or not a list. Assuming length 0.")
        return 0
    except redis.exceptions.ResponseError as e:
        logging.error(f"Redis error when checking length of queue keys: {e}. Assuming length 0.")
        return 0
    except Exception as e:
        logging.error(f"Unexpected error checking queue length: {e}. Assuming length 0.")
        return 0


def get_current_replicas(docker_client, service_name, project_name):
    """Gets the current number of running replicas for a Docker Swarm service or Compose service."""

    if DEPLOYMENT_MODE == 'swarm':
        # Docker Swarm mode (for Dockploy)
        try:
            # Find service by pattern matching (works with any project name)
            services = docker_client.services.list()
            worker_service = None

            for svc in services:
                if svc.name.endswith(f'_{service_name}'):
                    worker_service = svc
                    logging.info(f"[Swarm] Found worker service: {svc.name}")
                    break

            if worker_service:
                replicas = worker_service.attrs['Spec']['Mode']['Replicated']['Replicas']
                logging.info(f"[Swarm] Current replicas: {replicas}")
                return replicas
            else:
                logging.error(f"[Swarm] No service found matching pattern '*_{service_name}'")
                return MAX_REPLICAS + 1

        except Exception as e:
            logging.error(f"Error getting Swarm service replicas: {e}")
            return MAX_REPLICAS + 1

    else:
        # Docker Compose mode (default)
        if not project_name:
            logging.warning("COMPOSE_PROJECT_NAME is not set. Cannot accurately determine current replicas.")
            return MAX_REPLICAS + 1

        try:
            filters = {
                "label": [
                    f"com.docker.compose.service={service_name}",
                    f"com.docker.compose.project={project_name}"
                ],
                "status": "running"
            }
            service_containers = docker_client.containers.list(filters=filters, all=True)

            running_count = 0
            for container in service_containers:
                if container.status == 'running':
                     running_count +=1
            logging.info(f"[Compose] Found {running_count} running containers for service '{service_name}' in project '{project_name}'.")
            return running_count
        except Exception as e:
            logging.error(f"Error getting current replicas for {service_name} in {project_name}: {e}")
            return MAX_REPLICAS + 1


def scale_service(service_name, replicas, compose_file, project_name):
    """Scales a Docker Swarm service or Compose service."""

    if DEPLOYMENT_MODE == 'swarm':
        # Docker Swarm mode - find service by pattern and scale it
        try:
            import docker
            docker_cl = docker.from_env()
            services = docker_cl.services.list()

            worker_service = None
            for svc in services:
                if svc.name.endswith(f'_{service_name}'):
                    worker_service = svc
                    break

            if not worker_service:
                logging.error(f"[Swarm] No service found matching pattern '*_{service_name}'")
                return False

            full_service_name = worker_service.name
            command = ["docker", "service", "scale", f"{full_service_name}={replicas}"]
            logging.info(f"[Swarm] Executing scaling command: {' '.join(command)}")

            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logging.info(f"Scale command stdout: {result.stdout.strip()}")
            if result.stderr.strip():
                logging.warning(f"Scale command stderr: {result.stderr.strip()}")
            return True

        except subprocess.CalledProcessError as e:
            logging.error(f"Error scaling Swarm service {service_name} to {replicas}:")
            logging.error(f"  Command: {' '.join(e.cmd)}")
            logging.error(f"  Return Code: {e.returncode}")
            logging.error(f"  Stdout: {e.stdout.strip()}")
            logging.error(f"  Stderr: {e.stderr.strip()}")
            return False
        except Exception as e:
            logging.error(f"Error finding/scaling Swarm service: {e}")
            return False

    else:
        # Docker Compose mode
        if not project_name:
            logging.error("COMPOSE_PROJECT_NAME is not set. Cannot execute docker-compose scale.")
            return False

        command = [
            "docker",
            "compose",
            "-f", compose_file,
            "--project-name", project_name,
            "--project-directory", "/app",
            "up",
            "-d",
            "--no-deps",
            "--scale", f"{service_name}={replicas}",
            service_name
        ]
        logging.info(f"[Compose] Executing scaling command: {' '.join(command)}")
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logging.info(f"Scale command stdout: {result.stdout.strip()}")
            if result.stderr.strip():
                 logging.warning(f"Scale command stderr: {result.stderr.strip()}")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Error scaling service {service_name} to {replicas}:")
            logging.error(f"  Command: {' '.join(e.cmd)}")
            logging.error(f"  Return Code: {e.returncode}")
            logging.error(f"  Stdout: {e.stdout.strip()}")
            logging.error(f"  Stderr: {e.stderr.strip()}")
            return False
        except FileNotFoundError:
            logging.error("docker-compose command not found. Ensure it's installed in the autoscaler container and in PATH.")
            return False

def main():
    global last_scale_time

    # In Compose mode, project name is required. In Swarm mode, it's optional (auto-detected)
    if not COMPOSE_PROJECT_NAME and DEPLOYMENT_MODE == 'compose':
        logging.error("CRITICAL: COMPOSE_PROJECT_NAME environment variable is required for Compose mode.")
        logging.error("Please set COMPOSE_PROJECT_NAME to the name of your Docker Compose project (usually the directory name).")
        return # Exit if critical env var is missing

    try:
        r_conn = get_redis_connection()
        docker_cl = docker.from_env()
        # Test Docker connection
        docker_cl.ping()
        logging.info("Successfully connected to Docker daemon.")
    except Exception as e:
        logging.error(f"CRITICAL: Failed to connect to Redis or Docker: {e}")
        return

    logging.info(f"Autoscaler started. Deployment mode: {DEPLOYMENT_MODE.upper()}")
    if DEPLOYMENT_MODE == 'swarm':
        logging.info(f"Monitoring n8n worker service pattern '*_{N8N_WORKER_SERVICE_NAME}' (auto-detected)")
    else:
        logging.info(f"Monitoring n8n worker service '{N8N_WORKER_SERVICE_NAME}' in project '{COMPOSE_PROJECT_NAME}'.")
    logging.info(f"  Min Replicas: {MIN_REPLICAS}, Max Replicas: {MAX_REPLICAS}")
    logging.info(f"  Scale Up Queue Threshold: >{SCALE_UP_QUEUE_THRESHOLD}")
    logging.info(f"  Scale Down Queue Threshold: <{SCALE_DOWN_QUEUE_THRESHOLD}")
    logging.info(f"  Polling Interval: {POLLING_INTERVAL_SECONDS}s, Cooldown: {COOLDOWN_PERIOD_SECONDS}s")

    while True:
        try:
            current_time = time.time()
            if (current_time - last_scale_time) < COOLDOWN_PERIOD_SECONDS:
                logging.info(f"In cooldown period. Next check in {COOLDOWN_PERIOD_SECONDS - (current_time - last_scale_time):.0f}s.")
                time.sleep(POLLING_INTERVAL_SECONDS) # Still sleep for polling interval
                continue

            queue_len = get_queue_length(r_conn)
            current_reps = get_current_replicas(docker_cl, N8N_WORKER_SERVICE_NAME, COMPOSE_PROJECT_NAME)

            logging.info(f"Queue Length: {queue_len}, Current Replicas: {current_reps}")

            scaled = False
            if queue_len > SCALE_UP_QUEUE_THRESHOLD and current_reps < MAX_REPLICAS:
                new_replicas = min(current_reps + 1, MAX_REPLICAS) # Scale one by one for now
                logging.info(f"Condition met for SCALE UP. Queue: {queue_len} > {SCALE_UP_QUEUE_THRESHOLD}. Replicas: {current_reps} < {MAX_REPLICAS}.")
                if scale_service(N8N_WORKER_SERVICE_NAME, new_replicas, COMPOSE_FILE_PATH, COMPOSE_PROJECT_NAME):
                    last_scale_time = current_time
                    scaled = True
            elif queue_len < SCALE_DOWN_QUEUE_THRESHOLD and current_reps > MIN_REPLICAS:
                new_replicas = max(current_reps - 1, MIN_REPLICAS) # Scale one by one
                logging.info(f"Condition met for SCALE DOWN. Queue: {queue_len} < {SCALE_DOWN_QUEUE_THRESHOLD}. Replicas: {current_reps} > {MIN_REPLICAS}.")
                if scale_service(N8N_WORKER_SERVICE_NAME, new_replicas, COMPOSE_FILE_PATH, COMPOSE_PROJECT_NAME):
                    last_scale_time = current_time
                    scaled = True
            
            if not scaled:
                logging.info("No scaling action needed.")

        except redis.exceptions.ConnectionError as e:
            logging.error(f"Redis connection error: {e}. Retrying connection...")
            time.sleep(5) # Wait before retrying Redis connection
            try:
                r_conn = get_redis_connection()
            except Exception as recon_e:
                logging.error(f"Failed to reconnect to Redis: {recon_e}")
        except Exception as e:
            logging.error(f"Error in autoscaler main loop: {e}", exc_info=True)

        time.sleep(POLLING_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()