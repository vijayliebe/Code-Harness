import json
import re

def safe_json(obj):
    s = json.dumps(obj)
    return re.sub(r'</script>', r'<\/script>', s, flags=re.IGNORECASE)

def get_html_template(points, files, types, repos, repo_graph):
    points_json     = safe_json(points)
    files_json      = safe_json(files)
    types_json      = safe_json(types)
    repos_json      = safe_json(repos)
    repo_graph_json = safe_json(repo_graph)


    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code Harness · Vector Space Visualizer</title>

    <!-- Tailwind CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{
                    fontFamily: {{
                        sans: ['Inter', 'sans-serif'],
                        mono: ['Fira Code', 'JetBrains Mono', 'monospace']
                    }},
                    colors: {{
                        slate: {{ 850: '#131c2e', 950: '#070a13' }}
                    }}
                }}
            }}
        }}
    </script>

    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">

    <!-- Plotly -->
    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>

    <!-- Prism -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-javascript.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-typescript.min.js"></script>

    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html {{ height: 100%; }}
        body {{
            height: 100%;
            font-family: 'Inter', sans-serif;
            background: radial-gradient(ellipse at 75% 15%, rgba(99,102,241,.13), transparent 55%),
                        radial-gradient(ellipse at 10% 85%, rgba(168,85,247,.09), transparent 50%),
                        #070a13;
            color: #e2e8f0;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }}
        pre[class*="language-"] {{
            margin: 0;
            background: #0d111d !important;
            border-radius: 8px;
        }}
        ::-webkit-scrollbar {{ width: 5px; height: 5px; }}
        ::-webkit-scrollbar-track {{ background: #090d16; }}
        ::-webkit-scrollbar-thumb {{ background: #1e293b; border-radius: 3px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #334155; }}
        select option {{ background: #0d111d; color: #cbd5e1; }}
        .plotly-graph-div .modebar-container {{ margin-right: 8px !important; }}
    </style>
</head>
<body class="dark">

<!-- ═══ NAVBAR ═══════════════════════════════════════════════════════════════ -->
<header style="flex-shrink:0;z-index:50"
        class="border-b border-slate-800/80 bg-slate-950/50 backdrop-blur-md px-5 py-3 flex items-center justify-between">
    <div class="flex items-center gap-3">
        <div class="bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 text-white font-extrabold px-2.5 py-1 text-xs tracking-widest rounded-md shadow-lg shadow-indigo-500/15 uppercase">
            Harness
        </div>
        <div>
            <h1 class="text-sm font-semibold text-white flex items-center gap-2">
                Vector Space Visualizer
                <span class="px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 text-[10px] font-mono border border-indigo-500/20">v2.1</span>
            </h1>
            <p class="text-[11px] text-slate-500">2D PCA projection of indexed code chunks</p>
        </div>
    </div>
    <div class="flex items-center gap-2">
        <div class="text-[11px] bg-slate-900/50 px-2.5 py-1 rounded-lg border border-slate-800 flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse"></span>
            <span class="text-slate-400">Chunks:</span>
            <span id="stats-chunks" class="font-mono font-semibold text-indigo-400">0</span>
        </div>
        <div class="text-[11px] bg-slate-900/50 px-2.5 py-1 rounded-lg border border-slate-800 flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-purple-400"></span>
            <span class="text-slate-400">Files:</span>
            <span id="stats-files" class="font-mono font-semibold text-purple-400">0</span>
        </div>
        <div id="stats-repos-badge" class="hidden text-[11px] bg-slate-900/50 px-2.5 py-1 rounded-lg border border-slate-800 items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
            <span class="text-slate-400">Repos:</span>
            <span id="stats-repos" class="font-mono font-semibold text-emerald-400">0</span>
        </div>
    </div>
</header>

<!-- ═══ MAIN ══════════════════════════════════════════════════════════════════ -->
<main style="flex:1;min-height:0;display:flex;overflow:hidden;">

    <!-- ── LEFT: filters + plot ─────────────────────────────────────────────── -->
    <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:10px;padding:12px;overflow:hidden;">

        <!-- Filter bar -->
        <div style="flex-shrink:0"
             class="bg-slate-900/20 border border-slate-800/70 backdrop-blur-md rounded-xl px-3 py-2.5 flex flex-wrap gap-2 items-center">
            <!-- Search -->
            <div style="flex:1;min-width:200px" class="relative">
                <span class="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-slate-500">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                    </svg>
                </span>
                <input type="text" id="search-input"
                       placeholder="Search chunks, files, symbols…"
                       class="w-full bg-slate-950/80 border border-slate-800 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/20 transition-all">
            </div>
            <!-- Repo -->
            <div id="repo-select-container" style="width:155px;display:none;">
                <select id="repo-select"
                        class="w-full bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-indigo-500 transition-colors">
                    <option value="all">All Repositories</option>
                </select>
            </div>
            <!-- Type -->
            <div style="width:140px">
                <select id="type-select"
                        class="w-full bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-indigo-500 transition-colors">
                    <option value="all">All Types</option>
                </select>
            </div>
            <!-- File -->
            <div style="width:200px">
                <select id="file-select"
                        class="w-full bg-slate-950/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-indigo-500 transition-colors">
                    <option value="all">All Files</option>
                </select>
            </div>
            <!-- Reset -->
            <button id="reset-filters"
                    class="bg-slate-800/60 hover:bg-slate-700 text-slate-400 hover:text-white border border-slate-800 px-3 py-1.5 rounded-lg text-xs transition-all">
                Reset
            </button>
            <!-- Count -->
            <div class="ml-auto text-[10px] text-slate-600 font-mono whitespace-nowrap">
                <span id="filtered-count" class="text-slate-400 font-semibold">0</span>
                / <span id="total-count">0</span>
            </div>
        </div>

        <!-- Plot container — position:relative wrapper so absolute child fills it -->
        <div style="flex:1;position:relative;min-height:0;border-radius:16px;overflow:hidden;"
             class="border border-slate-800/60 bg-slate-900/10">

            <!-- Plotly target — MUST be position:absolute to avoid circular flex-height -->
            <div id="plotly-div" style="position:absolute;inset:0;"></div>

            <!-- Loading overlay -->
            <div id="plot-loading"
                 style="position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;gap:10px;background:rgba(7,10,19,.75);backdrop-filter:blur(4px);z-index:10;">
                <div class="animate-spin rounded-full h-7 w-7 border-b-2 border-indigo-500"></div>
                <span class="text-xs text-slate-500 font-mono">Filtering…</span>
            </div>

            <!-- Empty state -->
            <div id="plot-empty"
                 style="position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;gap:6px;z-index:5;">
                <svg class="w-8 h-8 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                          d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                </svg>
                <p class="text-xs text-slate-600">No chunks match your filters</p>
            </div>
        </div>
    </div>

    <!-- ── RIGHT: sidebar ───────────────────────────────────────────────────── -->
    <div style="width:440px;flex-shrink:0;display:flex;flex-direction:column;overflow:hidden;border-left:1px solid rgba(30,41,59,.7);"
         class="bg-slate-950/30 backdrop-blur-md">

        <!-- Tabs -->
        <div style="flex-shrink:0;display:flex;border-bottom:1px solid rgba(30,41,59,.7);"
             class="bg-slate-900/20">
            <button id="tab-inspector"
                    class="flex-1 py-3 text-center text-xs font-semibold uppercase tracking-wider border-b-2 text-indigo-400 border-indigo-500 focus:outline-none transition-all">
                Chunk Inspector
            </button>
            <button id="tab-relationships"
                    class="flex-1 py-3 text-center text-xs font-semibold uppercase tracking-wider border-b-2 text-slate-600 border-transparent hover:text-slate-300 focus:outline-none transition-all">
                Repo Connections
            </button>
        </div>

        <!-- ── Inspector tab ─────────────────────────────────────────────────── -->
        <div id="content-inspector" style="flex:1;overflow-y:auto;min-height:0;padding:14px;">

            <!-- Idle placeholder -->
            <div id="inspector-placeholder"
                 style="height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:32px;">
                <div class="w-12 h-12 rounded-xl bg-slate-900/60 border border-slate-800 flex items-center justify-center mb-4 text-slate-500">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                              d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122"/>
                    </svg>
                </div>
                <h4 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Inspector Idle</h4>
                <p class="text-[11px] text-slate-600 max-w-[250px] leading-relaxed">
                    Hover or click any node in the vector space to inspect its source code.
                </p>
            </div>

            <!-- Active details (hidden until a point is selected) -->
            <div id="inspector-details" style="display:none;flex-direction:column;gap:12px;">

                <!-- Indicator + chunk ID -->
                <div style="display:flex;align-items:center;gap:8px;">
                    <div id="detail-indicator"
                         style="width:10px;height:10px;border-radius:50%;background:#64748b;flex-shrink:0;"></div>
                    <span id="detail-id" class="text-[10px] font-mono text-slate-500 truncate"></span>
                </div>

                <!-- Metadata grid -->
                <div class="grid grid-cols-2 gap-2 text-[11px]">
                    <div class="bg-slate-900/50 border border-slate-800 rounded-lg px-3 py-2 flex flex-col">
                        <span class="text-slate-500 font-medium mb-0.5">Entity Type</span>
                        <span id="detail-entity-type" class="font-bold uppercase tracking-wide"></span>
                    </div>
                    <div class="bg-slate-900/50 border border-slate-800 rounded-lg px-3 py-2 flex flex-col">
                        <span class="text-slate-500 font-medium mb-0.5">Lines</span>
                        <span id="detail-lines" class="font-mono font-semibold text-indigo-400"></span>
                    </div>
                    <div class="col-span-2 bg-slate-900/50 border border-slate-800 rounded-lg px-3 py-2 flex flex-col">
                        <span class="text-slate-500 font-medium mb-0.5">Symbol</span>
                        <span id="detail-entity-name" class="font-mono font-bold text-indigo-300 truncate"></span>
                    </div>
                    <div class="col-span-2 bg-slate-900/50 border border-slate-800 rounded-lg px-3 py-2 flex flex-col">
                        <span class="text-slate-500 font-medium mb-0.5">File</span>
                        <span id="detail-file-path"
                              class="font-mono text-[10px] text-slate-300 truncate hover:text-white cursor-pointer transition-colors"
                              title=""></span>
                    </div>
                    <div id="detail-repo-card"
                         class="col-span-2 bg-slate-900/50 border border-slate-800 rounded-lg px-3 py-2 flex-col hidden">
                        <span class="text-slate-500 font-medium mb-0.5">Repository</span>
                        <span id="detail-repo-name" class="font-mono font-semibold text-emerald-400 truncate"></span>
                    </div>
                </div>

                <!-- Source snippet -->
                <div style="display:flex;flex-direction:column;gap:6px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span class="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Source</span>
                        <button id="copy-code-btn"
                                class="text-[11px] text-indigo-400 hover:text-indigo-300 transition-colors flex items-center gap-1">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                                      d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"/>
                            </svg>
                            Copy
                        </button>
                    </div>
                    <div style="border:1px solid #1e293b;border-radius:10px;background:#0d111d;overflow:hidden;">
                        <pre style="max-height:380px;overflow:auto;margin:0;padding:12px;font-size:11px;line-height:1.65;"><code id="detail-code-block" class="language-python"></code></pre>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── Repo Connections tab ──────────────────────────────────────────── -->
        <div id="content-relationships" style="flex:1;overflow-y:auto;min-height:0;padding:14px;display:none;">
            <div class="mb-4">
                <h4 class="text-xs font-bold text-white uppercase tracking-wider">Indexed Repositories</h4>
                <p class="text-[11px] text-slate-500 mt-0.5">Cross-repo import and symbol relationships.</p>
            </div>
            <div id="relationships-container" class="space-y-3"></div>
        </div>

    </div><!-- /sidebar -->
</main>

<!-- ═══ FOOTER ════════════════════════════════════════════════════════════════ -->
<footer style="flex-shrink:0;z-index:50"
        class="border-t border-slate-800/70 bg-slate-950/60 px-5 py-2 flex justify-between items-center text-[10px] text-slate-600">
    <span>Code Harness · Vector Space Visualizer</span>
    <span>Powered by <span class="text-indigo-400 font-mono">Plotly.js</span> &amp; <span class="text-purple-400 font-mono">NumPy PCA</span></span>
</footer>

<!-- ═══ APP SCRIPT ════════════════════════════════════════════════════════════ -->
<script>
// ── Data injected from Python ─────────────────────────────────────────────────
const points      = {points_json};
const uniqueFiles = {files_json};
const uniqueTypes = {types_json};
const uniqueRepos = {repos_json};
const repoGraph   = {repo_graph_json};

// ── DOM references ────────────────────────────────────────────────────────────
const el = id => document.getElementById(id);
const searchInput          = el('search-input');
const repoSelect           = el('repo-select');
const repoSelectContainer  = el('repo-select-container');
const typeSelect           = el('type-select');
const fileSelect           = el('file-select');
const resetBtn             = el('reset-filters');
const filteredCountEl      = el('filtered-count');
const totalCountEl         = el('total-count');
const statsChunks          = el('stats-chunks');
const statsFiles           = el('stats-files');
const statsRepos           = el('stats-repos');
const statsReposBadge      = el('stats-repos-badge');
const tabInspector         = el('tab-inspector');
const tabRelationships     = el('tab-relationships');
const contentInspector     = el('content-inspector');
const contentRelationships = el('content-relationships');
const relationshipsContainer = el('relationships-container');
const inspectorPlaceholder = el('inspector-placeholder');
const inspectorDetails     = el('inspector-details');
const detailIndicator      = el('detail-indicator');
const detailId             = el('detail-id');
const detailRepoCard       = el('detail-repo-card');
const detailRepoName       = el('detail-repo-name');
const detailFilePath       = el('detail-file-path');
const detailEntityName     = el('detail-entity-name');
const detailEntityType     = el('detail-entity-type');
const detailLines          = el('detail-lines');
const detailCodeBlock      = el('detail-code-block');
const copyCodeBtn          = el('copy-code-btn');
const plotLoadingEl        = el('plot-loading');
const plotEmptyEl          = el('plot-empty');

// ── Type colours ──────────────────────────────────────────────────────────────
const typeColors = {{
    file:          '#6366f1',
    class:         '#a855f7',
    function:      '#10b981',
    method:        '#06b6d4',
    documentation: '#f59e0b',
    unknown:       '#64748b'
}};

// ── Initialise stats ──────────────────────────────────────────────────────────
statsChunks.textContent    = points.length;
statsFiles.textContent     = uniqueFiles.length;
totalCountEl.textContent   = points.length;
filteredCountEl.textContent = points.length;

if (uniqueRepos.length > 1) {{
    statsRepos.textContent = uniqueRepos.length;
    statsReposBadge.style.display = 'flex';
    repoSelectContainer.style.display = 'block';
}}

// ── Populate filter selects ───────────────────────────────────────────────────
uniqueRepos.forEach(r => {{
    const o = new Option(r, r); repoSelect.add(o);
}});
uniqueTypes.forEach(t => {{
    const o = new Option(t.toUpperCase(), t); typeSelect.add(o);
}});
uniqueFiles.forEach(f => {{
    const o = new Option(f, f); fileSelect.add(o);
}});

// ── Tab switching ─────────────────────────────────────────────────────────────
const CLS_ACTIVE   = 'flex-1 py-3 text-center text-xs font-semibold uppercase tracking-wider border-b-2 text-indigo-400 border-indigo-500 focus:outline-none transition-all';
const CLS_INACTIVE = 'flex-1 py-3 text-center text-xs font-semibold uppercase tracking-wider border-b-2 text-slate-600 border-transparent hover:text-slate-300 focus:outline-none transition-all';

function activateTab(tab) {{
    if (tab === 'inspector') {{
        tabInspector.className = CLS_ACTIVE;
        tabRelationships.className = CLS_INACTIVE;
        contentInspector.style.display = 'block';
        contentRelationships.style.display = 'none';
    }} else {{
        tabRelationships.className = CLS_ACTIVE;
        tabInspector.className = CLS_INACTIVE;
        contentRelationships.style.display = 'block';
        contentInspector.style.display = 'none';
    }}
}}
tabInspector.addEventListener('click',     () => activateTab('inspector'));
tabRelationships.addEventListener('click', () => activateTab('relationships'));

// ── State ─────────────────────────────────────────────────────────────────────
let filteredPoints = [...points];
let plotReady      = false;

// ── Plotly layout ─────────────────────────────────────────────────────────────
function getLayout() {{
    return {{
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor:  'rgba(0,0,0,0)',
        margin: {{ l:30, r:30, t:40, b:20 }},
        hovermode: 'closest',
        dragmode: 'pan',
        xaxis: {{ showgrid:true, gridcolor:'rgba(51,65,85,.18)', zeroline:false, showticklabels:false }},
        yaxis: {{ showgrid:true, gridcolor:'rgba(51,65,85,.18)', zeroline:false, showticklabels:false }},
        legend: {{
            font: {{ color:'#94a3b8', size:10 }},
            orientation:'h', yanchor:'bottom', y:1.02, xanchor:'right', x:1
        }}
    }};
}}

// ── Render / update plot ──────────────────────────────────────────────────────
function renderPlot() {{
    plotLoadingEl.style.display = 'flex';
    plotEmptyEl.style.display   = 'none';

    // Defer actual render so the loading spinner can paint first
    requestAnimationFrame(() => {{
        if (filteredPoints.length === 0) {{
            Plotly.purge('plotly-div');
            plotLoadingEl.style.display = 'none';
            plotEmptyEl.style.display   = 'flex';
            plotReady = false;
            return;
        }}

        // Group by entity type
        const groups = {{}};
        filteredPoints.forEach(p => {{
            const key = p.entity_type || 'unknown';
            if (!groups[key]) {{
                groups[key] = {{
                    x: [], y: [], text: [], customdata: [],
                    mode: 'markers',
                    name: key.toUpperCase(),
                    hovertemplate: '%{{text}}<extra></extra>',
                    marker: {{
                        size: 7,
                        color: typeColors[key] || typeColors.unknown,
                        opacity: 0.78,
                        line: {{ color:'#090d16', width:0.8 }}
                    }},
                    type: 'scatter'
                }};
            }}
            groups[key].x.push(p.x);
            groups[key].y.push(p.y);
            groups[key].text.push(
                '<b>' + escHtml(p.entity_name) + '</b><br>'
                + '<span style="color:#94a3b8">' + escHtml(p.file_path) + '</span><br>'
                + '<span style="color:#6366f1">L' + p.start_line + '–L' + p.end_line + '</span>'
            );
            groups[key].customdata.push(p.id);
        }});

        const plotData = Object.values(groups);
        Plotly.react('plotly-div', plotData, getLayout(), {{ responsive:true, displaylogo:false, scrollZoom:true }});
        plotLoadingEl.style.display = 'none';

        // Wire up events only once
        if (!plotReady) {{
            const div = el('plotly-div');
            div.on('plotly_hover', evt => {{ if (evt.points && evt.points[0]) showDetails(evt.points[0].customdata); }});
            div.on('plotly_click', evt => {{ if (evt.points && evt.points[0]) showDetails(evt.points[0].customdata); }});
            plotReady = true;
        }}

        // Force correct dimensions after layout settles
        setTimeout(() => Plotly.Plots.resize('plotly-div'), 80);
    }});
}}

// ── Filtering ─────────────────────────────────────────────────────────────────
function applyFilters() {{
    const q   = searchInput.value.toLowerCase();
    const rep = repoSelect.value;
    const typ = typeSelect.value;
    const fil = fileSelect.value;

    filteredPoints = points.filter(p => {{
        if (q && !p.content.toLowerCase().includes(q)
              && !p.file_path.toLowerCase().includes(q)
              && !p.entity_name.toLowerCase().includes(q)) return false;
        if (rep !== 'all' && p.repo_name !== rep) return false;
        if (typ !== 'all' && p.entity_type !== typ) return false;
        if (fil !== 'all' && p.file_path !== fil) return false;
        return true;
    }});

    filteredCountEl.textContent = filteredPoints.length;
    renderPlot();
}}

searchInput.addEventListener('input',  applyFilters);
repoSelect.addEventListener('change',  applyFilters);
typeSelect.addEventListener('change',  applyFilters);
fileSelect.addEventListener('change',  applyFilters);
resetBtn.addEventListener('click', () => {{
    searchInput.value = ''; repoSelect.value = 'all';
    typeSelect.value  = 'all'; fileSelect.value = 'all';
    applyFilters();
}});

// ── Detail panel ──────────────────────────────────────────────────────────────
function showDetails(pointId) {{
    const p = points.find(item => item.id === pointId);
    if (!p) return;

    activateTab('inspector');
    inspectorPlaceholder.style.display = 'none';
    inspectorDetails.style.display     = 'flex';

    const col = typeColors[p.entity_type] || typeColors.unknown;
    detailIndicator.style.backgroundColor = col;
    detailId.textContent = (p.chunk_id || p.id || '').substring(0, 24) + ((p.chunk_id || '').length > 24 ? '…' : '');

    if (uniqueRepos.length > 1) {{
        detailRepoCard.style.display = 'flex';
        detailRepoName.textContent   = p.repo_name || 'unknown';
    }} else {{
        detailRepoCard.style.display = 'none';
    }}

    detailFilePath.textContent  = p.file_path;
    detailFilePath.title        = p.file_path;
    detailEntityName.textContent = p.entity_name;
    detailEntityType.textContent = (p.entity_type || 'unknown').toUpperCase();
    detailEntityType.style.color = col;
    detailLines.textContent     = 'L' + p.start_line + ' – L' + p.end_line;

    let lang = 'language-python';
    const fp = p.file_path || '';
    if      (fp.endsWith('.js')  || fp.endsWith('.jsx')) lang = 'language-javascript';
    else if (fp.endsWith('.ts')  || fp.endsWith('.tsx')) lang = 'language-typescript';
    else if (fp.endsWith('.go'))                         lang = 'language-go';
    else if (fp.endsWith('.rs'))                         lang = 'language-rust';

    detailCodeBlock.className   = lang;
    detailCodeBlock.textContent = p.content;
    Prism.highlightElement(detailCodeBlock);
}}


// ── Copy code ─────────────────────────────────────────────────────────────────
copyCodeBtn.addEventListener('click', () => {{
    navigator.clipboard.writeText(detailCodeBlock.textContent).then(() => {{
        const orig = copyCodeBtn.innerHTML;
        copyCodeBtn.innerHTML = '<svg class="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg><span class="text-emerald-400">Copied!</span>';
        setTimeout(() => {{ copyCodeBtn.innerHTML = orig; }}, 2000);
    }});
}});

// ── Repo connections panel ────────────────────────────────────────────────────
function renderRepoGraph() {{
    if (!repoGraph || !repoGraph.nodes || repoGraph.nodes.length === 0) return;

    let html = '';
    repoGraph.nodes.forEach(repo => {{
        const edges = (repoGraph.edges || []).filter(e => e.source === repo.name || e.target === repo.name);
        let relHtml = edges.length === 0
            ? '<p class="text-[10px] text-slate-600 italic">No connected repositories</p>'
            : edges.map(e => {{
                const other  = e.source === repo.name ? e.target : e.source;
                const rel    = e.relationship || 'related';
                const parts  = [];
                if ((e.modules || []).length)              parts.push('shared: ' + e.modules.slice(0,3).join(', '));
                if ((e.depends_on_modules || []).length)   parts.push('via: '    + e.depends_on_modules.slice(0,2).join(', '));
                if ((e.shared_entities || []).length)      parts.push('sym: '    + e.shared_entities.slice(0,2).join(', '));
                const detail = parts.length ? ' (' + parts.join('; ') + ')' : '';
                return '<div style="display:flex;gap:4px;padding:2px 0" class="text-[10px]">'
                     + '<span class="text-indigo-400 font-semibold shrink-0">→ ' + escHtml(other) + '</span>'
                     + '<span class="text-slate-500 font-mono truncate">' + escHtml(rel + detail) + '</span></div>';
            }}).join('');

        html += '<div class="bg-slate-900/40 border border-slate-800 rounded-xl p-3 space-y-2 hover:border-slate-700 transition-colors">'
              + '<div style="display:flex;justify-content:space-between;align-items:center;padding-bottom:6px;border-bottom:1px solid #1e293b;">'
              + '<span class="text-xs font-bold text-white">' + escHtml(repo.name) + '</span>'
              + '<span class="text-[10px] text-slate-500 font-mono">' + (repo.entity_count || 0) + ' chunks</span></div>'
              + '<div style="display:flex;gap:6px;flex-wrap:wrap;">'
              + '<span class="text-[9px] font-mono bg-indigo-500/10 text-indigo-400 px-1.5 py-0.5 rounded border border-indigo-500/15">↓ ' + (repo.imports || []).length + ' imports</span>'
              + '<span class="text-[9px] font-mono bg-purple-500/10 text-purple-400 px-1.5 py-0.5 rounded border border-purple-500/15">↑ ' + (repo.exports || []).length + ' exports</span>'
              + '</div><div class="space-y-0.5">'
              + '<div class="text-[9px] text-slate-600 font-semibold uppercase tracking-wider mb-1">Relations</div>'
              + relHtml + '</div></div>';
    }});
    relationshipsContainer.innerHTML = html;
}}

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// ── Window resize ─────────────────────────────────────────────────────────────
window.addEventListener('resize', () => Plotly.Plots.resize('plotly-div'));

// ── Boot ──────────────────────────────────────────────────────────────────────
renderPlot();
renderRepoGraph();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # This module is imported by visualize.py
    pass
