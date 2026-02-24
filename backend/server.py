#!/usr/bin/env python3
"""
Telegram File Renamer - FastAPI Backend v3
Renames files from SOURCE channel → DESTINATION channel without downloading/uploading.
Handles ALL channel ID formats: @username, -100xxx, t.me/joinchat/xxx, plain int, etc.
"""

import asyncio
import os
import re
import uuid
import logging
from typing import Dict, List, Optional, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram File Renamer API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: Dict[str, dict] = {}
job_queues: Dict[str, asyncio.Queue] = {}


# ─── Pydantic Models ──────────────────────────────────────────────────────────

class RenameRequest(BaseModel):
    api_id: str
    api_hash: str
    phone: str
    src_channel: str           # Source channel (where files currently live)
    dst_channel: str           # Destination channel (where renamed files go)
    delete_from_src: bool = False  # Whether to delete originals from source
    session_string: Optional[str] = None
    mappings: List[Dict[str, str]]  # [{"old": "...", "new": "..."}]


class OTPRequest(BaseModel):
    job_id: str
    otp: str


# ─── Channel ID Resolution ────────────────────────────────────────────────────

def parse_channel_input(raw: str) -> Union[str, int]:
    """
    Robustly parse any channel identifier into what Telethon's get_entity() accepts.

    Supported formats:
      • @username              → str  "@username"
      • username               → str  "@username"  (no @)
      • -1001234567890         → int  -1001234567890   (supergroup/channel with -100 prefix)
      • -1003557121488         → int  (same, even if it looks odd)
      • 1234567890             → int  (bare positive id — Telethon adds -100 internally)
      • t.me/channelname       → str  pass-through
      • t.me/joinchat/hash     → str  pass-through  (invite link)
      • https://t.me/...       → str  pass-through
    """
    raw = raw.strip()

    # 1. Invite / t.me links — pass straight through to get_entity()
    if raw.startswith("https://") or raw.startswith("http://") or raw.startswith("t.me/"):
        return raw

    # 2. Numeric IDs (with optional leading minus)
    #    Examples: -1003557121488  |  -1001234567890  |  1234567890
    if re.fullmatch(r"-?\d+", raw):
        num = int(raw)
        # Telethon's get_entity() needs the REAL peer ID, not the -100 prefixed one.
        # For channels/supergroups the actual channel_id = abs(-100xxx) - 100_000_000_000
        # But get_entity(int) with the FULL -100xxx value DOES work when you use
        # the PeerChannel constructor.  The safest approach: use PeerChannel if needed.
        return num   # handled specially in resolve_channel() below

    # 3. @username or bare username
    if raw.startswith("@"):
        return raw  # already has @
    # bare word that looks like a username (letters/digits/underscores)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", raw):
        return f"@{raw}"

    # 4. Fallback — let Telethon try
    return raw


