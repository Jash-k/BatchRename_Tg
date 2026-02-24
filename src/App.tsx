import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { cn } from "@/utils/cn";

// ─── Types ────────────────────────────────────────────────────────────────────
type Tab = "setup" | "mapping" | "run" | "guide";
type JobStatus = "idle" | "starting" | "scanning" | "renaming" | "done" | "error";

interface JobState {
  jobId: string | null;
  status: JobStatus;
  progress: number;
  total: number;
  logs: string[];
  error: string | null;
  needsOtp: boolean;
  sessionString: string | null;
}

// ─── API Base URL ─────────────────────────────────────────────────────────────
const API =
  import.meta.env.VITE_API_URL ||
  (typeof window !== "undefined" && window.location.origin !== "null"
    ? window.location.origin
    : "http://localhost:8000");

// ─── Utility Components ───────────────────────────────────────────────────────
function CopyBtn({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className={cn(
        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all",
        copied ? "bg-emerald-500 text-white" : "bg-slate-700 text-slate-200 hover:bg-slate-600"
      )}
    >
      {copied ? "✓ Copied!" : `⎘ ${label}`}
    </button>
  );
}

function Badge({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold", color)}>
      {children}
    </span>
  );
}

function CodeBlock({ code, lang = "bash" }: { code: string; lang?: string }) {
  return (
    <div className="relative rounded-xl overflow-hidden border border-slate-700 mt-2">
      <div className="flex items-center justify-between bg-slate-800 px-4 py-2 border-b border-slate-700">
        <span className="text-xs text-slate-400 font-mono">{lang}</span>
        <CopyBtn text={code} />
      </div>
      <pre className="bg-slate-900 px-4 py-3 text-sm text-emerald-400 font-mono overflow-x-auto whitespace-pre-wrap">
        {code}
      </pre>
    </div>
  );
}

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-slate-500">
        <span>{value} / {max} files</span>
        <span>{pct}%</span>
      </div>
      <div className="h-3 rounded-full bg-slate-200 overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            pct === 100 ? "bg-emerald-500" : "bg-violet-500"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ─── Tab: Setup ───────────────────────────────────────────────────────────────
