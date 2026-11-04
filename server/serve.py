"""Start the server with the host and port from .env (see config.py). Used by run.sh."""
import uvicorn

import config

if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL.lower())
