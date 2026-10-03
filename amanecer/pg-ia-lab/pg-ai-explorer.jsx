import { useState, useCallback } from "react";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from "recharts";

const COLORS = ["#6366f1", "#22d3ee", "#f59e0b", "#10b981", "#f43f5e", "#a78bfa", "#34d399"];

const DEFAULT_API = "http://localhost:8100";

// ── Pequeño cliente para la Claude API ────────────────────────────────────────
async function askClaude(messages, systemPrompt) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 1000,
      system: systemPrompt,
      messages,
    }),
  });
  const data = await res.json();
  return data.content?.[0]?.text || "";
}

// ── Detectar si los datos son aptos para gráfico ───────────────────────────────
function detectChartType(columns, rows) {
  if (!rows?.length || columns.length < 2) return null;
  const numericCols = columns.filter(c =>
    rows.some(r => typeof r[c] === "number")
  );
  const dateCols = columns.filter(c =>
    rows.some(r => typeof r[c] === "string" && /\d{4}-\d{2}/.test(r[c]))
  );
  if (numericCols.length === 0) return null;
  if (dateCols.length > 0) return { type: "line", xKey: dateCols[0], yKey: numericCols[0] };
  if (rows.length <= 20) return { type: "bar", xKey: columns[0], yKey: numericCols[0] };
  return null;
}

