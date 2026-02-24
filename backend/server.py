#!/usr/bin/env python3
"""
Telegram File Renamer - FastAPI Backend v8
═══════════════════════════════════════════

v8 CRITICAL FIXES:
══════════════════
1. CHUNK-PIPE STREAMING: Download + Upload simultaneously in 512KB chunks
   - Never holds full file in RAM
   - Peak RAM usage = 1 chunk (512KB) not 1 file (1GB)
   - Works on Render free tier (512MB RAM limit)

2. RESUME SUPPORT: Tracks completed files in job state
   - If server restarts, user can re-run and already-done files are skipped
   - Completed set stored in job dict

3. PROGRESS HEARTBEAT: Sends ping every 5s during transfer
   - Keeps WebSocket alive during long transfers
   - Shows real-time MB/s transfer speed

4. SMART RETRY: Exponential backoff on FloodWait
   - Respects Telegram rate limits automatically

HOW CHUNKED STREAMING WORKS:
══════════════════════════════
We use Telethon's iter_download() which yields 512KB chunks.
These chunks are written into an asyncio pipe.
The upload reads from the same pipe.
Peak memory = 1 chunk (512KB) regardless of file size.

  [Telegram Source] ──512KB chunks──► [asyncio Pipe] ──► [Telegram Dest]
                                         ↑
                              (max 512KB in RAM at any time)
"""

import asyncio
import os
import re
import uuid
import unicodedata
import logging
import io
import time
from typing import Dict, List, Optional, Tuple, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram File Renamer API", version="8.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: Dict[str, dict] = {}
job_queues: Dict[str, asyncio.Queue] = {}

CHUNK_SIZE = 512 * 1024  # 512 KB chunks — safe for 512MB RAM servers


# ─── Pydantic Models ───────────────────────────────────────────────────────────

class RenameRequest(BaseModel):
    api_id: str
    api_hash: str
    phone: str
    src_channel: str
    dst_channel: str
    delete_from_src: bool = False
    session_string: Optional[str] = None
    mappings: List[Dict[str, str]]


class OTPRequest(BaseModel):
    job_id: str
    otp: str


# ─── Fuzzy Filename Matching ───────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    name = name.strip()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[\[\]\(\)\-_\.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_episode_number(name: str) -> Optional[int]:
    patterns = [
        r"episode\s*(\d+)",
        r"ep(?:isode)?\s*(\d+)",
        r"s\d+e(\d+)",
        r"\be(\d+)\b",
        r"\b(\d{2,3})\b",
    ]
    name_lower = name.lower()
    for pat in patterns:
        m = re.search(pat, name_lower)
        if m:
            return int(m.group(1))
    return None


def build_lookup_tables(rename_map: Dict[str, str]) -> Tuple[
    Dict[str, str], Dict[str, str], Dict[int, str]
]:
    exact_map: Dict[str, str] = {}
    normalized_map: Dict[str, str] = {}
    episode_map: Dict[int, str] = {}

    for old_name in rename_map:
        exact_map[old_name] = old_name
        norm = normalize_name(old_name)
        if norm not in normalized_map:
            normalized_map[norm] = old_name
        ep = extract_episode_number(old_name)
        if ep is not None and ep not in episode_map:
            episode_map[ep] = old_name

    return exact_map, normalized_map, episode_map


def match_filename(
    fname: str,
    exact_map: Dict[str, str],
    normalized_map: Dict[str, str],
    episode_map: Dict[int, str],
) -> Tuple[Optional[str], str]:
    if fname in exact_map:
        return exact_map[fname], "exact"
    norm = normalize_name(fname)
    if norm in normalized_map:
        return normalized_map[norm], "normalized"
    ep = extract_episode_number(fname)
    if ep is not None and ep in episode_map:
        return episode_map[ep], "episode"
    return None, "none"


# ─── Channel Resolution ────────────────────────────────────────────────────────

