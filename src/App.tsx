import { useState, useEffect } from 'react';
import axios from 'axios';
import { Terminal, FileText, AlertCircle, Play, Settings, ExternalLink, StopCircle, RefreshCw, Key } from 'lucide-react';

export function App() {
  const [apiId, setApiId] = useState('');
  const [apiHash, setApiHash] = useState('');
  const [sessionString, setSessionString] = useState('');
  const [sourceChatId, setSourceChatId] = useState('');
  const [destChatId, setDestChatId] = useState('');
  const [newFilenames, setNewFilenames] = useState('');
  
  const [isRunning, setIsRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState(0);
  const [totalFiles, setTotalFiles] = useState(0);
  const [currentFile, setCurrentFile] = useState('');
  
  const [activeTab, setActiveTab] = useState<'config' | 'files' | 'run'>('config');

  const startTask = async () => {
    const namesList = newFilenames
      .split('\n')
      .map(n => n.trim())
      .filter(n => n.length > 0);

    if (namesList.length === 0) {
      alert("Please provide at least one filename.");
      return;
    }

    if (!apiId || !apiHash || !sessionString || !sourceChatId || !destChatId) {
      alert("Please fill in all configuration fields.");
      return;
    }

    try {
      await axios.post('/api/start', {
        api_id: parseInt(apiId),
        api_hash: apiHash,
        session_string: sessionString,
        source_chat_id: sourceChatId,
        dest_chat_id: destChatId,
        filenames: namesList,
        start_index: 0
      });
      setIsRunning(true);
      setActiveTab('run');
    } catch (e: any) {
      alert("Failed to start task: " + (e.response?.data?.detail || e.message));
    }
  };

  const stopTask = async () => {
    try {
      await axios.post('/api/stop');
      alert("Stop signal sent. Task will stop after current file.");
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    let interval: any;
    if (activeTab === 'run') {
      const fetchStatus = async () => {
        try {
          const res = await axios.get('/api/status');
          setIsRunning(res.data.is_running);
          setLogs(res.data.logs || []);
          setProgress(res.data.progress || 0);
          setTotalFiles(res.data.total || 0);
          setCurrentFile(res.data.current_file || '');
        } catch (e) {
          console.error("Failed to fetch status", e);
        }
      };
      
      fetchStatus();
      interval = setInterval(fetchStatus, 2000);
    }
    return () => clearInterval(interval);
  }, [activeTab]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="bg-blue-600 text-white p-2 rounded-lg">
              <Terminal size={20} />
            </div>
            <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-700 to-indigo-600">
              Telegram Bulk Renamer
            </h1>
          </div>
          <div className="flex items-center gap-4">
             {isRunning && (
               <span className="flex items-center gap-2 text-sm text-green-600 bg-green-50 px-3 py-1 rounded-full border border-green-200">
                 <span className="relative flex h-2 w-2">
                   <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                   <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                 </span>
                 Task Running
               </span>
             )}
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          
          {/* Left Sidebar - Navigation */}
          <div className="lg:col-span-3 space-y-2">
            <button
              onClick={() => setActiveTab('config')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all ${
                activeTab === 'config' 
                  ? 'bg-white shadow-sm ring-1 ring-slate-200 text-blue-700 font-medium' 
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              <Settings size={18} />
              Configuration
            </button>
            <button
              onClick={() => setActiveTab('files')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all ${
                activeTab === 'files' 
                  ? 'bg-white shadow-sm ring-1 ring-slate-200 text-blue-700 font-medium' 
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              <FileText size={18} />
              File Mapping
            </button>
            <button
              onClick={() => setActiveTab('run')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all ${
                activeTab === 'run' 
                  ? 'bg-white shadow-sm ring-1 ring-slate-200 text-blue-700 font-medium' 
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              <Play size={18} />
              Run Task
            </button>
            
            <div className="mt-8 bg-blue-50 p-4 rounded-xl border border-blue-100 text-sm text-blue-800">
              <div className="flex gap-2 items-start mb-2">
                <AlertCircle size={16} className="mt-0.5 shrink-0" />
                <span className="font-semibold">How it works</span>
              </div>
              <p className="leading-relaxed opacity-90">
                1. Configure API & Session.<br/>
                2. Input filenames.<br/>
                3. Start the process.
              </p>
              <p className="mt-2 text-xs text-blue-600/80">
                Requires a Pyrogram Session String.
              </p>
            </div>
          </div>

          {/* Main Content */}
          <div className="lg:col-span-9">
            
            {/* CONFIGURATION TAB */}
            <div className={activeTab === 'config' ? 'block' : 'hidden'}>
              <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 sm:p-8">
                <div className="flex items-center justify-between mb-6">
                  <h2 className="text-xl font-bold text-slate-900">API Configuration</h2>
                  <a href="https://my.telegram.org/apps" target="_blank" rel="noreferrer" 
                     className="text-sm text-blue-600 hover:underline flex items-center gap-1">
                    Get API Credentials <ExternalLink size={12} />
                  </a>
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-slate-700">API ID</label>
                    <input
                      type="text"
                      value={apiId}
                      onChange={(e) => setApiId(e.target.value)}
                      placeholder="e.g. 12345678"
                      className="w-full px-4 py-2.5 rounded-lg border border-slate-300 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-slate-700">API Hash</label>
                    <input
                      type="text"
                      value={apiHash}
                      onChange={(e) => setApiHash(e.target.value)}
                      placeholder="e.g. a1b2c3d4e5f6..."
                      className="w-full px-4 py-2.5 rounded-lg border border-slate-300 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                    />
                  </div>
                  
                  <div className="col-span-1 md:col-span-2 space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-sm font-medium text-slate-700">Pyrogram Session String</label>
                      <a href="https://replit.com/@SpEcHiDe/Generate-Pyrogram-String-Session" target="_blank" rel="noreferrer" className="text-xs text-blue-500 hover:underline flex items-center gap-1">
                         Generate String <ExternalLink size={10} />
                      </a>
                    </div>
                    <div className="relative">
                      <Key className="absolute left-3 top-3 text-slate-400" size={18} />
                      <input
                        type="text"
                        value={sessionString}
                        onChange={(e) => setSessionString(e.target.value)}
                        placeholder="Paste your long session string here..."
                        className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-slate-300 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all font-mono text-xs"
                      />
                    </div>
                    <p className="text-xs text-slate-500">
                      Required for server-side authentication. Do not share this string.
                    </p>
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium text-slate-700">Source Channel ID</label>
                    <input
                      type="text"
                      value={sourceChatId}
                      onChange={(e) => setSourceChatId(e.target.value)}
                      placeholder="e.g. -100123456789"
                      className="w-full px-4 py-2.5 rounded-lg border border-slate-300 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-slate-700">Destination Channel ID</label>
                    <input
                      type="text"
                      value={destChatId}
                      onChange={(e) => setDestChatId(e.target.value)}
                      placeholder="e.g. -100987654321"
                      className="w-full px-4 py-2.5 rounded-lg border border-slate-300 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                    />
                  </div>
                </div>

                <div className="mt-8 flex justify-end">
                  <button 
                    onClick={() => setActiveTab('files')}
                    className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2"
                  >
                    Next: File Mapping <Play size={16} />
                  </button>
                </div>
              </div>
            </div>

            {/* FILES TAB */}
            <div className={activeTab === 'files' ? 'block' : 'hidden'}>
              <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 sm:p-8 h-[calc(100vh-140px)] min-h-[500px] flex flex-col">
                <div className="mb-4">
                  <h2 className="text-xl font-bold text-slate-900">New Filenames List</h2>
                  <p className="text-slate-500 mt-1">
                    Paste your new filenames here. One per line.
                  </p>
                </div>
                
                <textarea
                  value={newFilenames}
                  onChange={(e) => setNewFilenames(e.target.value)}
                  placeholder="Mahabharat.2013.S01E001.mkv&#10;Mahabharat.2013.S01E002.mkv&#10;..."
                  className="flex-1 w-full p-4 rounded-xl border border-slate-300 font-mono text-sm leading-relaxed focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none resize-none"
                />

                <div className="mt-4 flex items-center justify-between">
                  <div className="text-sm font-medium text-slate-600 bg-slate-100 px-3 py-1 rounded-md">
                    Line count: {newFilenames.split('\n').filter(l => l.trim()).length}
                  </div>
                  <button 
                    onClick={() => setActiveTab('run')}
                    className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2"
                  >
                    Next: Run Task <Play size={16} />
                  </button>
                </div>
              </div>
            </div>

            {/* RUN TAB */}
            <div className={activeTab === 'run' ? 'block' : 'hidden'}>
              <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 sm:p-8 min-h-[500px] flex flex-col">
                 <div className="flex items-center justify-between mb-6">
                   <h2 className="text-xl font-bold text-slate-900">Task Control</h2>
                   <div className="flex gap-2">
                     {!isRunning ? (
                       <button 
                         onClick={startTask}
                         className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2"
                       >
                         <Play size={16} /> Start
                       </button>
                     ) : (
                       <button 
                         onClick={stopTask}
                         className="bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2"
                       >
                         <StopCircle size={16} /> Stop
                       </button>
                     )}
                   </div>
                 </div>

                 {/* Progress Stats */}
                 <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                    <div className="bg-slate-50 p-4 rounded-xl border border-slate-100">
                      <div className="text-sm text-slate-500 mb-1">Status</div>
                      <div className={`text-lg font-semibold ${isRunning ? 'text-green-600' : 'text-slate-700'}`}>
                        {isRunning ? 'Running' : 'Idle'}
                      </div>
                    </div>
                    <div className="bg-slate-50 p-4 rounded-xl border border-slate-100">
                      <div className="text-sm text-slate-500 mb-1">Progress</div>
                      <div className="text-lg font-semibold text-slate-900">
                        {progress} / {totalFiles}
                      </div>
                    </div>
                    <div className="bg-slate-50 p-4 rounded-xl border border-slate-100">
                      <div className="text-sm text-slate-500 mb-1">Current File</div>
                      <div className="text-sm font-medium text-slate-900 truncate" title={currentFile}>
                        {currentFile || '-'}
                      </div>
                    </div>
                 </div>

                 {/* Logs */}
                 <div className="flex-1 bg-[#1e1e1e] rounded-xl overflow-hidden flex flex-col">
                   <div className="px-4 py-2 bg-[#2d2d2d] text-slate-400 text-xs uppercase font-semibold tracking-wider flex justify-between items-center">
                     <span>Console Logs</span>
                     <RefreshCw size={12} className={isRunning ? "animate-spin" : ""} />
                   </div>
                   <div className="flex-1 p-4 overflow-y-auto font-mono text-xs text-slate-300 space-y-1 max-h-[400px]">
                     {logs.length === 0 ? (
                       <span className="text-slate-600 italic">Waiting for logs...</span>
                     ) : (
                       logs.map((log, i) => (
                         <div key={i} className="break-all border-b border-white/5 pb-0.5 mb-0.5 last:border-0">
                           <span className="text-slate-500 select-none mr-2">[{new Date().toLocaleTimeString()}]</span>
                           {log}
                         </div>
                       ))
                     )}
                   </div>
                 </div>
              </div>
            </div>

          </div>
        </div>
      </main>
    </div>
  );
}
