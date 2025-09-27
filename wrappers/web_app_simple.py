# Простая асинхронная версия БЕЗ WebSocket
from fastapi import FastAPI, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import uuid
import tempfile
import os
import asyncio
from datetime import datetime
from typing import Dict

from core import api as core_api, database as db

app = FastAPI(title="AI Web Widget Simple")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COOKIE_NAME = "uid"

# Хранилище задач (в реальном проекте - Redis/БД)
tasks_storage: Dict[str, dict] = {}

async def _get_or_set_uid(request: Request, response: Response) -> str:
    uid = request.cookies.get(COOKIE_NAME)
    if not uid:
        uid = uuid.uuid4().hex
        response.set_cookie(key=COOKIE_NAME, value=uid, httponly=True, samesite="Lax")
    await db.add_or_get_user(user_id=abs(hash(uid)) % (2**31), username=f"web-{uid[:8]}")
    return uid

def _uid_to_user_id(uid: str) -> int:
    return abs(hash(uid)) % (2**31)

# Асинхронная обработка аудио (без WebSocket)
async def process_audio_background(task_id: str, user_id: int, audio_data: bytes):
    """Обрабатывает аудио в фоне и сохраняет результат"""
    try:
        # Обновляем статус
        tasks_storage[task_id] = {
            "status": "processing",
            "message": "Начинаем обработку аудио...",
            "progress": 10
        }
        
        # Создаем временные файлы
        temp_input = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
        temp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        
        try:
            # Сохраняем аудио
            temp_input.write(audio_data)
            temp_input.close()
            
            tasks_storage[task_id].update({
                "message": "Конвертируем в MP3...",
                "progress": 30
            })
            
            # Конвертируем в MP3
            from pydub import AudioSegment
            audio_segment = AudioSegment.from_file(temp_input.name)
            audio_segment.export(temp_mp3.name, format="mp3")
            
            tasks_storage[task_id].update({
                "message": "Распознаем речь...",
                "progress": 60
            })
            
            # Транскрибируем
            from openai import OpenAI
            client = OpenAI(
                api_key=os.getenv("VSEGPT_API_KEY"),
                base_url="https://api.vsegpt.ru/v1",
            )
            
            with open(temp_mp3.name, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="stt-openai/whisper-1",
                    response_format="json",
                    language="ru",
                    file=audio_file
                )
            
            text = transcript.text.strip()
            
            tasks_storage[task_id].update({
                "message": "Получаем ответ от AI...",
                "progress": 80
            })
            
            # Получаем ответ от AI
            result = await core_api.process_message(user_id=user_id, text=text)
            
            # Сохраняем результат
            tasks_storage[task_id] = {
                "status": "completed",
                "message": "Обработка завершена",
                "progress": 100,
                "transcription": text,
                "ai_response": result.get("content", "") if result else "Ошибка обработки",
                "success": True
            }
            
        finally:
            # Очищаем временные файлы
            try:
                os.remove(temp_input.name)
            except:
                pass
            try:
                os.remove(temp_mp3.name)
            except:
                pass
                
    except Exception as e:
        # Сохраняем ошибку
        tasks_storage[task_id] = {
            "status": "error",
            "message": f"Ошибка: {str(e)}",
            "progress": 0,
            "success": False,
            "error": str(e)
        }

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    html = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>AI Widget Simple</title>
  <style>
    body { font-family: sans-serif; margin: 0; padding: 12px; }
    .box { max-width: 640px; margin: 0 auto; }
    .row { display: flex; gap: 8px; margin-top: 8px; }
    textarea { width: 100%; height: 100px; }
    #log { border: 1px solid #ddd; padding: 8px; height: 200px; overflow: auto; white-space: pre-wrap; }
    button { padding: 8px 12px; }
    #status { padding: 8px; margin: 8px 0; border-radius: 4px; }
    .info { background: #e3f2fd; }
    .success { background: #e8f5e8; }
    .error { background: #ffebee; }
    .progress { width: 100%; height: 20px; background: #f0f0f0; border-radius: 10px; overflow: hidden; }
    .progress-bar { height: 100%; background: #4caf50; transition: width 0.3s; }
  </style>
</head>
<body>
<div class="box">
  <h3>Виджет чата с ИИ (Простая версия)</h3>
  <div id="status"></div>
  <div id="log"></div>
  <div class="row">
    <textarea id="msg" placeholder="Напишите сообщение..."></textarea>
  </div>
  <div class="row">
    <button id="send">Отправить</button>
    <button id="rec">🎙 Запись</button>
    <span id="recStatus"></span>
  </div>
</div>
<script>
const logEl = document.getElementById('log');
const msgEl = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const recBtn = document.getElementById('rec');
const recStatus = document.getElementById('recStatus');
const statusEl = document.getElementById('status');

let currentTaskId = null;
let pollingInterval = null;

function append(role, text) {
  const d = document.createElement('div');
  d.textContent = role + ": " + text;
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(message, type = 'info') {
  statusEl.innerHTML = message;
  statusEl.className = type;
}

function setProgress(percent) {
  const progressHtml = `
    <div class="progress">
      <div class="progress-bar" style="width: ${percent}%"></div>
    </div>
  `;
  statusEl.innerHTML = statusEl.innerHTML + progressHtml;
}

// Polling - проверяем статус задачи каждые 500мс
function startPolling(taskId) {
  currentTaskId = taskId;
  pollingInterval = setInterval(async () => {
    try {
      const response = await fetch(`/api/task/${taskId}`);
      const data = await response.json();
      
      if (data.status === 'processing') {
        setStatus(data.message, 'info');
        setProgress(data.progress);
      } else if (data.status === 'completed') {
        setStatus('Обработка завершена', 'success');
        append("Вы", `[Голосовое]: ${data.transcription}`);
        append("ИИ", data.ai_response);
        stopPolling();
      } else if (data.status === 'error') {
        setStatus(data.message, 'error');
        append("Ошибка", data.error);
        stopPolling();
      }
    } catch (e) {
      setStatus('Ошибка проверки статуса', 'error');
      stopPolling();
    }
  }, 500);
}

function stopPolling() {
  if (pollingInterval) {
    clearInterval(pollingInterval);
    pollingInterval = null;
  }
  currentTaskId = null;
}

sendBtn.onclick = async () => {
  const text = msgEl.value.trim();
  if (!text) return;
  append("Вы", text);
  msgEl.value = "";
  try {
    const res = await fetch('/api/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    append("ИИ", data.content || "(пусто)");
  } catch (e) {
    append("Ошибка", String(e));
  }
};

let mediaRecorder = null;
let chunks = [];

recBtn.onclick = async () => {
  if (!mediaRecorder) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      let preferredMime = '';
      if (MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) {
        preferredMime = 'audio/ogg;codecs=opus';
      } else if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        preferredMime = 'audio/webm;codecs=opus';
      }
      const options = preferredMime ? { mimeType: preferredMime } : undefined;
      mediaRecorder = new MediaRecorder(stream, options);
      mediaRecorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
      mediaRecorder.onstop = async () => {
        recStatus.textContent = "";
        const mimeTop = (mediaRecorder && mediaRecorder.mimeType) ? mediaRecorder.mimeType.split(';')[0] : 'audio/ogg';
        const ext = mimeTop === 'audio/ogg' ? 'ogg' : (mimeTop === 'audio/webm' ? 'webm' : 'ogg');
        const blob = new Blob(chunks, { type: mimeTop });
        chunks = [];
        const form = new FormData();
        form.append('audio', blob, `voice.${ext}`);
        
        try {
          setStatus('Отправляем аудио...', 'info');
          const res = await fetch('/api/audio', {
            method: 'POST',
            body: form,
            credentials: 'include'
          });
          const data = await res.json();
          
          if (data.type === 'task_started') {
            setStatus('Аудио принято. Обрабатываем...', 'info');
            startPolling(data.task_id);
          } else if (data.type === 'error') {
            append("Ошибка", data.content);
            setStatus('Ошибка', 'error');
          }
        } catch (e) {
          append("Ошибка", String(e));
          setStatus('Ошибка отправки', 'error');
        }
      };
      mediaRecorder.start();
      recStatus.textContent = "Запись...";
      recBtn.textContent = "■ Остановить";
    } catch (e) {
      append("Ошибка", "Микрофон недоступен: " + e);
    }
  } else {
    mediaRecorder.stop();
    mediaRecorder.stream.getTracks().forEach(t => t.stop());
    mediaRecorder = null;
    recBtn.textContent = "🎙 Запись";
  }
};
</script>
</body>
</html>
"""
    response = HTMLResponse(content=html)
    await _get_or_set_uid(request, response)
    return response

@app.post("/api/send")
async def api_send(request: Request):
    data = await request.json()
    text = (data or {}).get("message", "")
    if not text:
        return JSONResponse({"type": "error", "content": "Пустое сообщение."}, status_code=400)
    
    dummy_resp = Response()
    uid = await _get_or_set_uid(request, dummy_resp)
    user_id = _uid_to_user_id(uid)
    
    result = await core_api.process_message(user_id=user_id, text=text)
    return JSONResponse(result or {"type": "error", "content": "Нет ответа."})

@app.post("/api/audio")
async def api_audio(request: Request, audio: UploadFile = File(...)):
    import logging
    
    logging.info(f"Получен аудиофайл: {audio.filename}, MIME: {audio.content_type}")
    
    dummy_resp = Response()
    uid = await _get_or_set_uid(request, dummy_resp)
    user_id = _uid_to_user_id(uid)
    
    # Читаем аудио данные
    data = b""
    while True:
        chunk = await audio.read(1024 * 1024)
        if not chunk:
            break
        data += chunk
    
    # Создаем уникальный ID задачи
    task_id = f"audio_{int(datetime.now().timestamp() * 1000)}"
    
    # Запускаем асинхронную обработку
    asyncio.create_task(process_audio_background(task_id, user_id, data))
    
    return JSONResponse({
        "type": "task_started",
        "task_id": task_id,
        "message": "Обработка аудио начата. Результат придет через polling."
    })

@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """Получить статус задачи"""
    if task_id not in tasks_storage:
        return JSONResponse({"status": "not_found"}, status_code=404)
    
    return JSONResponse(tasks_storage[task_id])

async def start():
    config = uvicorn.Config(app, host="0.0.0.0", port=8001, loop="asyncio", lifespan="on")
    server = uvicorn.Server(config)
    await server.serve()