async def resolve_channel(client, raw: str, log_fn):
    """
    Resolve a channel string/ID to a Telethon entity, with multiple fallback strategies.
    Logs each attempt so the user can see what's happening.
    """
    from telethon.tl.types import (
        InputChannel, PeerChannel, PeerChat, Channel, Chat
    )
    from telethon.tl.functions.channels import GetChannelsRequest
    from telethon.errors import (
        ChannelPrivateError, UsernameNotOccupiedError,
        ChatIdInvalidError, PeerIdInvalidError
    )

    raw = raw.strip()
    log_fn(f"   🔎 Resolving channel: «{raw}»")

    # ── Strategy 1: parse & try get_entity directly ──────────────────────────
    parsed = parse_channel_input(raw)

    if isinstance(parsed, int):
        chan_id = parsed
        log_fn(f"   📐 Detected numeric ID: {chan_id}")

        # Telethon needs the bare channel_id (without -100 prefix) for PeerChannel
        # Full -100xxxxxxxxxx → bare id = abs(chan_id) - 100_000_000_000
        # Plain negative (group) → use PeerChat

        try:
            # Try direct get_entity with the int first (works if already cached)
            entity = await client.get_entity(chan_id)
            log_fn(f"   ✅ Resolved via direct int: {getattr(entity, 'title', str(chan_id))}")
            return entity
        except Exception as e1:
            log_fn(f"   ⚠️  Direct int failed: {e1}")

        # Extract bare channel id from -100xxxxxxxxxx format
        abs_id = abs(chan_id)
        if abs_id > 100_000_000_000:
            bare_id = abs_id - 100_000_000_000
        else:
            bare_id = abs_id

        log_fn(f"   📐 Trying PeerChannel(id={bare_id}) ...")
        try:
            peer = PeerChannel(channel_id=bare_id)
            entity = await client.get_entity(peer)
            log_fn(f"   ✅ Resolved via PeerChannel: {getattr(entity, 'title', str(bare_id))}")
            return entity
        except Exception as e2:
            log_fn(f"   ⚠️  PeerChannel failed: {e2}")

        # Try GetChannelsRequest with InputChannel
        log_fn(f"   📐 Trying GetChannelsRequest with access_hash=0 ...")
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

        # Try as group chat (PeerChat) — for basic groups
        log_fn(f"   📐 Trying PeerChat(id={bare_id}) ...")
        try:
            peer_chat = PeerChat(chat_id=bare_id)
            entity = await client.get_entity(peer_chat)
            log_fn(f"   ✅ Resolved via PeerChat: {getattr(entity, 'title', str(bare_id))}")
            return entity
        except Exception as e4:
            log_fn(f"   ⚠️  PeerChat failed: {e4}")

        raise ValueError(
            f"Cannot resolve channel ID '{raw}'.\n"
            f"Tried: direct int, PeerChannel({bare_id}), GetChannelsRequest, PeerChat.\n"
            f"Make sure:\n"
            f"  1. You are a MEMBER of this channel\n"
            f"  2. The ID is correct (forward a message to @userinfobot to confirm)\n"
            f"  3. Use @username instead of the numeric ID if possible"
        )

    else:
        # String (username or link)
        log_fn(f"   📐 Trying as username/link: {parsed}")
        try:
            entity = await client.get_entity(parsed)
            log_fn(f"   ✅ Resolved: {getattr(entity, 'title', str(parsed))}")
            return entity
        except Exception as e:
            log_fn(f"   ⚠️  Username resolution failed: {e}")
            raise ValueError(
                f"Cannot resolve channel '{raw}'.\n"
                f"Error: {e}\n"
                f"Tips:\n"
                f"  • Use format: @username or -100xxxxxxxxxx\n"
                f"  • Make sure you are a member of this channel\n"
                f"  • For private channels, use the numeric ID from @userinfobot"
            )


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Telegram File Renamer v3 is running"}


@app.post("/api/start-rename")
async def start_rename(req: RenameRequest):
    """Start a rename job. Returns job_id."""
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "starting",
        "progress": 0,
        "total": len(req.mappings),
        "logs": [],
        "error": None,
        "needs_otp": False,
        "session_string": None,
    }
    job_queues[job_id] = asyncio.Queue()
    asyncio.create_task(run_rename_job(job_id, req))
    return {"job_id": job_id, "total": len(req.mappings)}


@app.post("/api/submit-otp")
async def submit_otp(data: OTPRequest):
    """Submit OTP for a job waiting for Telegram authentication."""
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
        "logs": j["logs"][-200:],
        "error": j["error"],
        "needs_otp": j["needs_otp"],
        "session_string": j.get("session_string"),
    }


# ─── WebSocket (live log streaming) ──────────────────────────────────────────

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

            # Stream any new log lines
            new_logs = j["logs"][last_log_idx:]
            for log_line in new_logs:
                await websocket.send_json({"type": "log", "message": log_line})
            last_log_idx = len(j["logs"])

            # Send status update
            await websocket.send_json({
                "type": "status",
                "status": j["status"],
                "progress": j["progress"],
                "total": j["total"],
                "needs_otp": j["needs_otp"],
                "session_string": j.get("session_string"),
                "error": j["error"],
            })

            if j["status"] in ("done", "error", "cancelled"):
                break

            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for job {job_id}")


# ─── Core Rename Job ──────────────────────────────────────────────────────────