function SetupTab({
  apiId, setApiId,
  apiHash, setApiHash,
  phone, setPhone,
  srcChannel, setSrcChannel,
  dstChannel, setDstChannel,
  deleteFromSrc, setDeleteFromSrc,
  sessionString, setSessionString,
}: {
  apiId: string; setApiId: (v: string) => void;
  apiHash: string; setApiHash: (v: string) => void;
  phone: string; setPhone: (v: string) => void;
  srcChannel: string; setSrcChannel: (v: string) => void;
  dstChannel: string; setDstChannel: (v: string) => void;
  deleteFromSrc: boolean; setDeleteFromSrc: (v: boolean) => void;
  sessionString: string; setSessionString: (v: string) => void;
}) {
  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
        <p className="font-bold mb-1">🔑 Get your Telegram API credentials</p>
        <ol className="list-decimal ml-4 space-y-1 text-blue-700">
          <li>Go to <a href="https://my.telegram.org" target="_blank" rel="noreferrer" className="underline font-semibold">my.telegram.org</a></li>
          <li>Log in → click <strong>API development tools</strong></li>
          <li>Create a new app → copy <strong>API ID</strong> and <strong>API Hash</strong></li>
        </ol>
      </div>

      {/* API Credentials */}
      <div>
        <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-3">🔐 API Credentials</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          {[
            { label: "API ID", val: apiId, set: setApiId, placeholder: "e.g. 12345678", type: "text" },
            { label: "API Hash", val: apiHash, set: setApiHash, placeholder: "e.g. a1b2c3d4e5f6a1b2c3d4e5f6", type: "text" },
            { label: "Phone Number (with country code)", val: phone, set: setPhone, placeholder: "e.g. +919876543210", type: "tel" },
          ].map(({ label, val, set, placeholder, type }) => (
            <div key={label}>
              <label className="block text-sm font-semibold text-slate-700 mb-1">{label}</label>
              <input
                type={type}
                value={val}
                onChange={e => set(e.target.value)}
                placeholder={placeholder}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
              />
            </div>
          ))}
        </div>
      </div>

      {/* Channel Config */}
      <div>
        <h3 className="text-sm font-bold text-slate-600 uppercase tracking-wider mb-3">📡 Channel Configuration</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          {/* Source Channel */}
          <div className="rounded-xl border-2 border-red-200 bg-red-50 p-4">
            <label className="block text-sm font-bold text-red-700 mb-1">
              📥 Source Channel
              <span className="ml-1 font-normal text-red-500 text-xs">(where files currently are)</span>
            </label>
            <input
              type="text"
              value={srcChannel}
              onChange={e => setSrcChannel(e.target.value)}
              placeholder="@animesaga  or  -1003557121488  or  t.me/animesaga"
              className="w-full rounded-lg border border-red-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400 font-mono"
            />
            <div className="mt-2 space-y-1">
              <p className="text-xs text-red-600">Files are <strong>scanned</strong> from here</p>
              <div className="rounded-lg bg-red-100 border border-red-200 px-2 py-1.5 text-xs text-red-700 space-y-0.5">
                <p className="font-semibold">✅ Accepted formats:</p>
                <p className="font-mono">@channelname</p>
                <p className="font-mono">-1003557121488 &nbsp;<span className="font-sans font-normal text-red-500">(numeric ID with -100 prefix)</span></p>
                <p className="font-mono">t.me/channelname</p>
                <p className="font-semibold mt-1">💡 Don't know the ID? <a href="https://t.me/userinfobot" target="_blank" rel="noreferrer" className="underline">Forward a message to @userinfobot</a></p>
              </div>
            </div>
          </div>

          {/* Destination Channel */}
          <div className="rounded-xl border-2 border-emerald-200 bg-emerald-50 p-4">
            <label className="block text-sm font-bold text-emerald-700 mb-1">
              📤 Destination Channel
              <span className="ml-1 font-normal text-emerald-500 text-xs">(where renamed files go)</span>
            </label>
            <input
              type="text"
              value={dstChannel}
              onChange={e => setDstChannel(e.target.value)}
              placeholder="@mahabharat_hd  or  -1001234567890  or  t.me/mahabharat"
              className="w-full rounded-lg border border-emerald-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400 font-mono"
            />
            <div className="mt-2 space-y-1">
              <p className="text-xs text-emerald-600">Renamed files are <strong>sent</strong> here</p>
              <div className="rounded-lg bg-emerald-100 border border-emerald-200 px-2 py-1.5 text-xs text-emerald-700 space-y-0.5">
                <p className="font-semibold">✅ Accepted formats:</p>
                <p className="font-mono">@channelname</p>
                <p className="font-mono">-1001234567890 &nbsp;<span className="font-sans font-normal text-emerald-600">(numeric ID with -100 prefix)</span></p>
                <p className="font-mono">t.me/channelname</p>
                <p className="font-semibold mt-1">💡 Must be <strong>admin</strong> of this channel to post files</p>
              </div>
            </div>
          </div>
        </div>

        {/* Flow diagram */}
        <div className="mt-3 flex items-center justify-center gap-3 py-3 bg-slate-50 rounded-xl border border-slate-200">
          <div className="flex flex-col items-center">
            <span className="text-2xl">📥</span>
            <span className="text-xs font-semibold text-red-600 mt-1">Source</span>
            <span className="text-xs text-slate-500 font-mono max-w-[100px] truncate">{srcChannel || "@source"}</span>
          </div>
          <div className="flex flex-col items-center gap-0.5 text-slate-400 text-xs text-center">
            <span className="text-lg">→</span>
            <span className="font-mono bg-violet-100 text-violet-700 px-2 py-0.5 rounded text-xs">rename</span>
            <span className="text-lg">→</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="text-2xl">📤</span>
            <span className="text-xs font-semibold text-emerald-600 mt-1">Destination</span>
            <span className="text-xs text-slate-500 font-mono max-w-[100px] truncate">{dstChannel || "@destination"}</span>
          </div>
        </div>

        {/* Delete from source toggle */}
        <div className="mt-3 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <button
            type="button"
            onClick={() => setDeleteFromSrc(!deleteFromSrc)}
            className={cn(
              "relative flex-shrink-0 mt-0.5 h-6 w-11 rounded-full transition-colors duration-200 focus:outline-none",
              deleteFromSrc ? "bg-red-500" : "bg-slate-300"
            )}
          >
            <span className={cn(
              "absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-200",
              deleteFromSrc ? "translate-x-5" : "translate-x-0"
            )} />
          </button>
          <div>
            <p className="text-sm font-semibold text-amber-800">
              Delete original from Source after rename
              <span className={cn("ml-2 text-xs rounded-full px-2 py-0.5 font-bold", deleteFromSrc ? "bg-red-100 text-red-700" : "bg-slate-200 text-slate-500")}>
                {deleteFromSrc ? "ON" : "OFF"}
              </span>
            </p>
            <p className="text-xs text-amber-700 mt-0.5">
              {deleteFromSrc
                ? "⚠️ Source files will be deleted after successful rename — you must be an admin of the source channel"
                : "✅ Source files will be kept — renamed copies go to destination only"}
            </p>
          </div>
        </div>
      </div>

      {/* Session String */}
      <div>
        <label className="block text-sm font-semibold text-slate-700 mb-1">
          Session String <span className="text-slate-400 font-normal">(optional — paste from a previous run to skip OTP)</span>
        </label>
        <textarea
          value={sessionString}
          onChange={e => setSessionString(e.target.value)}
          rows={3}
          placeholder="Paste your Telethon StringSession here to skip OTP on future runs..."
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none"
        />
      </div>

      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
        <p className="font-bold mb-2">⚠️ Important Notes</p>
        <ul className="list-disc ml-4 space-y-1 text-slate-600">
          <li>Files are renamed <strong>without any download/upload</strong> — Telegram reuses the same <code className="bg-slate-200 px-1 rounded text-xs">file_id</code></li>
          <li>You must be a <strong>member</strong> of the source channel to read files</li>
          <li>You must be an <strong>admin</strong> of the destination channel to post files</li>
          <li>If <em>Delete from Source</em> is ON, you must also be an <strong>admin</strong> of the source channel</li>
          <li>After the job, save the <strong>session string</strong> shown in Run tab to skip OTP next time</li>
        </ul>
      </div>
    </div>
  );
}

