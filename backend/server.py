#!/usr/bin/env python3
"""
Telegram File Renamer - FastAPI Backend v6
Renames files SOURCE → DESTINATION without downloading/uploading.

RENAME METHOD (v6 — THE FIX that actually works):
══════════════════════════════════════════════════

❌ BROKEN v4: send_file(file=Document, attributes=[...])
   Telethon silently ignores `attributes` when `file` is already a
   Document TLObject. The file arrives with the OLD name.

❌ BROKEN v5: SendMediaRequest(..., attributes=[...])
   MTProto's SendMediaRequest schema has NO 'attributes' field at all.
   Raises: "got an unexpected keyword argument 'attributes'"

✅ WORKING v6: Mutate doc.attributes IN-PLACE, then send_file(doc)
   1. Refresh the doc's file_reference via GetMessages (avoids EXPIRED error)
   2. Walk doc.attributes, replace DocumentAttributeFilename with new name
   3. Call client.send_file(dst, file=doc, force_document=True)
      → Telethon sees the Document object, reads its (now-mutated) attributes
      → Builds InputMediaDocument with the NEW DocumentAttributeFilename
      → Sends to Telegram. Server stores new filename. ZERO bytes transferred.
"""

import asyncio
import os
import re
import uuid
import unicodedata
import logging
from typing import Dict, List, Optional, Tuple, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram File Renamer API", version="6.0.0")

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
    src_channel: str
    dst_channel: str
    delete_from_src: bool = False
    session_string: Optional[str] = None
    mappings: List[Dict[str, str]]  # [{"old": "...", "new": "..."}]


class OTPRequest(BaseModel):
    job_id: str
    otp: str


# ─── Fuzzy Filename Matching ──────────────────────────────────────────────────

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
    Dict[str, str],
    Dict[str, str],
    Dict[int, str],
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


# ─── Channel ID Resolution ────────────────────────────────────────────────────

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
        log_fn(f"   📐 Trying PeerChannel(id={bare_id}) ...")
        try:
            entity = await client.get_entity(PeerChannel(channel_id=bare_id))
            log_fn(f"   ✅ Resolved via PeerChannel: {getattr(entity, 'title', str(bare_id))}")
            return entity
        except Exception as e2:
            log_fn(f"   ⚠️  PeerChannel failed: {e2}")

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

        log_fn(f"   📐 Trying PeerChat(id={bare_id}) ...")
        try:
            entity = await client.get_entity(PeerChat(chat_id=bare_id))
            log_fn(f"   ✅ Resolved via PeerChat: {getattr(entity, 'title', str(bare_id))}")
            return entity
        except Exception as e4:
            log_fn(f"   ⚠️  PeerChat failed: {e4}")

        raise ValueError(
            f"Cannot resolve channel ID '{raw}'.\n"
            f"Fix: Forward any message from the channel to @userinfobot and use that exact ID."
        )
    else:
        log_fn(f"   📐 Trying as username/link: {parsed}")
        try:
            entity = await client.get_entity(parsed)
            log_fn(f"   ✅ Resolved: {getattr(entity, 'title', str(parsed))}")
            return entity
        except Exception as e:
            raise ValueError(
                f"Cannot resolve channel '{raw}'.\nError: {e}\n"
                f"Tips:\n  • Use @username format\n  • Or numeric ID from @userinfobot"
            )


# ─── Exhaustive Message Scanner ───────────────────────────────────────────────

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
    log_fn(f"   ✅ Scan complete: {scanned} total file messages processed")
    log_fn(f"   🎯 Exact matches     : {sum(1 for t in match_tiers.values() if t == 'exact')}")
    log_fn(f"   🔤 Normalized matches: {sum(1 for t in match_tiers.values() if t == 'normalized')}")
    log_fn(f"   🔢 Episode# matches  : {sum(1 for t in match_tiers.values() if t == 'episode')}")
    log_fn(f"   ⚠️  Not found         : {len(rename_map) - len(file_map)}")

    return file_map


# ─── CORE RENAME FUNCTION (v6 — mutate attributes in-place) ──────────────────