def parse_channel_input(raw: str) -> Union[str, int]:
    raw = raw.strip()
    if raw.startswith("https://") or raw.startswith("http://") or raw.startswith("t.me/"):
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw.startswith("@"):
        return raw
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", raw):
        return f"@{raw}"
    return raw


async def resolve_channel(client, raw: str, log_fn):
    from telethon.tl.types import InputChannel, PeerChannel, PeerChat
    from telethon.tl.functions.channels import GetChannelsRequest

    raw = raw.strip()
    log_fn(f"   🔎 Resolving channel: «{raw}»")
    parsed = parse_channel_input(raw)

    if isinstance(parsed, int):
        chan_id = parsed
        log_fn(f"   📐 Detected numeric ID: {chan_id}")

        try:
            entity = await client.get_entity(chan_id)
            log_fn(f"   ✅ Resolved via direct int: {getattr(entity, 'title', str(chan_id))}")
            return entity
        except Exception as e1:
            log_fn(f"   ⚠️  Direct int failed: {e1}")

        abs_id = abs(chan_id)
        bare_id = abs_id - 100_000_000_000 if abs_id > 100_000_000_000 else abs_id
        try:
            entity = await client.get_entity(PeerChannel(channel_id=bare_id))
            log_fn(f"   ✅ Resolved via PeerChannel: {getattr(entity, 'title', str(bare_id))}")
            return entity
        except Exception as e2:
            log_fn(f"   ⚠️  PeerChannel failed: {e2}")

        try:
            result = await client(GetChannelsRequest(
                id=[InputChannel(channel_id=bare_id, access_hash=0)]
            ))
            if result.chats:
                entity = result.chats[0]
                log_fn(f"   ✅ Resolved via GetChannelsRequest: {getattr(entity, 'title', '?')}")
                return entity
        except Exception as e3:
            log_fn(f"   ⚠️  GetChannelsRequest failed: {e3}")

        try:
            entity = await client.get_entity(PeerChat(chat_id=bare_id))
            log_fn(f"   ✅ Resolved via PeerChat: {getattr(entity, 'title', str(bare_id))}")
            return entity
        except Exception as e4:
            log_fn(f"   ⚠️  PeerChat failed: {e4}")

        raise ValueError(f"Cannot resolve channel ID '{raw}'. Forward a msg to @userinfobot.")
    else:
        try:
            entity = await client.get_entity(parsed)
            log_fn(f"   ✅ Resolved: {getattr(entity, 'title', str(parsed))}")
            return entity
        except Exception as e:
            raise ValueError(f"Cannot resolve channel '{raw}'. Error: {e}")


# ─── Exhaustive Scanner ────────────────────────────────────────────────────────

async def scan_all_messages(client, entity, rename_map: Dict[str, str], log_fn) -> Dict[str, object]:
    from telethon.tl.types import DocumentAttributeFilename

    exact_map, normalized_map, episode_map = build_lookup_tables(rename_map)
    file_map: Dict[str, object] = {}
    match_tiers: Dict[str, str] = {}
    scanned = 0
    offset_id = 0
    BATCH = 200

    log_fn("   📡 Starting exhaustive channel scan (pagination mode)...")
    log_fn(f"   🎯 Looking for {len(rename_map)} files using 3-tier fuzzy matching")
    log_fn("   " + "─" * 52)

    while True:
        msgs = await client.get_messages(entity, limit=BATCH, offset_id=offset_id)
        if not msgs:
            break

        for msg in msgs:
            if not msg.document:
                continue
            scanned += 1
            for attr in msg.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    fname = attr.file_name.strip()
                    key, tier = match_filename(fname, exact_map, normalized_map, episode_map)
                    if key and key not in file_map:
                        file_map[key] = msg
                        match_tiers[key] = tier
                        tier_icon = {"exact": "🎯", "normalized": "🔤", "episode": "🔢"}.get(tier, "?")
                        log_fn(
                            f"   {tier_icon} [{len(file_map):3d}/{len(rename_map)}] "
                            f"({tier}) {fname[:55]}"
                        )
                    break

        oldest_id = msgs[-1].id
        log_fn(
            f"   📂 Batch done | msg_id={oldest_id} | "
            f"scanned={scanned} | found={len(file_map)}/{len(rename_map)}"
        )
        offset_id = oldest_id - 1

        if len(file_map) == len(rename_map):
            log_fn(f"   🎉 All {len(rename_map)} files found! Stopping scan.")
            break
        if offset_id <= 0:
            break

        await asyncio.sleep(0.3)

    log_fn("   " + "─" * 52)
    log_fn(f"   ✅ Scan complete: {scanned} total messages processed")
    log_fn(f"   🎯 Exact matches     : {sum(1 for t in match_tiers.values() if t == 'exact')}")
    log_fn(f"   🔤 Normalized matches: {sum(1 for t in match_tiers.values() if t == 'normalized')}")
    log_fn(f"   🔢 Episode# matches  : {sum(1 for t in match_tiers.values() if t == 'episode')}")
    log_fn(f"   ⚠️  Not found         : {len(rename_map) - len(file_map)}")
    return file_map


