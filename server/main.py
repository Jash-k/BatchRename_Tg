import os
import asyncio
import logging
from typing import List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pyrogram import Client
from pyrogram.errors import FloodWait

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
class TaskState:
    is_running: bool = False
    progress: int = 0
    total: int = 0
    current_file: str = ""
    logs: List[str] = []
    should_stop: bool = False

state = TaskState()

class StartRequest(BaseModel):
    api_id: int
    api_hash: str
    session_string: str
    source_chat_id: str
    dest_chat_id: str
    filenames: List[str]
    start_index: int = 0

def add_log(message: str):
    print(message)
    state.logs.append(message)
    # Keep logs manageable
    if len(state.logs) > 1000:
        state.logs = state.logs[-1000:]

async def run_renaming_task(req: StartRequest):
    state.is_running = True
    state.should_stop = False
    state.progress = 0
    state.total = len(req.filenames)
    state.logs = []
    
    add_log("Starting renaming task...")
    
    # Create working directory for downloads
    # Use /tmp for ephemeral storage on Render/cloud envs
    work_dir = "/tmp/downloads"
    if not os.path.exists(work_dir):
        os.makedirs(work_dir)

    client = Client(
        "renamer_session",
        api_id=req.api_id,
        api_hash=req.api_hash,
        session_string=req.session_string,
        in_memory=True
    )

    try:
        await client.start()
        add_log("Connected to Telegram successfully.")

        try:
            source_chat = int(req.source_chat_id)
            dest_chat = int(req.dest_chat_id)
        except ValueError:
            add_log("Error: Chat IDs must be integers (e.g., -100123456789).")
            return

        add_log(f"Fetching messages from {source_chat}...")
        
        messages = []
        async for message in client.get_chat_history(source_chat):
            if state.should_stop:
                add_log("Task stopped by user.")
                break
            if message.video or message.document:
                messages.append(message)
        
        # Sort messages by ID (oldest first usually)
        messages.sort(key=lambda x: x.id)
        
        add_log(f"Found {len(messages)} media files in source channel.")
        add_log(f"Target filenames provided: {len(req.filenames)}")

        count = min(len(messages), len(req.filenames))
        
        # Adjust start index
        start_idx = req.start_index
        if start_idx >= count:
             add_log("Start index is greater than available files. Finished.")
             return

        for i in range(start_idx, count):
            if state.should_stop:
                add_log("Task stopped by user.")
                break

            state.progress = i + 1
            msg = messages[i]
            new_name = req.filenames[i]
            
            # Helper to get file extension
            original_media = msg.video or msg.document
            original_name = original_media.file_name or "unknown.mkv"
            
            if "." not in new_name:
                ext = os.path.splitext(original_name)[1]
                if not ext:
                    ext = ".mkv" # Default
                new_name += ext

            state.current_file = f"{original_name} -> {new_name}"
            add_log(f"Processing [{i+1}/{count}]: {original_name} -> {new_name}")

            try:
                # 1. Download
                add_log(f"Downloading {original_name}...")
                file_path = await client.download_media(msg, file_name=os.path.join(work_dir, new_name))
                
                # 2. Upload
                add_log(f"Uploading as {new_name}...")
                
                # Get thumbnail if exists
                thumb_path = None
                if msg.video and msg.video.thumbs:
                    thumb_path = await client.download_media(msg.video.thumbs[0].file_id, file_name=os.path.join(work_dir, "thumb.jpg"))

                await client.send_document(
                    chat_id=dest_chat,
                    document=file_path,
                    caption=new_name,
                    thumb=thumb_path,
                    force_document=True
                )

                # 3. Cleanup
                if os.path.exists(file_path):
                    os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)

                add_log(f"Successfully processed: {new_name}")

            except FloodWait as e:
                add_log(f"FloodWait: Sleeping for {e.value} seconds...")
                await asyncio.sleep(e.value)
                # Retry logic could be added here, but for now we skip or simple continue
            except Exception as e:
                add_log(f"Error processing {new_name}: {e}")

            # Brief pause
            await asyncio.sleep(2)

        add_log("Task completed.")

    except Exception as e:
        add_log(f"Critical Error: {e}")
    finally:
        state.is_running = False
        if client.is_connected:
            await client.stop()

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/api/start")
async def start_task(req: StartRequest, background_tasks: BackgroundTasks):
    if state.is_running:
        raise HTTPException(status_code=400, detail="Task is already running")
    
    background_tasks.add_task(run_renaming_task, req)
    return {"message": "Task started", "status": "running"}

@app.post("/api/stop")
async def stop_task():
    if not state.is_running:
        return {"message": "No task running"}
    state.should_stop = True
    return {"message": "Stop signal sent"}

@app.get("/api/status")
async def get_status():
    return {
        "is_running": state.is_running,
        "progress": state.progress,
        "total": state.total,
        "current_file": state.current_file,
        "logs": state.logs[-50:] # Return last 50 logs
    }

# SPA & Static Files Serving
# Determine where 'dist' is (root or one level up)
dist_dir = "dist"
if not os.path.exists(dist_dir) and os.path.exists("../dist"):
    dist_dir = "../dist"

if os.path.exists(dist_dir):
    # Mount /assets specifically if it exists to let StaticFiles handle it efficiently
    assets_dir = os.path.join(dist_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # Catch-all route for SPA
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Check if the file exists in dist (e.g., vite.svg, favicon.ico)
        file_path = os.path.join(dist_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        
        # Otherwise, return index.html for client-side routing
        return FileResponse(os.path.join(dist_dir, "index.html"))
else:
    logger.warning(f"Dist directory '{dist_dir}' not found. Frontend will not be served.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