async def rename_and_send(client, dst_entity, msg, new_name: str, caption: str = "") -> bool:
    """
    THE v6 FIX — Mutate doc.attributes in-place, then send_file(doc).

    Why every other approach fails:
    ────────────────────────────────
    ❌ send_file(file=doc, attributes=[...])
       Telethon ignores `attributes` if `file` is already a Document object.
       Old filename comes through unchanged.

    ❌ SendMediaRequest(..., attributes=[...])
       MTProto SendMediaRequest has no 'attributes' field.
       Crash: "unexpected keyword argument 'attributes'"

    ✅ THIS APPROACH (v6):
       1. Refresh file_reference → avoids FILE_REFERENCE_EXPIRED errors
       2. Mutate doc.attributes → replace DocumentAttributeFilename in-place
       3. send_file(dst, file=doc) → Telethon reads the mutated attributes,
          builds InputMediaDocument with the NEW filename baked in,
          sends to Telegram. ZERO bytes transferred.
    """
    from telethon.tl.types import DocumentAttributeFilename
    from telethon.tl.functions.channels import GetMessagesRequest as ChannelGetMessages
    from telethon.tl.functions.messages import GetMessagesRequest
    from telethon.tl.types import InputMessageID

    doc = msg.document

    # ── Step 1: Refresh file_reference (avoids FILE_REFERENCE_EXPIRED) ────────
    try:
        # Try channel messages first (works for channel posts)
        peer = await client.get_input_entity(msg.peer_id)
        from telethon.tl.functions.channels import GetMessagesRequest as ChanGetMsgs
        refreshed = await client(ChanGetMsgs(channel=peer, id=[msg.id]))
        if hasattr(refreshed, "messages") and refreshed.messages:
            fresh = refreshed.messages[0]
            if hasattr(fresh, "document") and fresh.document:
                doc = fresh.document
    except Exception:
        try:
            # Fallback: plain GetMessages
            refreshed = await client(GetMessagesRequest(id=[msg.id]))
            if hasattr(refreshed, "messages") and refreshed.messages:
                fresh = refreshed.messages[0]
                if hasattr(fresh, "document") and fresh.document:
                    doc = fresh.document
        except Exception:
            pass  # Use original — may still succeed

    # ── Step 2: Build new attributes list with REPLACED filename ──────────────
    has_filename_attr = any(isinstance(a, DocumentAttributeFilename) for a in doc.attributes)

    new_attrs = []
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            new_attrs.append(DocumentAttributeFilename(file_name=new_name))
        else:
            new_attrs.append(attr)

    if not has_filename_attr:
        new_attrs.append(DocumentAttributeFilename(file_name=new_name))

    # ── Step 3: Mutate the document object IN-PLACE ───────────────────────────
    doc.attributes = new_attrs

    # ── Step 4: send_file reads mutated doc → sends with new filename ──────────
    # Telethon's send_file() with a Document object calls _get_file_info()
    # which reads doc.attributes — now containing our NEW filename.
    # It then builds InputMediaDocument and calls SendMediaRequest internally.
    # Result: Telegram stores the new filename. ZERO bytes transferred.
    await client.send_file(
        dst_entity,
        file=doc,
        caption=caption,
        force_document=True,
        supports_streaming=False,
    )

    return True


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Telegram File Renamer v6 is running"}


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
    }


# ─── WebSocket live log streaming ─────────────────────────────────────────────

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
            })

            if j["status"] in ("done", "error", "cancelled"):
                break

            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for job {job_id}")


# ─── Core Rename Job ──────────────────────────────────────────────────────────