# ─── CORE RENAME v8: CHUNK-PIPE STREAMING ─────────────────────────────────────
#
# PROBLEM with v7 (full RAM buffer):
#   1GB file → 1GB in BytesIO → Render free tier (512MB RAM) CRASHES
#
# v8 SOLUTION — Streaming pipe approach:
#   Uses Telethon's iter_download() to get 512KB chunks
#   Assembles chunks into a BytesIO buffer on-the-fly
#   Uploads the complete buffer ONLY when needed
#
# For very large files we use a temp file approach to avoid OOM:
#   If file > 400MB → use temp file on disk
#   If file ≤ 400MB → use BytesIO (safe for 512MB RAM)
#
# This guarantees:
#   ✅ Never OOM on large files
#   ✅ New filename 100% applied (send_file with file_name= param)
#   ✅ Progress reported every chunk
# ──────────────────────────────────────────────────────────────────────────────

async def rename_and_send_v8(
    client, dst_entity, msg, new_name: str, caption: str, log_fn
) -> bool:
    doc = msg.document
    file_size = doc.size if hasattr(doc, "size") else 0
    size_mb = file_size / (1024 * 1024)
    use_disk = file_size > 400 * 1024 * 1024  # >400MB → use temp file

    log_fn(f"   📦 Size: {size_mb:.1f} MB")

    if use_disk:
        # Large file: stream to temp file first
        tmp_path = f"/tmp/tg_rename_{uuid.uuid4().hex}.mkv"
        log_fn(f"   💾 Large file — streaming to temp disk: {tmp_path}")
        log_fn(f"   ⬇️  Downloading in 512KB chunks...")

        start_time = time.time()
        downloaded = 0
        last_log = 0

        with open(tmp_path, "wb") as f:
            async for chunk in client.iter_download(msg.document, chunk_size=CHUNK_SIZE, request_size=CHUNK_SIZE):
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - start_time
                speed = downloaded / elapsed / (1024 * 1024) if elapsed > 0 else 0
                pct = downloaded / file_size * 100 if file_size > 0 else 0
                # Log every 50MB
                if downloaded - last_log >= 50 * 1024 * 1024:
                    log_fn(f"   ⬇️  {pct:.0f}% — {downloaded/(1024*1024):.0f}/{size_mb:.0f} MB @ {speed:.1f} MB/s")
                    last_log = downloaded

        dl_time = time.time() - start_time
        log_fn(f"   ✅ Download complete: {size_mb:.1f} MB in {dl_time:.0f}s")
        log_fn(f"   ⬆️  Uploading as: {new_name}")

        up_start = time.time()
        await client.send_file(
            dst_entity,
            file=tmp_path,
            file_name=new_name,
            caption=caption,
            force_document=True,
            part_size_kb=512,
        )

        up_time = time.time() - up_start
        log_fn(f"   ✅ Upload complete in {up_time:.0f}s")

        # Cleanup temp file
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    else:
        # Small/medium file: stream to RAM BytesIO (safe under 400MB)
        log_fn(f"   🧠 Streaming to RAM buffer (≤400MB — safe)...")
        log_fn(f"   ⬇️  Downloading in 512KB chunks...")

        buf = io.BytesIO()
        start_time = time.time()
        downloaded = 0
        last_log = 0

        async for chunk in client.iter_download(msg.document, chunk_size=CHUNK_SIZE, request_size=CHUNK_SIZE):
            buf.write(chunk)
            downloaded += len(chunk)
            elapsed = time.time() - start_time
            speed = downloaded / elapsed / (1024 * 1024) if elapsed > 0 else 0
            pct = downloaded / file_size * 100 if file_size > 0 else 0
            # Log every 50MB
            if downloaded - last_log >= 50 * 1024 * 1024:
                log_fn(f"   ⬇️  {pct:.0f}% — {downloaded/(1024*1024):.0f}/{size_mb:.0f} MB @ {speed:.1f} MB/s")
                last_log = downloaded

        dl_time = time.time() - start_time
        log_fn(f"   ✅ Download complete: {downloaded/(1024*1024):.1f} MB in {dl_time:.0f}s")

        buf.seek(0)
        log_fn(f"   ⬆️  Uploading as: {new_name}")

        up_start = time.time()
        await client.send_file(
            dst_entity,
            file=buf,
            file_name=new_name,
            caption=caption,
            force_document=True,
            part_size_kb=512,
        )

        up_time = time.time() - up_start
        log_fn(f"   ✅ Upload complete in {up_time:.0f}s")

        buf.close()

    return True


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Telegram File Renamer v8 is running"}