async def run_rename_job(job_id: str, req: RenameRequest):
    """
    Main async worker:
      1. Login to Telegram (with OTP via WebSocket if needed)
      2. Resolve both channels with multiple fallback strategies
      3. Scan SOURCE channel for matching filenames
      4. For each match: send_file() to DESTINATION with new name (no re-upload)
      5. Optionally delete original from SOURCE
    """
    j = jobs[job_id]
    queue = job_queues[job_id]

    def log(msg: str):
        j["logs"].append(msg)
        logger.info(f"[{job_id[:8]}] {msg}")

    try:
        from telethon import TelegramClient
        from telethon.tl.types import DocumentAttributeFilename
        from telethon.sessions import StringSession

        log("🚀 Starting Telegram File Renamer v3...")
        log(f"📥 Source channel  : {req.src_channel}")
        log(f"📤 Destination     : {req.dst_channel}")
        log(f"🗑️  Delete source   : {'YES' if req.delete_from_src else 'NO'}")
        log(f"📋 Files to rename : {len(req.mappings)}")
        log("─" * 50)

        api_id = int(req.api_id)
        api_hash = req.api_hash.strip()

        session = StringSession(req.session_string.strip() if req.session_string else "")
        client = TelegramClient(session, api_id, api_hash)

        # OTP callback — prompts browser UI via WebSocket flag
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

        # Save session string immediately after login
        j["session_string"] = client.session.save()
        log("✅ Logged in to Telegram successfully!")

        # ── Resolve both channels with smart fallback ─────────────────────
        log("─" * 50)
        log("📡 Resolving channels...")

        try:
            log("📥 Resolving SOURCE channel...")
            src_entity = await resolve_channel(client, req.src_channel, log)
        except ValueError as e:
            log(f"❌ Failed to resolve SOURCE channel: {req.src_channel}")
            log(str(e))
            raise

        try:
            log("📤 Resolving DESTINATION channel...")
            dst_entity = await resolve_channel(client, req.dst_channel, log)
        except ValueError as e:
            log(f"❌ Failed to resolve DESTINATION channel: {req.dst_channel}")
            log(str(e))
            raise

        src_name = getattr(src_entity, "title", req.src_channel)
        dst_name = getattr(dst_entity, "title", req.dst_channel)

        log(f"✅ SOURCE      → {src_name}")
        log(f"✅ DESTINATION → {dst_name}")
        log("─" * 50)
        log("🔍 Scanning source channel for matching files...")
        log("   (This may take 1–2 minutes for large channels)")

        j["status"] = "scanning"

        # Build rename map: old_name → new_name
        rename_map: Dict[str, str] = {m["old"].strip(): m["new"].strip() for m in req.mappings}

        # Scan source channel messages and collect matching ones
        file_map: Dict[str, object] = {}  # old_name → message object
        scanned = 0

        async for msg in client.iter_messages(src_entity, limit=None):
            if not msg.document:
                continue
            scanned += 1
            for attr in msg.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    fname = attr.file_name.strip()
                    if fname in rename_map and fname not in file_map:
                        file_map[fname] = msg
                        log(f"   🎯 Found [{len(file_map)}/{len(rename_map)}]: {fname[:60]}...")
                    break

            # Log scan progress every 100 messages
            if scanned % 100 == 0:
                log(f"   📂 Scanned {scanned} messages, found {len(file_map)}/{len(rename_map)} so far...")

            # Early exit if all found
            if len(file_map) == len(rename_map):
                log(f"   🎉 All {len(rename_map)} files found! Stopping scan early.")
                break

        found = len(file_map)
        not_found_count = len(rename_map) - found
        log(f"✅ Scan complete! Scanned {scanned} messages total.")
        log(f"   Found    : {found} / {len(rename_map)} files")
        if not_found_count > 0:
            log(f"   Missing  : {not_found_count} files (will be skipped)")
        log("─" * 50)

        if found == 0:
            log("⚠️  No matching files found in source channel.")
            log("   → Check filenames are EXACT (case-sensitive, spaces/dots included)")
            log("   → Try copying the filename directly from Telegram")
            j["status"] = "done"
            await client.disconnect()
            return

        j["status"] = "renaming"
        renamed = 0
        failed: List[str] = []
        not_found_list: List[str] = []
        total = len(rename_map)

        for idx, (old_name, new_name) in enumerate(rename_map.items(), 1):
            msg = file_map.get(old_name)

            if not msg:
                log(f"⚠️  [{idx:03d}/{total}] NOT FOUND: {old_name}")
                not_found_list.append(old_name)
                j["progress"] = idx
                continue

            try:
                # Build new attributes — replace only DocumentAttributeFilename
                new_attrs = []
                for attr in msg.document.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        new_attrs.append(DocumentAttributeFilename(file_name=new_name))
                    else:
                        new_attrs.append(attr)

                # ── KEY: send_file with existing document — ZERO BYTES transferred ──
                # Telegram deduplicates by file_id, so this is instant on Telegram servers.
                await client.send_file(
                    dst_entity,            # ← send to DESTINATION channel
                    file=msg.document,     # ← reuse existing file (no upload/download)
                    attributes=new_attrs,  # ← with the new filename
                    caption=msg.message or "",
                    force_document=True,
                )

                # Delete from source if requested
                if req.delete_from_src:
                    await msg.delete()
                    log(f"✅ [{idx:03d}/{total}] Renamed + deleted from source")
                else:
                    log(f"✅ [{idx:03d}/{total}] Renamed → {dst_name}")

                log(f"   📄 OLD: {old_name}")
                log(f"   ✏️  NEW: {new_name}")

                renamed += 1
                j["progress"] = idx

                # Rate limit protection — Telegram allows ~20 sends/min
                await asyncio.sleep(2.0)

            except Exception as e:
                err_msg = str(e)
                log(f"❌ [{idx:03d}/{total}] ERROR renaming: {old_name}")
                log(f"   Reason: {err_msg}")
                failed.append(old_name)
                j["progress"] = idx

                # Handle Telegram FloodWait automatically
                if "FloodWait" in err_msg or "A wait of" in err_msg:
                    wait = 60
                    try:
                        match = re.search(r"(\d+)", err_msg)
                        if match:
                            wait = int(match.group(1)) + 10
                    except Exception:
                        pass
                    log(f"⏳ FloodWait! Telegram says wait {wait}s. Pausing...")
                    await asyncio.sleep(wait)
                    log("▶️  Resuming after FloodWait...")

        # ── Summary ──────────────────────────────────────────────────────────
        log("─" * 50)
        log("🎉 RENAME JOB COMPLETE!")
        log(f"   ✅ Renamed successfully : {renamed}")
        log(f"   ❌ Errors              : {len(failed)}")
        log(f"   ⚠️  Not found           : {len(not_found_list)}")
        log(f"   📥 Source              : {src_name}")
        log(f"   📤 Destination         : {dst_name}")
        log(f"   🗑️  Deleted from source  : {'YES' if req.delete_from_src else 'NO'}")

        if failed:
            log("\n❌ Failed files:")
            for f in failed:
                log(f"   - {f}")

        if not_found_list:
            log("\n⚠️  Files not found in source channel:")
            for f in not_found_list:
                log(f"   - {f}")

        log("─" * 50)
        log("💾 Save your session string from the green box — skip OTP next time!")

        j["status"] = "done"
        j["progress"] = total
        await client.disconnect()

    except Exception as e:
        err = str(e)
        logger.error(f"Job {job_id} failed: {err}")
        j["error"] = err
        j["status"] = "error"
        j["logs"].append(f"❌ Fatal error: {err}")
        j["logs"].append("")
        j["logs"].append("💡 Common fixes:")
        j["logs"].append("   • Channel IDs: use -100XXXXXXXXXX format or @username")
        j["logs"].append("   • Forward a message from the channel to @userinfobot to get the exact ID")
        j["logs"].append("   • Make sure you are a MEMBER of both channels")
        j["logs"].append("   • For private channels, you must have joined them in the Telegram app first")


# ─── Serve React Frontend ─────────────────────────────────────────────────────

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