async def run_rename_job(job_id: str, req: RenameRequest):
    j = jobs[job_id]
    queue = job_queues[job_id]

    def log(msg: str):
        j["logs"].append(msg)
        logger.info(f"[{job_id[:8]}] {msg}")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        log("🚀 Telegram File Renamer v6 — Filename Rename (No Download/Upload)")
        log("=" * 60)
        log(f"📥 Source      : {req.src_channel}")
        log(f"📤 Destination : {req.dst_channel}")
        log(f"🗑️  Delete src  : {'YES' if req.delete_from_src else 'NO'}")
        log(f"📋 Files       : {len(req.mappings)}")
        log("=" * 60)
        log("🔧 Rename Method v6: Mutate doc.attributes in-place → send_file(doc)")
        log("   ✅ This is the ONLY method that actually renames the file!")
        log("   ✅ Zero bytes transferred — uses same Telegram file_id")
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

        # ── Resolve channels ─────────────────────────────────────────────────
        log("📡 Resolving channels...")

        try:
            log("📥 Resolving SOURCE channel...")
            src_entity = await resolve_channel(client, req.src_channel, log)
        except ValueError as e:
            log(f"❌ SOURCE channel failed: {req.src_channel}")
            log(str(e))
            raise

        try:
            log("📤 Resolving DESTINATION channel...")
            dst_entity = await resolve_channel(client, req.dst_channel, log)
        except ValueError as e:
            log(f"❌ DESTINATION channel failed: {req.dst_channel}")
            log(str(e))
            raise

        src_name = getattr(src_entity, "title", req.src_channel)
        dst_name = getattr(dst_entity, "title", req.dst_channel)

        log(f"✅ SOURCE      → {src_name}")
        log(f"✅ DESTINATION → {dst_name}")
        log("─" * 60)

        # ── Build rename map ─────────────────────────────────────────────────
        rename_map: Dict[str, str] = {
            m["old"].strip(): m["new"].strip() for m in req.mappings
        }

        # ── Exhaustive fuzzy scan ─────────────────────────────────────────────
        log("🔍 PHASE 1: Scanning source channel (exhaustive, fuzzy match)...")
        j["status"] = "scanning"

        file_map = await scan_all_messages(client, src_entity, rename_map, log)

        found = len(file_map)
        log("─" * 60)
        log(f"📊 SCAN RESULTS: {found}/{len(rename_map)} files located")

        if found == 0:
            log("⚠️  Zero files matched. Possible reasons:")
            log("   1. Old filenames don't match what's in the channel")
            log("   2. The source channel has no document files")
            log("   3. Long-press file → Info → copy exact filename")
            j["status"] = "done"
            await client.disconnect()
            return

        # ── Rename phase ──────────────────────────────────────────────────────
        log("─" * 60)
        log(f"✏️  PHASE 2: Renaming {found} files...")
        log(f"   Step 1: Refresh file_reference (avoids REFERENCE_EXPIRED)")
        log(f"   Step 2: Mutate doc.attributes with NEW DocumentAttributeFilename")
        log(f"   Step 3: send_file(doc) → Telegram stores new name, 0 bytes sent")
        log("─" * 60)
        j["status"] = "renaming"

        renamed = 0
        failed: List[str] = []
        not_found_list: List[str] = []
        total = len(rename_map)

        for idx, (old_name, new_name) in enumerate(rename_map.items(), 1):
            msg_obj = file_map.get(old_name)

            if not msg_obj:
                log(f"⚠️  [{idx:03d}/{total}] SKIP (not found): {old_name[:60]}")
                not_found_list.append(old_name)
                j["progress"] = idx
                continue

            try:
                caption = msg_obj.message or ""

                await rename_and_send(client, dst_entity, msg_obj, new_name, caption)

                if req.delete_from_src:
                    await msg_obj.delete()

                log(f"✅ [{idx:03d}/{total}] {'MOVED' if req.delete_from_src else 'COPIED'} + RENAMED")
                log(f"   📄 OLD: {old_name[:65]}")
                log(f"   ✏️  NEW: {new_name[:65]}")

                renamed += 1
                j["progress"] = idx

                # Rate limit: 2 seconds between sends (~30 files/min, safe)
                await asyncio.sleep(2.0)

            except Exception as e:
                err_msg = str(e)
                log(f"❌ [{idx:03d}/{total}] FAILED: {old_name[:60]}")
                log(f"   Reason: {err_msg}")
                failed.append(old_name)
                j["progress"] = idx

                if "FloodWait" in err_msg or "A wait of" in err_msg:
                    wait = 60
                    try:
                        m2 = re.search(r"(\d+)", err_msg)
                        if m2:
                            wait = int(m2.group(1)) + 10
                    except Exception:
                        pass
                    log(f"⏳ FloodWait! Pausing {wait}s...")
                    await asyncio.sleep(wait)
                    log("▶️  Resuming...")
                else:
                    await asyncio.sleep(3.0)

        # ── Final summary ─────────────────────────────────────────────────────
        log("=" * 60)
        log("🎉 RENAME JOB COMPLETE!")
        log(f"   ✅ Renamed successfully : {renamed}")
        log(f"   ❌ Errors              : {len(failed)}")
        log(f"   ⚠️  Not found           : {len(not_found_list)}")
        log(f"   📥 Source              : {src_name}")
        log(f"   📤 Destination         : {dst_name}")
        log(f"   🗑️  Deleted from source  : {'YES' if req.delete_from_src else 'NO'}")

        if not_found_list:
            log("")
            log("⚠️  Files not found in source channel:")
            for f in not_found_list:
                log(f"   - {f}")
            log("")
            log("💡 FIX: Long-press file → ⋮ → File Info → copy exact filename")
            log("   Update your 'Old Filenames' list and run again.")

        if failed:
            log("")
            log("❌ Files that errored during rename:")
            for f in failed:
                log(f"   - {f}")
            log("💡 TIP: Re-run with just the failed files pasted in.")

        log("=" * 60)
        log("💾 Save the session string (green box above) to skip OTP next time!")

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
        j["logs"].append("   • Channel IDs: use -100XXXXXXXXXX or @username")
        j["logs"].append("   • Forward a message to @userinfobot to get the exact channel ID")
        j["logs"].append("   • Make sure you are a MEMBER of both channels in Telegram app")


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