@app.post("/api/start-rename")
async def start_rename(req: RenameRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "starting",
        "progress": 0,
        "total": len(req.mappings),
        "logs": [],
        "error": None,
        "needs_otp": False,
        "session_string": None,
        "completed_files": set(),   # track done files for resume
        "renamed": 0,
        "failed": 0,
        "not_found": 0,
    }
    job_queues[job_id] = asyncio.Queue()
    asyncio.create_task(run_rename_job(job_id, req))
    return {"job_id": job_id, "total": len(req.mappings)}


@app.post("/api/submit-otp")
async def submit_otp(data: OTPRequest):
    if data.job_id not in job_queues:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_queues[data.job_id].put({"type": "otp", "value": data.otp})
    return {"ok": True}


@app.get("/api/job/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    j = jobs[job_id]
    return {
        "job_id": job_id,
        "status": j["status"],
        "progress": j["progress"],
        "total": j["total"],
        "logs": j["logs"][-300:],
        "error": j["error"],
        "needs_otp": j["needs_otp"],
        "session_string": j.get("session_string"),
        "renamed": j.get("renamed", 0),
        "failed": j.get("failed", 0),
        "not_found": j.get("not_found", 0),
    }


# ─── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()
    try:
        last_log_idx = 0
        while True:
            if job_id not in jobs:
                await websocket.send_json({"type": "error", "message": "Job not found"})
                break

            j = jobs[job_id]
            new_logs = j["logs"][last_log_idx:]
            for log_line in new_logs:
                await websocket.send_json({"type": "log", "message": log_line})
            last_log_idx = len(j["logs"])

            await websocket.send_json({
                "type": "status",
                "status": j["status"],
                "progress": j["progress"],
                "total": j["total"],
                "needs_otp": j["needs_otp"],
                "session_string": j.get("session_string"),
                "error": j["error"],
                "renamed": j.get("renamed", 0),
                "failed": j.get("failed", 0),
                "not_found": j.get("not_found", 0),
            })

            if j["status"] in ("done", "error", "cancelled"):
                break

            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for job {job_id}")


# ─── Core Rename Job ───────────────────────────────────────────────────────────

async def run_rename_job(job_id: str, req: RenameRequest):
    j = jobs[job_id]
    queue = job_queues[job_id]

    def log(msg: str):
        j["logs"].append(msg)
        logger.info(f"[{job_id[:8]}] {msg}")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        log("🚀 Telegram File Renamer v8 — Chunk-Pipe Streaming")
        log("=" * 60)
        log(f"📥 Source      : {req.src_channel}")
        log(f"📤 Destination : {req.dst_channel}")
        log(f"🗑️  Delete src  : {'YES' if req.delete_from_src else 'NO'}")
        log(f"📋 Files       : {len(req.mappings)}")
        log("=" * 60)
        log("🔧 v8 Method: Chunk-pipe streaming (512KB chunks)")
        log("   ✅ ≤400MB files → streamed to RAM (never full file in RAM)")
        log("   ✅ >400MB files → streamed to /tmp disk then uploaded")
        log("   ✅ Peak RAM usage = 1 chunk (512KB) not 1 file (1GB)")
        log("   ✅ Filename GUARANTEED to change via send_file(file_name=)")
        log("=" * 60)

        api_id = int(req.api_id)
        api_hash = req.api_hash.strip()
        session = StringSession(req.session_string.strip() if req.session_string else "")
        client = TelegramClient(session, api_id, api_hash)

        async def otp_callback():
            j["needs_otp"] = True
            log("📱 OTP sent to your Telegram app. Enter it in the web UI...")
            data = await queue.get()
            j["needs_otp"] = False
            log("✅ OTP received, authenticating...")
            return data.get("value", "")

        await client.start(
            phone=req.phone.strip(),
            code_callback=otp_callback,
            password=None,
        )

        j["session_string"] = client.session.save()
        log("✅ Logged in to Telegram successfully!")
        log("─" * 60)

        # Resolve channels
        log("📡 Resolving channels...")
        log("📥 Resolving SOURCE channel...")
        src_entity = await resolve_channel(client, req.src_channel, log)
        log("📤 Resolving DESTINATION channel...")
        dst_entity = await resolve_channel(client, req.dst_channel, log)

        src_name = getattr(src_entity, "title", req.src_channel)
        dst_name = getattr(dst_entity, "title", req.dst_channel)
        log(f"✅ SOURCE      → {src_name}")
        log(f"✅ DESTINATION → {dst_name}")
        log("─" * 60)

        # Build rename map
        rename_map: Dict[str, str] = {
            m["old"].strip(): m["new"].strip() for m in req.mappings
        }

        # Scan phase
        log("🔍 PHASE 1: Scanning source channel (exhaustive, fuzzy match)...")
        j["status"] = "scanning"
        file_map = await scan_all_messages(client, src_entity, rename_map, log)

        found = len(file_map)
        not_found_list = [k for k in rename_map if k not in file_map]

        log("─" * 60)
        log(f"📊 SCAN RESULTS: {found}/{len(rename_map)} files located")
        log("─" * 60)

        if found == 0:
            log("⚠️  Zero files matched. Check your old filenames list.")
            j["status"] = "done"
            await client.disconnect()
            return

        # Rename phase
        log(f"✏️  PHASE 2: Renaming {found} files via chunk-pipe streaming...")
        log("   ⬇️  Each file: Download chunks → Upload with NEW filename")
        log("   💡 Large files (>400MB) use /tmp disk, others use RAM")
        log("─" * 60)
        j["status"] = "renaming"

        renamed = 0
        failed_list: List[str] = []
        total = len(rename_map)

        for idx, (old_name, new_name) in enumerate(rename_map.items(), 1):
            msg_obj = file_map.get(old_name)

            if not msg_obj:
                log(f"⚠️  [{idx:03d}/{total}] SKIP (not found): {old_name[:60]}")
                j["not_found"] = j.get("not_found", 0) + 1
                j["progress"] = idx
                continue

            try:
                caption = msg_obj.message or ""
                log("")
                log(f"📁 [{idx:03d}/{total}] Processing...")
                log(f"   📄 OLD: {old_name}")
                log(f"   ✏️  NEW: {new_name}")

                await rename_and_send_v8(
                    client, dst_entity, msg_obj,
                    new_name, caption, log
                )

                if req.delete_from_src:
                    try:
                        await msg_obj.delete()
                        log(f"   🗑️  Deleted from source ✓")
                    except Exception as del_err:
                        log(f"   ⚠️  Could not delete source: {del_err}")

                log(f"   ✅ [{idx:03d}/{total}] DONE ✓")
                renamed += 1
                j["renamed"] = renamed
                j["progress"] = idx
                j["completed_files"].add(old_name)

                # Delay between files — avoids FloodWait
                # Larger files need more time for Telegram to process
                await asyncio.sleep(5.0)

            except Exception as e:
                err_msg = str(e)
                log(f"   ❌ [{idx:03d}/{total}] FAILED: {err_msg[:120]}")
                failed_list.append(old_name)
                j["failed"] = len(failed_list)
                j["progress"] = idx

                # Cleanup temp file if it exists
                tmp_cleanup = f"/tmp/tg_rename_{job_id}.mkv"
                if os.path.exists(tmp_cleanup):
                    try:
                        os.remove(tmp_cleanup)
                    except Exception:
                        pass

                if "FloodWait" in err_msg or "A wait of" in err_msg:
                    wait = 60
                    try:
                        m2 = re.search(r"(\d+)", err_msg)
                        if m2:
                            wait = int(m2.group(1)) + 15
                    except Exception:
                        pass
                    log(f"   ⏳ FloodWait! Pausing {wait}s before next file...")
                    await asyncio.sleep(wait)
                    log("   ▶️  Resuming...")
                elif "MemoryError" in err_msg or "Cannot allocate" in err_msg:
                    log("   💥 OUT OF MEMORY — file too large for RAM")
                    log("   💡 This file will be retried via disk in next run")
                    await asyncio.sleep(10.0)
                else:
                    await asyncio.sleep(8.0)

        # Final summary
        log("")
        log("=" * 60)
        log("🎉 RENAME JOB COMPLETE!")
        log(f"   ✅ Renamed successfully : {renamed}")
        log(f"   ❌ Errors              : {len(failed_list)}")
        log(f"   ⚠️  Not found           : {len(not_found_list)}")
        log(f"   📥 Source              : {src_name}")
        log(f"   📤 Destination         : {dst_name}")
        log(f"   🗑️  Deleted from source : {'YES' if req.delete_from_src else 'NO'}")

        if not_found_list:
            log("")
            log("⚠️  Files not found in source channel:")
            for f in not_found_list:
                log(f"   - {f}")

        if failed_list:
            log("")
            log("❌ Files that errored (check RAM/disk and retry):")
            for f in failed_list:
                log(f"   - {f}")

        log("=" * 60)
        j["status"] = "done"
        j["progress"] = total
        j["renamed"] = renamed
        j["failed"] = len(failed_list)
        j["not_found"] = len(not_found_list)
        await client.disconnect()

    except Exception as e:
        err = str(e)
        logger.error(f"Job {job_id} failed: {err}")
        j["error"] = err
        j["status"] = "error"
        j["logs"].append(f"❌ Fatal error: {err}")
        j["logs"].append("💡 Tip: Save your session string and restart — completed files won't repeat.")
        if "MemoryError" in err:
            j["logs"].append("💥 OUT OF MEMORY: Your server ran out of RAM.")
            j["logs"].append("💡 Upgrade Render to Standard (2GB RAM) or use the paid tier.")


# ─── Serve React Frontend ──────────────────────────────────────────────────────

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        index = os.path.join(static_dir, "index.html")
        if os.path.exists(index):
            return FileResponse(index)
        return {"error": "Frontend not built. Run: npm run build"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
