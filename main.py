import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
import configparser
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = configparser.ConfigParser()
config.read("configs.ini")

ollama_host = config["ollama"]["host"]
ollama_port = config.getint("ollama", "port")

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent

class Version(BaseModel):
    version: str
class Model(BaseModel):
    name: str
    model: str

class Tags(BaseModel):
    models: list[Model]
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]
    model: str
    think: bool = False

class ChatResponse(BaseModel):
    message: Message
    model: str
    done: bool = True
    eval_count: int = 0
class TokensResponse(BaseModel):
    llm_id: int
    llm_name: str
    total_tokens: int

# Dashboard with LLM usage statistics
@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(BASE_DIR / "dashboard.html")

# Ollama version
@app.get("/api/version", response_model=Version)
def get_ollama_version():
    response = requests.get(f"http://{ollama_host}:{ollama_port}/api/version")
    response.raise_for_status()
    payload = response.json()
    logger.info(f"Retrieved version from Ollama: {payload}")
    return Version(version=payload["version"])

# Ollama models
@app.get("/api/tags", response_model=Tags)
def get_ollama_models():
    response = requests.get(f"http://{ollama_host}:{ollama_port}/api/tags")
    response.raise_for_status()
    payload = response.json()
    logger.info(f"Retrieved models from Ollama: {payload}")

    return Tags(models=[Model(name=model["name"], model=model["model"]) for model in payload["models"]])

# Call Ollama /api/chat with the provided messages and model, and return the ChatResponse
# Based on Ollama API documentation
def call_local_ollama(
    messages: list[Message],
    model: str,
    think: bool = False,
    base_url: str = f"http://{ollama_host}:{ollama_port}",
    timeout: float = 120,
) -> ChatResponse:
    response = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
            "think": think,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    logger.info(f"Received response from Ollama: {payload}")
    return ChatResponse(model=payload["model"], message=payload["message"], done=payload.get("done", True), eval_count=payload.get("eval_count", 0))



# Tokens usage endpoint
@app.get("/api/token_usage", response_model=list[TokensResponse])
def get_token_usage():
    con = sqlite3.connect("tokens.db")
    cur = con.cursor()
    cur.execute("SELECT llm_id,llm_name, SUM(token_nb) AS total_tokens FROM tokens GROUP BY llm_name")
    rows = cur.fetchall()
    con.close()
    logger.info("Retrieved token usage")
    return [TokensResponse(llm_id=row[0], llm_name=row[1], total_tokens=row[2]) for row in rows]

# /api/chat endpoint to route user prompts to the configured Ollama server
# Can be latter extended to support multiple LLMs and routing based on the model name
@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest)-> ChatResponse:
    try:
        logger.info(f"Received chat request: {request}")

        response = call_local_ollama(request.messages, request.model, request.think)
        log_token_usage(llm_id=3, token_nb=response.eval_count, llm_name=request.model) 
        return response
    except Exception as e:
        logger.error(f"Error occurred while processing chat request: {str(e)}")
        return ChatResponse(model="", message="Failed to route or process the request.", done=True, eval_count=0)

# Insert token usage into the database
def log_token_usage(llm_id: int, token_nb: int, llm_name: str):
    con = sqlite3.connect("tokens.db")
    cur = con.cursor()
    cur.execute(
        "INSERT INTO tokens (llm_id, llm_name, date_exec, token_nb) VALUES (?, ?, datetime('now'), ?)",
        (llm_id, llm_name, token_nb)
    )
    con.commit()
    con.close()

def create_tokens_table():
    con = sqlite3.connect("tokens.db")
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tokens(
            llm_id INTEGER,
            llm_name TEXT,
            date_exec DATETIME,
            token_nb INTEGER
        )
    """)
    con.commit()
    con.close()
# This part is for testing and running the application
if __name__ == "__main__":
    create_tokens_table()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)