// ─── Tab: Mapping ─────────────────────────────────────────────────────────────
function MappingTab({
  oldNames, setOldNames,
  newNames, setNewNames,
}: {
  oldNames: string; setOldNames: (v: string) => void;
  newNames: string; setNewNames: (v: string) => void;
}) {
  const oldLines = oldNames.split("\n").filter(l => l.trim());
  const newLines = newNames.split("\n").filter(l => l.trim());
  const count = Math.min(oldLines.length, newLines.length);
  const mismatch = oldLines.length !== newLines.length && (oldLines.length > 0 || newLines.length > 0);

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        <p className="font-semibold text-slate-700 mb-1">📋 Paste your filenames — one per line</p>
        <p>Order must match exactly. Line 1 of <em>Old</em> maps to Line 1 of <em>New</em>, and so on.</p>
      </div>

      {mismatch && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 font-semibold">
          ⚠️ Count mismatch — Old: <strong>{oldLines.length}</strong> | New: <strong>{newLines.length}</strong>
        </div>
      )}
      {!mismatch && count > 0 && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-700 font-semibold">
          ✅ {count} file mappings ready to go!
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <label className="block text-sm font-semibold text-slate-700 mb-1">
            Old Filenames <span className="text-slate-400 font-normal">({oldLines.length} lines)</span>
          </label>
          <textarea
            value={oldNames}
            onChange={e => setOldNames(e.target.value)}
            rows={15}
            placeholder={"[AnimeSaga]- MahabharathamEpisode 1 [AS].mkv\n[AnimeSaga]- MahabharathamEpisode 2 [AS].mkv\n[AnimeSaga]- MahabharathamEpisode 3 [AS].mkv\n..."}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none"
          />
        </div>
        <div>
          <label className="block text-sm font-semibold text-slate-700 mb-1">
            New Filenames <span className="text-slate-400 font-normal">({newLines.length} lines)</span>
          </label>
          <textarea
            value={newNames}
            onChange={e => setNewNames(e.target.value)}
            rows={15}
            placeholder={"Mahabharat.2013.S01E001.Shantanu.Accepts.Bheeshm.As.Son.1080p.WEB-DL.JaSH.mkv\nMahabharat.2013.S01E002.Title.Here.1080p.WEB-DL.JaSH.mkv\nMahabharat.2013.S01E003.Title.Here.1080p.WEB-DL.JaSH.mkv\n..."}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none"
          />
        </div>
      </div>

      {count > 0 && !mismatch && (
        <div>
          <p className="text-sm font-semibold text-slate-700 mb-2">Preview (first 5 mappings)</p>
          <div className="rounded-xl border border-slate-200 overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-slate-100">
                <tr>
                  <th className="px-3 py-2 text-left text-slate-500 w-8">#</th>
                  <th className="px-3 py-2 text-left text-red-600">Old Name</th>
                  <th className="px-3 py-2 text-center text-slate-400 w-6">→</th>
                  <th className="px-3 py-2 text-left text-emerald-700">New Name</th>
                </tr>
              </thead>
              <tbody>
                {oldLines.slice(0, 5).map((old, i) => (
                  <tr key={i} className={i % 2 === 0 ? "bg-white" : "bg-slate-50"}>
                    <td className="px-3 py-2 text-slate-400">{i + 1}</td>
                    <td className="px-3 py-2 font-mono text-red-600 max-w-[200px] truncate">{old}</td>
                    <td className="px-3 py-2 text-center text-slate-400">→</td>
                    <td className="px-3 py-2 font-mono text-emerald-700 max-w-[200px] truncate">{newLines[i]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {count > 5 && (
              <div className="bg-slate-50 px-3 py-2 text-xs text-slate-400 text-center border-t">
                + {count - 5} more mappings...
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Tab: Run ─────────────────────────────────────────────────────────────────
function RunTab({
  job, mappingCount, ready,
  onStart, onSubmitOtp, otp, setOtp,
  srcChannel, dstChannel, deleteFromSrc,
}: {
  job: JobState;
  mappingCount: number;
  ready: boolean;
  onStart: () => void;
  onSubmitOtp: () => void;
  otp: string;
  setOtp: (v: string) => void;
  srcChannel: string;
  dstChannel: string;
  deleteFromSrc: boolean;
}) {
  const logsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logsRef.current) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight;
    }
  }, [job.logs]);

  // Channel flow info bar
  const channelBar = srcChannel && dstChannel ? (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 flex flex-wrap items-center gap-2 text-xs">
      <span className="flex items-center gap-1.5 rounded-lg bg-red-100 text-red-700 px-3 py-1.5 font-semibold">
        📥 {srcChannel}
      </span>
      <span className="text-slate-400 font-bold">→ rename →</span>
      <span className="flex items-center gap-1.5 rounded-lg bg-emerald-100 text-emerald-700 px-3 py-1.5 font-semibold">
        📤 {dstChannel}
      </span>
      {deleteFromSrc && (
        <span className="rounded-lg bg-amber-100 text-amber-700 px-2 py-1 font-semibold">
          🗑️ delete source
        </span>
      )}
    </div>
  ) : null;

  const statusColor: Record<JobStatus, string> = {
    idle: "bg-slate-100 text-slate-600",
    starting: "bg-blue-100 text-blue-700",
    scanning: "bg-amber-100 text-amber-700",
    renaming: "bg-violet-100 text-violet-700",
    done: "bg-emerald-100 text-emerald-700",
    error: "bg-red-100 text-red-700",
  };

  const statusLabel: Record<JobStatus, string> = {
    idle: "⏸ Idle",
    starting: "🔄 Starting...",
    scanning: "🔍 Scanning Messages",
    renaming: "✏️ Renaming Files",
    done: "✅ Done!",
    error: "❌ Error",
  };

  return (
    <div className="space-y-5">
      {/* Channel flow bar */}
      {channelBar}

      {/* Status bar */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Badge color={statusColor[job.status]}>{statusLabel[job.status]}</Badge>
          {job.jobId && (
            <span className="text-xs text-slate-400 font-mono">Job: {job.jobId.slice(0, 8)}</span>
          )}
        </div>
        {!ready && job.status === "idle" && (
          <p className="text-sm text-amber-600 font-medium">⚠️ Complete Setup + File Mapping tabs first</p>
        )}
      </div>

      {/* Progress */}
      {job.status !== "idle" && (
        <ProgressBar value={job.progress} max={job.total} />
      )}

      {/* OTP Input */}
      {job.needsOtp && (
        <div className="rounded-xl border-2 border-violet-300 bg-violet-50 p-5">
          <p className="font-bold text-violet-800 text-base mb-1">📱 OTP Required</p>
          <p className="text-sm text-violet-700 mb-3">
            Telegram has sent a verification code to your app. Enter it below to continue.
          </p>
          <div className="flex gap-3">
            <input
              type="text"
              value={otp}
              onChange={e => setOtp(e.target.value)}
              placeholder="Enter OTP code..."
              className="flex-1 rounded-lg border-2 border-violet-300 px-4 py-2 text-lg font-mono text-center focus:outline-none focus:border-violet-500"
              onKeyDown={e => e.key === "Enter" && onSubmitOtp()}
            />
            <button
              onClick={onSubmitOtp}
              className="rounded-lg bg-violet-600 text-white px-5 py-2 font-semibold hover:bg-violet-700 transition-all"
            >
              Submit →
            </button>
          </div>
        </div>
      )}

      {/* Session string display */}
      {job.sessionString && job.status === "done" && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="font-semibold text-emerald-800 text-sm">💾 Save Your Session String</p>
            <CopyBtn text={job.sessionString} label="Copy Session" />
          </div>
          <p className="text-xs text-emerald-700 mb-2">
            Paste this in the <strong>Session String</strong> field in Setup tab next time to skip OTP!
          </p>
          <textarea
            readOnly
            value={job.sessionString}
            rows={3}
            className="w-full rounded-lg border border-emerald-300 bg-white px-3 py-2 text-xs font-mono resize-none"
          />
        </div>
      )}

      {/* Error */}
      {job.status === "error" && job.error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 space-y-3">
          <p className="font-bold">❌ Error Details</p>
          <code className="block text-xs font-mono break-all bg-red-100 rounded p-2">{job.error}</code>

          {/* Channel ID specific help */}
          {(job.error.includes("entity") || job.error.includes("channel") || job.error.includes("Cannot find")) && (
            <div className="rounded-lg border border-red-300 bg-white p-3 space-y-2">
              <p className="font-bold text-red-800">🔧 Channel ID Fix Guide</p>
              <div className="space-y-1.5 text-xs text-red-700">
                <p>The channel ID you entered could not be resolved. Try these steps:</p>
                <div className="bg-red-50 rounded p-2 space-y-1 font-mono">
                  <p className="font-sans font-semibold">Option 1 — Use @username:</p>
                  <p>@animesaga_channel</p>
                </div>
                <div className="bg-red-50 rounded p-2 space-y-1 font-mono">
                  <p className="font-sans font-semibold">Option 2 — Get exact numeric ID:</p>
                  <p className="font-sans">1. Open Telegram → go to the channel</p>
                  <p className="font-sans">2. Forward ANY message to <a href="https://t.me/userinfobot" target="_blank" rel="noreferrer" className="underline font-bold">@userinfobot</a></p>
                  <p className="font-sans">3. It replies with the exact Chat ID like: <span className="font-mono">-1003557121488</span></p>
                  <p className="font-sans">4. Paste that exact number (with -100 prefix)</p>
                </div>
                <div className="bg-red-50 rounded p-2 space-y-1">
                  <p className="font-semibold">Option 3 — Use t.me link:</p>
                  <p className="font-mono">t.me/channelname</p>
                </div>
                <p className="font-semibold text-red-800">⚠️ Also make sure you have JOINED the channel in your Telegram app before running!</p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Live Logs */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-semibold text-slate-700">📜 Live Logs</p>
          {job.logs.length > 0 && (
            <CopyBtn text={job.logs.join("\n")} label="Copy Logs" />
          )}
        </div>
        <div
          ref={logsRef}
          className="rounded-xl bg-slate-900 border border-slate-700 p-4 h-72 overflow-y-auto font-mono text-xs leading-relaxed space-y-0.5"
        >
          {job.logs.length === 0 ? (
            <p className="text-slate-500 italic">Logs will appear here when you start the job...</p>
          ) : (
            job.logs.map((log, i) => (
              <div
                key={i}
                className={cn(
                  "whitespace-pre-wrap",
                  log.startsWith("✅") ? "text-emerald-400" :
                  log.startsWith("❌") ? "text-red-400" :
                  log.startsWith("⚠️") ? "text-amber-400" :
                  log.startsWith("🔍") || log.startsWith("📂") ? "text-blue-400" :
                  log.startsWith("🎉") ? "text-yellow-400" :
                  log.startsWith("=") ? "text-violet-400" :
                  "text-slate-300"
                )}
              >
                {log}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Start Button */}
      <div className="flex justify-center pt-2">
        {job.status === "idle" || job.status === "error" || job.status === "done" ? (
          <button
            onClick={onStart}
            disabled={!ready || mappingCount === 0}
            className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-blue-600 text-white px-10 py-3.5 text-base font-bold shadow-lg hover:from-violet-700 hover:to-blue-700 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 3l14 9-14 9V3z" />
            </svg>
            {job.status === "done" ? "Run Again" : "Start Renaming"}
            {mappingCount > 0 && <span className="ml-1 opacity-75">({mappingCount} files)</span>}
          </button>
        ) : (
          <div className="flex items-center gap-3 text-slate-500 text-sm">
            <div className="h-5 w-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
            <span>Job running... do not close this tab</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Tab: Guide ───────────────────────────────────────────────────────────────
function GuideTab() {
  return (
    <div className="space-y-5">
      {/* How it works */}
      <div className="rounded-xl border border-violet-200 bg-violet-50 p-5 space-y-3">
        <p className="font-bold text-violet-900 text-base">💡 How does rename work without downloading?</p>
        <p className="text-sm text-violet-800">
          Telegram stores files by <strong>file_id</strong> (a content hash). When Telethon calls{" "}
          <code className="bg-violet-200 px-1 rounded">send_file()</code> with an existing{" "}
          <code className="bg-violet-200 px-1 rounded">document</code> object and <em>new</em> filename
          attributes, Telegram creates a new message referencing the same stored bytes —{" "}
          <strong>zero bytes downloaded or uploaded</strong>. This is exactly how bots like @FileRenameBot work.
        </p>
        {/* Flow diagram */}
        <div className="rounded-xl bg-white border border-violet-200 p-4">
          <p className="text-xs font-bold text-violet-700 mb-3 uppercase tracking-wider">How the flow works</p>
          <div className="flex flex-col gap-2 text-sm">
            {[
              { icon: "1️⃣", label: "Scan Source", desc: "Telethon reads message list from Source channel — no file data downloaded" },
              { icon: "2️⃣", label: "Match Filenames", desc: "Matches each old filename in your mapping list against scanned messages" },
              { icon: "3️⃣", label: "send_file() to Destination", desc: "Sends the document object (by file_id) to Destination with new filename — 0 bytes transferred" },
              { icon: "4️⃣", label: "Delete from Source (optional)", desc: "If enabled, deletes the original message from Source channel" },
            ].map((step, i) => (
              <div key={i} className="flex items-start gap-3 rounded-lg bg-violet-50 p-3">
                <span className="text-lg flex-shrink-0">{step.icon}</span>
                <div>
                  <p className="font-semibold text-violet-800">{step.label}</p>
                  <p className="text-xs text-violet-700">{step.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Deploy on Render */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-3">
        <p className="font-bold text-slate-800 text-base">🚀 Deploy this app on Render.com (Free)</p>
        <ol className="space-y-3 text-sm text-slate-700 list-decimal ml-4">
          <li>Push this entire project folder to a <strong>GitHub repo</strong></li>
          <li>
            Go to <a href="https://render.com" target="_blank" rel="noreferrer" className="text-violet-600 underline font-semibold">render.com</a> → New → <strong>Web Service</strong>
          </li>
          <li>Connect your GitHub repo</li>
          <li>Render auto-detects the <code className="bg-slate-100 px-1 rounded">render.yaml</code> and <code className="bg-slate-100 px-1 rounded">Dockerfile</code></li>
          <li>Click <strong>Deploy</strong> — your app is live in ~3 minutes!</li>
        </ol>
        <CodeBlock code="# render.yaml is already included in this project\n# Just push to GitHub and connect to Render!" lang="yaml" />
      </div>

      {/* Run locally */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-3">
        <p className="font-bold text-slate-800 text-base">🖥️ Run Locally (Alternative)</p>
        <CodeBlock code={`# 1. Install Python dependencies
cd backend
pip install -r requirements.txt

# 2. Build the React frontend
cd ..
npm install && npm run build

# 3. Copy built files to backend/static
cp -r dist/* backend/static/

# 4. Start the server
cd backend
python server.py
# → Open http://localhost:8000`} lang="bash" />
      </div>

      {/* Run with Docker */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-3">
        <p className="font-bold text-slate-800 text-base">🐳 Run with Docker</p>
        <CodeBlock code={`# Build the Docker image
docker build -t tg-renamer .

# Run the container
docker run -p 8000:8000 tg-renamer

# → Open http://localhost:8000`} lang="bash" />
      </div>

      {/* FAQ */}
      <div className="space-y-3">
        <p className="font-bold text-slate-800">❓ FAQ</p>
        {[
          {
            q: "Is my Telegram account safe?",
            a: "Yes. The script only reads file messages and re-posts them with new names. It never accesses your private chats. Credentials are used only during the active rename session.",
          },
          {
            q: "Do I need to be an admin of both channels?",
            a: "You must be a member of the Source channel (to read files). You must be an admin of the Destination channel (to post files). If 'Delete from Source' is ON, you also need admin in the Source channel.",
          },
          {
            q: "Can I use the same channel as source and destination?",
            a: "Yes! Just enter the same channel for both Source and Destination. The renamed file will appear in the same channel. Enable 'Delete from Source' to remove the old-named file.",
          },
          {
            q: "I get 'Cannot find any entity' error for my channel ID!",
            a: "This is a channel ID format issue. The server tries 4 resolution strategies automatically. Best fix: forward ANY message from the channel to @userinfobot on Telegram — it replies with the exact Chat ID (e.g. -1003557121488). Use that exact number including the -100 prefix. Alternatively use the @username format.",
          },
          {
            q: "Can I use private channels?",
            a: "Yes — use the numeric chat ID (e.g. -1003557121488). Forward a message from the channel to @userinfobot to get the exact ID. You must have JOINED the channel in your Telegram app before running the script.",
          },
          {
            q: "What if a file is not found in source?",
            a: "The script logs it as NOT FOUND and continues. Make sure filenames are exact (case-sensitive, spaces, special characters included).",
          },
          {
            q: "What if I get a FloodWaitError?",
            a: "Telegram's rate limiter kicked in. The script auto-detects the wait time and pauses accordingly. It adds a 1.5s delay between each rename to minimize this.",
          },
          {
            q: "How long does it take for 267 files?",
            a: "About 7–8 minutes with a 1.5s delay between each rename. The send_file() call itself is instant (no data transfer) — only the Telegram API round-trip takes time.",
          },
          {
            q: "Do I need to keep the browser open?",
            a: "Yes — keep the Run tab open while the job is running. The WebSocket streams live logs from the server.",
          },
        ].map((item, i) => (
          <div key={i} className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="font-semibold text-slate-800 text-sm mb-1">Q: {item.q}</p>
            <p className="text-sm text-slate-600">A: {item.a}</p>
          </div>
        ))}
      </div>

      {/* Architecture */}
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <p className="font-bold text-slate-800 text-base mb-3">🏗️ Architecture</p>
        <div className="grid grid-cols-3 gap-3 text-center text-sm">
          {[
            { icon: "🌐", label: "React Frontend", sub: "Vite + Tailwind" },
            { icon: "⚡", label: "FastAPI Backend", sub: "WebSocket + REST" },
            { icon: "🤖", label: "Telethon", sub: "Telegram MTProto" },
          ].map((item, i) => (
            <div key={i} className="rounded-lg bg-slate-50 border border-slate-200 p-3">
              <div className="text-2xl mb-1">{item.icon}</div>
              <div className="font-semibold text-slate-800">{item.label}</div>
              <div className="text-xs text-slate-500">{item.sub}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs font-mono text-center text-slate-500">
          Browser UI → REST/WebSocket → FastAPI → Telethon MTProto<br />
          → Read from <span className="text-red-600 font-bold">Source Channel</span><br />
          → Write to <span className="text-emerald-600 font-bold">Destination Channel</span><br />
          → (Optionally) Delete from Source
        </div>
      </div>
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────
const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "setup", label: "Setup", icon: "⚙️" },
  { id: "mapping", label: "File Mapping", icon: "📋" },
  { id: "run", label: "Run", icon: "▶️" },
  { id: "guide", label: "Deploy Guide", icon: "📖" },
];

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>("setup");

  // Setup state
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [srcChannel, setSrcChannel] = useState("");
  const [dstChannel, setDstChannel] = useState("");
  const [deleteFromSrc, setDeleteFromSrc] = useState(false);
  const [sessionString, setSessionString] = useState("");

  // Mapping state
  const [oldNames, setOldNames] = useState("");
  const [newNames, setNewNames] = useState("");

  // Run state
  const [otp, setOtp] = useState("");
  const [job, setJob] = useState<JobState>({
    jobId: null,
    status: "idle",
    progress: 0,
    total: 0,
    logs: [],
    error: null,
    needsOtp: false,
    sessionString: null,
  });

  const wsRef = useRef<WebSocket | null>(null);

  const mappings = useMemo(() => {
    const oldLines = oldNames.split("\n").filter(l => l.trim());
    const newLines = newNames.split("\n").filter(l => l.trim());
    if (oldLines.length === 0 || newLines.length === 0 || oldLines.length !== newLines.length) return [];
    return oldLines.map((old, i) => ({ old: old.trim(), new: newLines[i].trim() }));
  }, [oldNames, newNames]);

  const ready = Boolean(apiId && apiHash && phone && srcChannel && dstChannel);

  // Connect WebSocket for a job
  const connectWs = useCallback((jobId: string) => {
    const wsUrl = API.replace(/^http/, "ws") + `/ws/${jobId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "log") {
        setJob(prev => ({ ...prev, logs: [...prev.logs, data.message] }));
      } else if (data.type === "status") {
        setJob(prev => ({
          ...prev,
          status: data.status as JobStatus,
          progress: data.progress,
          total: data.total,
          needsOtp: data.needs_otp,
          error: data.error,
          sessionString: data.session_string ?? prev.sessionString,
        }));
      }
    };

    ws.onerror = () => {
      setJob(prev => ({
        ...prev,
        status: "error",
        error: "WebSocket connection failed. Is the backend running?",
      }));
    };

    return ws;
  }, []);

  const handleStart = async () => {
    if (!ready || mappings.length === 0) return;

    // Reset job
    setJob({
      jobId: null,
      status: "starting",
      progress: 0,
      total: mappings.length,
      logs: ["🔄 Connecting to server..."],
      error: null,
      needsOtp: false,
      sessionString: null,
    });
    setActiveTab("run");

    try {
      const res = await fetch(`${API}/api/start-rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_id: apiId,
          api_hash: apiHash,
          phone,
          src_channel: srcChannel,
          dst_channel: dstChannel,
          delete_from_src: deleteFromSrc,
          session_string: sessionString || null,
          mappings,
        }),
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error(`Server error: ${err}`);
      }

      const { job_id } = await res.json();
      setJob(prev => ({ ...prev, jobId: job_id }));
      connectWs(job_id);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setJob(prev => ({
        ...prev,
        status: "error",
        error: message,
        logs: [...prev.logs, `❌ Failed to start: ${message}`],
      }));
    }
  };

  const handleSubmitOtp = async () => {
    if (!job.jobId || !otp) return;
    await fetch(`${API}/api/submit-otp`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job.jobId, otp }),
    });
    setOtp("");
  };

  // Cleanup WS on unmount
  useEffect(() => {
    return () => { wsRef.current?.close(); };
  }, []);

  const setupDone = Boolean(apiId && apiHash && phone && srcChannel && dstChannel);
  const mappingDone = mappings.length > 0;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-violet-950">
      {/* Header */}
      <header className="border-b border-white/10 bg-white/5 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-3">
          <div className="flex items-center justify-center h-10 w-10 rounded-xl bg-gradient-to-br from-blue-500 to-violet-600 shadow-lg flex-shrink-0">
            <svg className="h-6 w-6 text-white" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.941z" />
            </svg>
          </div>
          <div>
            <h1 className="text-base font-bold text-white leading-tight">Telegram File Renamer</h1>
            <p className="text-xs text-slate-400">Batch rename without downloading — powered by Telethon on Render</p>
          </div>
          <div className="ml-auto flex items-center gap-2 flex-wrap justify-end">
            {mappingDone && (
              <Badge color="bg-emerald-500/20 text-emerald-300">{mappings.length} files mapped</Badge>
            )}
            {job.status !== "idle" && job.status !== "error" && (
              <Badge color="bg-violet-500/20 text-violet-300">{job.status}</Badge>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-8">
        {/* Tabs */}
        <div className="flex gap-1 rounded-xl bg-white/5 border border-white/10 p-1 mb-6">
          {TABS.map(tab => {
            const isDone =
              (tab.id === "setup" && setupDone) ||
              (tab.id === "mapping" && mappingDone) ||
              (tab.id === "run" && job.status === "done");
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "flex-1 flex items-center justify-center gap-1.5 rounded-lg py-2.5 px-2 text-sm font-semibold transition-all duration-200",
                  activeTab === tab.id
                    ? "bg-white text-violet-700 shadow-sm"
                    : isDone
                    ? "text-emerald-400 hover:bg-white/10"
                    : "text-slate-400 hover:bg-white/10"
                )}
              >
                <span>{isDone && activeTab !== tab.id ? "✅" : tab.icon}</span>
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="bg-white rounded-2xl border border-slate-200 shadow-xl p-6">
          {activeTab === "setup" && (
            <SetupTab
              apiId={apiId} setApiId={setApiId}
              apiHash={apiHash} setApiHash={setApiHash}
              phone={phone} setPhone={setPhone}
              srcChannel={srcChannel} setSrcChannel={setSrcChannel}
              dstChannel={dstChannel} setDstChannel={setDstChannel}
              deleteFromSrc={deleteFromSrc} setDeleteFromSrc={setDeleteFromSrc}
              sessionString={sessionString} setSessionString={setSessionString}
            />
          )}
          {activeTab === "mapping" && (
            <MappingTab
              oldNames={oldNames} setOldNames={setOldNames}
              newNames={newNames} setNewNames={setNewNames}
            />
          )}
          {activeTab === "run" && (
            <RunTab
              job={job}
              mappingCount={mappings.length}
              ready={ready}
              onStart={handleStart}
              onSubmitOtp={handleSubmitOtp}
              otp={otp}
              setOtp={setOtp}
              srcChannel={srcChannel}
              dstChannel={dstChannel}
              deleteFromSrc={deleteFromSrc}
            />
          )}
          {activeTab === "guide" && <GuideTab />}
        </div>

        {/* Next/Prev nav */}
        <div className="flex justify-between mt-4">
          <button
            onClick={() => {
              const idx = TABS.findIndex(t => t.id === activeTab);
              if (idx > 0) setActiveTab(TABS[idx - 1].id);
            }}
            disabled={activeTab === TABS[0].id}
            className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-slate-300 hover:bg-white/10 border border-white/10 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          >
            ← Back
          </button>
          <button
            onClick={() => {
              const idx = TABS.findIndex(t => t.id === activeTab);
              if (idx < TABS.length - 1) setActiveTab(TABS[idx + 1].id);
            }}
            disabled={activeTab === TABS[TABS.length - 1].id}
            className="flex items-center gap-2 rounded-lg bg-violet-600 text-white px-5 py-2 text-sm font-semibold hover:bg-violet-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed shadow-lg"
          >
            Next →
          </button>
        </div>
      </main>

      <footer className="text-center pb-8 text-xs text-slate-500">
        🔒 Your credentials are only used on this server to run Telethon. No files are downloaded or uploaded.
      </footer>
    </div>
  );
}