// ── Tabla de resultados ────────────────────────────────────────────────────────
function ResultTable({ columns, rows }) {
  if (!rows?.length) return <p className="empty">Sin resultados.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map(c => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((row, i) => (
            <tr key={i}>
              {columns.map(c => (
                <td key={c}>{row[c] === null ? <span className="null">null</span> : String(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 200 && <p className="hint">Mostrando 200 de {rows.length} filas.</p>}
    </div>
  );
}

// ── Gráfico automático ─────────────────────────────────────────────────────────
function AutoChart({ columns, rows }) {
  const chart = detectChartType(columns, rows);
  if (!chart) return null;
  const data = rows.slice(0, 50).map(r => ({
    ...r,
    [chart.xKey]: String(r[chart.xKey]).slice(0, 18),
  }));

  return (
    <div className="chart-box">
      <p className="chart-label">
        {chart.type === "line" ? "📈" : "📊"} Vista rápida — {chart.yKey} por {chart.xKey}
      </p>
      <ResponsiveContainer width="100%" height={220}>
        {chart.type === "line" ? (
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey={chart.xKey} tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", color: "#e2e8f0" }} />
            <Line type="monotone" dataKey={chart.yKey} stroke="#6366f1" strokeWidth={2} dot={false} />
          </LineChart>
        ) : (
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis dataKey={chart.xKey} tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", color: "#e2e8f0" }} />
            <Bar dataKey={chart.yKey} fill="#6366f1" radius={[4, 4, 0, 0]} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

// ── App principal ──────────────────────────────────────────────────────────────
export default function App() {
  const [apiUrl, setApiUrl] = useState(DEFAULT_API);
  const [schema, setSchema] = useState(null);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [schemaError, setSchemaError] = useState(null);

  const [question, setQuestion] = useState("");
  const [chatHistory, setChatHistory] = useState([]);
  const [thinking, setThinking] = useState(false);

  const [sqlResult, setSqlResult] = useState(null);
  const [sqlError, setSqlError] = useState(null);

  const [tab, setTab] = useState("chat"); // chat | schema | sql

  // Cargar esquema
  const loadSchema = useCallback(async () => {
    setLoadingSchema(true);
    setSchemaError(null);
    try {
      const res = await fetch(`${apiUrl}/schema?schema_name=public`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSchema(data);
    } catch (e) {
      setSchemaError(`No se pudo conectar a pg-tools en ${apiUrl}. ¿Está corriendo? Error: ${e.message}`);
    } finally {
      setLoadingSchema(false);
    }
  }, [apiUrl]);

  // Ejecutar SQL
  const runSQL = useCallback(async (sql) => {
    setSqlError(null);
    setSqlResult(null);
    try {
      const res = await fetch(`${apiUrl}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: sql, limit: 500 }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Error desconocido");
      return data;
    } catch (e) {
      setSqlError(e.message);
      return null;
    }
  }, [apiUrl]);

  // Chat con IA
  const sendQuestion = useCallback(async () => {
    if (!question.trim() || thinking) return;
    if (!schema) {
      setChatHistory(h => [...h, {
        role: "assistant",
        text: "⚠️ Primero conecta a tu base de datos usando el botón **Conectar BD**.",
        sql: null, result: null
      }]);
      return;
    }

    const userMsg = question.trim();
    setQuestion("");
    setThinking(true);
    setChatHistory(h => [...h, { role: "user", text: userMsg }]);

    // Construir system prompt con el esquema
    const schemaStr = Object.entries(schema.tables)
      .map(([table, info]) => {
        const cols = info.columns.map(c =>
          `  - ${c.name} (${c.type}${c.constraint === "PRIMARY KEY" ? ", PK" : ""}${!c.nullable ? ", NOT NULL" : ""})`
        ).join("\n");
        const fks = info.foreign_keys.map(fk => `  - FK: ${fk.column} → ${fk.references}`).join("\n");
        return `Tabla: ${table} (${info.row_count?.toLocaleString() ?? "?"} filas)\n${cols}${fks ? "\n" + fks : ""}`;
      }).join("\n\n");

    const systemPrompt = `Eres un asistente de datos experto en PostgreSQL.
Tienes acceso al siguiente esquema de base de datos:

${schemaStr}

Tu tarea:
1. Analiza la pregunta del usuario.
2. Responde con un JSON válido con esta estructura EXACTA (sin markdown, sin texto extra):
{
  "explanation": "Explicación breve de qué vas a consultar",
  "sql": "SELECT ... (SQL válido para PostgreSQL, usa comillas dobles para identificadores)",
  "summary_prompt": "Pregunta breve para resumir los resultados después de obtenerlos"
}

Si la pregunta no requiere SQL (es conceptual), usa sql: null.
Usa siempre LIMIT en tus consultas. Máximo 500 filas.`;

    const messages = [
      ...chatHistory
        .filter(m => m.role === "user")
        .slice(-4)
        .map(m => ({ role: "user", content: m.text })),
      { role: "user", content: userMsg }
    ];

    try {
      const raw = await askClaude(messages, systemPrompt);
      let parsed;
      try {
        const clean = raw.replace(/```json|```/g, "").trim();
        parsed = JSON.parse(clean);
      } catch {
        setChatHistory(h => [...h, { role: "assistant", text: raw, sql: null, result: null }]);
        setThinking(false);
        return;
      }

      let result = null;
      let finalText = parsed.explanation || "";

      if (parsed.sql) {
        result = await runSQL(parsed.sql);
        if (result && parsed.summary_prompt) {
          // Pedir resumen al LLM con los datos reales
          const summarySystem = `Eres un analista de datos. El usuario preguntó: "${userMsg}".
Se ejecutó este SQL: ${parsed.sql}
Resultados (primeras 20 filas): ${JSON.stringify(result.rows.slice(0, 20))}
Total de filas: ${result.count}
Columnas: ${result.columns.join(", ")}

Responde en español con un análisis breve y claro de los datos. Máximo 3 párrafos.
Si ves KPIs importantes, resáltalos. Sé específico con números.`;
          const summary = await askClaude(
            [{ role: "user", content: parsed.summary_prompt }],
            summarySystem
          );
          finalText = summary;
        }
      }

      setChatHistory(h => [...h, {
        role: "assistant",
        text: finalText,
        sql: parsed.sql,
        result,
      }]);
    } catch (e) {
      setChatHistory(h => [...h, {
        role: "assistant",
        text: `Error al procesar: ${e.message}`,
        sql: null, result: null
      }]);
    } finally {
      setThinking(false);
    }
  }, [question, schema, chatHistory, thinking, runSQL]);

  return (
    <div style={{
      fontFamily: "'Inter', system-ui, sans-serif",
      background: "#080f1e",
      minHeight: "100vh",
      color: "#e2e8f0",
      display: "flex",
      flexDirection: "column",
    }}>
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; } 
        ::-webkit-scrollbar-track { background: #0f172a; }
        ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
        
        .header { background: #0d1525; border-bottom: 1px solid #1e293b; padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
        .logo { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; color: #e2e8f0; }
        .logo span { color: #6366f1; }
        .badge { background: #1e293b; color: #94a3b8; font-size: 11px; padding: 3px 8px; border-radius: 20px; }
        
        .conn-bar { display: flex; gap: 8px; align-items: center; margin-left: auto; }
        .conn-input { background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 6px 12px; border-radius: 6px; font-size: 13px; width: 220px; }
        .conn-input:focus { outline: none; border-color: #6366f1; }
        .btn { padding: 7px 14px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.15s; }
        .btn-primary { background: #6366f1; color: white; }
        .btn-primary:hover { background: #4f46e5; }
        .btn-ghost { background: #1e293b; color: #94a3b8; }
        .btn-ghost:hover { background: #334155; color: #e2e8f0; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        
        .tabs { display: flex; gap: 2px; padding: 10px 24px 0; background: #0d1525; border-bottom: 1px solid #1e293b; }
        .tab { padding: 8px 16px; font-size: 13px; cursor: pointer; border-bottom: 2px solid transparent; color: #64748b; transition: all 0.15s; }
        .tab.active { color: #6366f1; border-bottom-color: #6366f1; }
        .tab:hover { color: #e2e8f0; }
        
        .content { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
        
        /* Chat */
        .chat-area { flex: 1; overflow-y: auto; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }
        .msg { max-width: 85%; }
        .msg.user { align-self: flex-end; }
        .msg.assistant { align-self: flex-start; }
        .bubble { padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.6; }
        .msg.user .bubble { background: #4f46e5; color: white; border-radius: 12px 12px 2px 12px; }
        .msg.assistant .bubble { background: #0f172a; border: 1px solid #1e293b; border-radius: 12px 12px 12px 2px; }
        
        .sql-block { margin-top: 10px; background: #020817; border: 1px solid #1e293b; border-radius: 8px; overflow: hidden; }
        .sql-header { padding: 6px 12px; background: #0d1525; font-size: 11px; color: #475569; font-family: monospace; display: flex; justify-content: space-between; }
        .sql-code { padding: 12px; font-family: "JetBrains Mono", "Fira Code", monospace; font-size: 12px; color: #7dd3fc; white-space: pre-wrap; overflow-x: auto; }
        
        .thinking { display: flex; gap: 6px; align-items: center; padding: 12px 16px; }
        .dot { width: 8px; height: 8px; border-radius: 50%; background: #6366f1; animation: pulse 1.2s infinite; }
        .dot:nth-child(2) { animation-delay: 0.2s; }
        .dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes pulse { 0%,80%,100% { opacity: 0.2 } 40% { opacity: 1 } }
        
        .input-bar { padding: 16px 24px; background: #0d1525; border-top: 1px solid #1e293b; display: flex; gap: 10px; }
        .question-input { flex: 1; background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 10px 16px; border-radius: 8px; font-size: 14px; resize: none; }
        .question-input:focus { outline: none; border-color: #6366f1; }
        .question-input::placeholder { color: #475569; }
        .send-btn { padding: 10px 20px; background: #6366f1; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; }
        .send-btn:hover { background: #4f46e5; }
        .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        
        /* Schema */
        .schema-view { flex: 1; overflow-y: auto; padding: 20px 24px; }
        .table-card { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; margin-bottom: 12px; overflow: hidden; }
        .table-card-header { padding: 12px 16px; background: #0d1525; display: flex; align-items: center; justify-content: space-between; cursor: pointer; }
        .table-name { font-weight: 600; font-size: 14px; color: #e2e8f0; font-family: monospace; }
        .row-count { font-size: 12px; color: #64748b; }
        .col-list { padding: 8px 0; }
        .col-row { padding: 6px 16px; display: flex; gap: 12px; align-items: center; border-top: 1px solid #1e293b; font-size: 13px; }
        .col-name { font-family: monospace; color: #7dd3fc; min-width: 160px; }
        .col-type { color: #64748b; font-size: 12px; }
        .col-badge { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }
        .pk { background: #1e1b4b; color: #a5b4fc; }
        .fk-list { padding: 8px 16px; border-top: 1px solid #1e293b; }
        .fk-item { font-size: 12px; color: #f59e0b; font-family: monospace; }
        
        /* Table results */
        .table-wrap { overflow-x: auto; margin-top: 10px; }
        table { border-collapse: collapse; width: 100%; font-size: 12px; }
        th { background: #0d1525; padding: 8px 12px; text-align: left; color: #94a3b8; font-weight: 500; border-bottom: 1px solid #1e293b; white-space: nowrap; }
        td { padding: 7px 12px; border-bottom: 1px solid #1e293b; color: #cbd5e1; white-space: nowrap; max-width: 300px; overflow: hidden; text-overflow: ellipsis; }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #1e293b22; }
        .null { color: #475569; font-style: italic; }
        
        .chart-box { margin-top: 12px; background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 16px; }
        .chart-label { font-size: 12px; color: #64748b; margin-bottom: 12px; }
        
        .empty { color: #475569; font-size: 13px; padding: 16px 0; }
        .hint { color: #475569; font-size: 12px; padding: 8px 0; }
        .error-box { background: #1c0a0a; border: 1px solid #7f1d1d; border-radius: 8px; padding: 12px 16px; color: #fca5a5; font-size: 13px; margin-top: 8px; }
        
        .welcome { text-align: center; padding: 60px 24px; }
        .welcome h2 { font-size: 22px; margin-bottom: 8px; }
        .welcome p { color: #64748b; margin-bottom: 24px; line-height: 1.6; }
        .examples { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
        .example-btn { background: #0f172a; border: 1px solid #1e293b; color: #94a3b8; padding: 8px 14px; border-radius: 20px; font-size: 13px; cursor: pointer; transition: all 0.15s; }
        .example-btn:hover { border-color: #6366f1; color: #a5b4fc; }
        
        .stats-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
        .stat-chip { background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 14px; font-size: 12px; }
        .stat-chip span { display: block; color: #6366f1; font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
      `}</style>

      {/* Header */}
      <div className="header">
        <div className="logo">pg<span>·AI</span></div>
        <div className="badge">PostgreSQL Explorer</div>
        <div className="conn-bar">
          <input
            className="conn-input"
            value={apiUrl}
            onChange={e => setApiUrl(e.target.value)}
            placeholder="http://localhost:8100"
          />
          <button
            className="btn btn-primary"
            onClick={loadSchema}
            disabled={loadingSchema}
          >
            {loadingSchema ? "Conectando…" : schema ? "↺ Reconectar" : "Conectar BD"}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="tabs">
        {["chat", "schema"].map(t => (
          <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "chat" ? "💬 Chat" : "🗂 Esquema"}
          </div>
        ))}
        {schema && (
          <div style={{ marginLeft: "auto", padding: "8px 0", fontSize: 12, color: "#22d3ee" }}>
            ✓ {Object.keys(schema.tables).length} tablas · {apiUrl}
          </div>
        )}
      </div>

      {schemaError && (
        <div className="error-box" style={{ margin: "12px 24px" }}>{schemaError}</div>
      )}

      <div className="content">
        {/* CHAT TAB */}
        {tab === "chat" && (
          <>
            <div className="chat-area">
              {chatHistory.length === 0 && (
                <div className="welcome">
                  <h2>Haz preguntas sobre tus datos</h2>
                  <p>
                    {schema
                      ? `Conectado a ${Object.keys(schema.tables).length} tablas. Pregunta en lenguaje natural.`
                      : "Conecta tu base de datos primero usando el botón de arriba."}
                  </p>
                  <div className="examples">
                    {[
                      "¿Cuántos registros hay en cada tabla?",
                      "¿Cuáles son los 10 más recientes?",
                      "Muestra un resumen del esquema",
                      "¿Qué tablas tienen más datos?",
                      "Dame estadísticas de los últimos 30 días",
                    ].map(ex => (
                      <button key={ex} className="example-btn" onClick={() => setQuestion(ex)}>
                        {ex}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {chatHistory.map((msg, i) => (
                <div key={i} className={`msg ${msg.role}`}>
                  {msg.role === "user" ? (
                    <div className="bubble">{msg.text}</div>
                  ) : (
                    <div>
                      <div className="bubble">
                        {msg.text}
                        {msg.result && (
                          <>
                            <div className="stats-row" style={{ marginTop: 12 }}>
                              <div className="stat-chip">
                                <span>{msg.result.count.toLocaleString()}</span>
                                filas devueltas
                              </div>
                              <div className="stat-chip">
                                <span>{msg.result.columns.length}</span>
                                columnas
                              </div>
                            </div>
                            <AutoChart columns={msg.result.columns} rows={msg.result.rows} />
                            <ResultTable columns={msg.result.columns} rows={msg.result.rows} />
                          </>
                        )}
                      </div>
                      {msg.sql && (
                        <div className="sql-block">
                          <div className="sql-header">
                            <span>SQL generado</span>
                            <span style={{ color: "#22d3ee" }}>SELECT</span>
                          </div>
                          <div className="sql-code">{msg.sql}</div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}

              {thinking && (
                <div className="msg assistant">
                  <div className="bubble thinking">
                    <div className="dot" /><div className="dot" /><div className="dot" />
                  </div>
                </div>
              )}
            </div>

            <div className="input-bar">
              <textarea
                className="question-input"
                rows={2}
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); } }}
                placeholder="Pregunta algo sobre tus datos… (Enter para enviar)"
              />
              <button className="send-btn" onClick={sendQuestion} disabled={thinking || !question.trim()}>
                {thinking ? "…" : "Enviar"}
              </button>
            </div>
          </>
        )}

        {/* SCHEMA TAB */}
        {tab === "schema" && (
          <div className="schema-view">
            {!schema ? (
              <div className="welcome">
                <h2>Sin conexión</h2>
                <p>Conecta tu base de datos para explorar el esquema.</p>
              </div>
            ) : (
              Object.entries(schema.tables).map(([tableName, info]) => (
                <TableCard key={tableName} name={tableName} info={info} />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function TableCard({ name, info }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="table-card">
      <div className="table-card-header" onClick={() => setOpen(o => !o)}>
        <span className="table-name">{name}</span>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span className="row-count">{info.row_count?.toLocaleString() ?? "?"} filas</span>
          <span style={{ color: "#475569", fontSize: 12 }}>{open ? "▲" : "▼"}</span>
        </div>
      </div>
      {open && (
        <>
          <div className="col-list">
            {info.columns.map(col => (
              <div key={col.name} className="col-row">
                <span className="col-name">{col.name}</span>
                <span className="col-type">{col.type}</span>
                {col.constraint === "PRIMARY KEY" && <span className="col-badge pk">PK</span>}
                {!col.nullable && <span style={{ fontSize: 10, color: "#475569" }}>NOT NULL</span>}
              </div>
            ))}
          </div>
          {info.foreign_keys?.length > 0 && (
            <div className="fk-list">
              {info.foreign_keys.map((fk, i) => (
                <div key={i} className="fk-item">→ {fk.column} → {fk.references}</div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
