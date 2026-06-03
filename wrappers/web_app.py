from fastapi import FastAPI, Request, Response, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import uuid
import tempfile
import os
import asyncio
import json
from typing import Dict, List
from datetime import datetime

from core import ai_service, api as core_api, database as db

app = FastAPI(title="AI Web Widget")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COOKIE_NAME = "uid"

# Менеджер WebSocket соединений
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_tasks: Dict[str, str] = {}  # user_id -> task_id

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_tasks:
            del self.user_tasks[user_id]

    async def send_message(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_text(json.dumps(message))

    def set_task(self, user_id: str, task_id: str):
        self.user_tasks[user_id] = task_id

    def get_task(self, user_id: str) -> str:
        return self.user_tasks.get(user_id)

manager = ConnectionManager()


async def _get_or_set_uid(request: Request, response: Response) -> str:
    uid = request.cookies.get(COOKIE_NAME)
    if not uid:
        uid = uuid.uuid4().hex
        response.set_cookie(key=COOKIE_NAME, value=uid, httponly=True, samesite="Lax")
    await db.add_or_get_user(user_id=abs(hash(uid)) % (2**31), username=f"web-{uid[:8]}")
    return uid


def _uid_to_user_id(uid: str) -> int:
    return abs(hash(uid)) % (2**31)

# Асинхронная обработка аудио
async def process_audio_async(user_id: str, audio_data: bytes, task_id: str):
    """Обрабатывает аудио в фоновом режиме и отправляет результат через WebSocket"""
    try:
        # Уведомляем о начале обработки
        await manager.send_message(user_id, {
            "type": "processing_started",
            "task_id": task_id,
            "message": "Начинаем обработку аудио..."
        })
        
        # Создаем временные файлы
        temp_input = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
        temp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        
        try:
            # Сохраняем аудио
            temp_input.write(audio_data)
            temp_input.close()
            
            # Уведомляем о конвертации
            await manager.send_message(user_id, {
                "type": "processing_update",
                "task_id": task_id,
                "message": "Конвертируем в MP3..."
            })
            
            # Конвертируем в MP3
            from pydub import AudioSegment
            audio_segment = AudioSegment.from_file(temp_input.name)
            audio_segment.export(temp_mp3.name, format="mp3")
            
            # Уведомляем о транскрибации
            await manager.send_message(user_id, {
                "type": "processing_update", 
                "task_id": task_id,
                "message": "Распознаем речь..."
            })
            
            # Транскрибируем через Gemini.
            text = await ai_service.transcribe_audio(audio_data, mime_type="audio/ogg")
            
            # Уведомляем о получении ответа от AI
            await manager.send_message(user_id, {
                "type": "processing_update",
                "task_id": task_id, 
                "message": "Получаем ответ от AI..."
            })
            
            # Получаем ответ от AI
            result = await core_api.process_message(user_id=user_id, text=text)
            
            # Отправляем финальный результат
            await manager.send_message(user_id, {
                "type": "audio_result",
                "task_id": task_id,
                "transcription": text,
                "ai_response": result.get("content", "") if result else "Ошибка обработки",
                "success": True
            })
            
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
        # Отправляем ошибку
        await manager.send_message(user_id, {
            "type": "audio_result",
            "task_id": task_id,
            "error": str(e),
            "success": False
        })
    finally:
        # Очищаем задачу
        manager.user_tasks.pop(user_id, None)


# WebSocket эндпоинт
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "ping":
                await manager.send_message(user_id, {"type": "pong"})
            elif message.get("type") == "get_status":
                task_id = manager.get_task(user_id)
                await manager.send_message(user_id, {
                    "type": "status",
                    "task_id": task_id,
                    "status": "processing" if task_id else "idle"
                })
                
    except WebSocketDisconnect:
        manager.disconnect(user_id)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    html = """
<!DOCTYPE html>
<html lang=\"ru\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>AI Widget</title>
  <style>
    body { font-family: sans-serif; margin: 0; padding: 12px; }
    .box { max-width: 640px; margin: 0 auto; }
    .row { display: flex; gap: 8px; margin-top: 8px; }
    textarea { width: 100%; height: 100px; }
    #log { border: 1px solid #ddd; padding: 8px; height: 200px; overflow: auto; white-space: pre-wrap; }
    button { padding: 8px 12px; }
  </style>
  <base href=\"/\">
</head>
<body>
  <div class=\"box\">
    <h3>Виджет чата с ИИ</h3>
    <div id=\"status\"></div>
    <div id=\"log\"></div>
    <div class=\"row\">
      <textarea id=\"msg\" placeholder=\"Напишите сообщение...\"></textarea>
    </div>
    <div class=\"row\">
      <button id=\"send\">Отправить</button>
      <button id=\"rec\">🎙 Запись</button>
      <span id=\"recStatus\"></span>
    </div>
  </div>
<script>
const logEl = document.getElementById('log');
const msgEl = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const recBtn = document.getElementById('rec');
const recStatus = document.getElementById('recStatus');
const statusEl = document.getElementById('status');

let ws = null;
let userId = null;

function append(role, text) {
  const d = document.createElement('div');
  d.textContent = role + ": " + text;
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(message, type = 'info') {
  statusEl.textContent = message;
  statusEl.className = type;
}

// WebSocket подключение
function connectWebSocket() {
  if (!userId) {
    // Получаем user_id из cookie или создаем новый
    const cookies = document.cookie.split(';');
    let uid = null;
    for (let cookie of cookies) {
      const [name, value] = cookie.trim().split('=');
      if (name === 'uid') {
        uid = value;
        break;
      }
    }
    if (!uid) {
      uid = Math.random().toString(36).substring(2);
      document.cookie = `uid=${uid}; path=/`;
    }
    userId = Math.abs(hashCode(uid)) % (2**31);
  }
  
  ws = new WebSocket(`ws://localhost:8000/ws/${userId}`);
  
  ws.onopen = function() {
    setStatus('Подключено к серверу', 'success');
  };
  
  ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    
    switch(data.type) {
      case 'pong':
        // Ответ на ping
        break;
      case 'processing_started':
        setStatus(data.message, 'info');
        break;
      case 'processing_update':
        setStatus(data.message, 'info');
        break;
      case 'audio_result':
        if (data.success) {
          append("Вы", `[Голосовое]: ${data.transcription}`);
          append("ИИ", data.ai_response);
          setStatus('Обработка завершена', 'success');
        } else {
          append("Ошибка", data.error);
          setStatus('Ошибка обработки', 'error');
        }
        break;
    }
  };
  
  ws.onclose = function() {
    setStatus('Соединение потеряно. Переподключаемся...', 'warning');
    setTimeout(connectWebSocket, 3000);
  };
  
  ws.onerror = function() {
    setStatus('Ошибка соединения', 'error');
  };
}

// Простая хеш-функция для user_id
function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32bit integer
  }
  return hash;
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
            // Результат придет через WebSocket
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

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
  connectWebSocket();
});
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
    
    # Синхронная обработка текста (быстро)
    result = await core_api.process_message(user_id=user_id, text=text)
    return JSONResponse(result or {"type": "error", "content": "Нет ответа."})


@app.post("/api/audio")
async def api_audio(request: Request, audio: UploadFile = File(...)):
    import logging
    
    logging.info(f"Получен аудиофайл: {audio.filename}, MIME: {audio.content_type}")
    
    # Получаем user_id
    dummy_resp = Response()
    uid = await _get_or_set_uid(request, dummy_resp)
    user_id = _uid_to_user_id(uid)
    user_id_str = str(user_id)
    
    # Проверяем, есть ли активная задача
    if manager.get_task(user_id_str):
        return JSONResponse({
            "type": "error", 
            "content": "Обработка аудио уже выполняется. Дождитесь завершения."
        }, status_code=429)
    
    # Читаем аудио данные
    data = b""
    while True:
        chunk = await audio.read(1024 * 1024)
        if not chunk:
            break
        data += chunk
    
    # Создаем уникальный ID задачи
    task_id = f"audio_{int(datetime.now().timestamp() * 1000)}"
    manager.set_task(user_id_str, task_id)
    
    # Запускаем асинхронную обработку
    asyncio.create_task(process_audio_async(user_id, data, task_id))
    
    # Возвращаем ID задачи для отслеживания
    return JSONResponse({
        "type": "task_started",
        "task_id": task_id,
        "message": "Обработка аудио начата. Результат придет через WebSocket."
    })


async def start():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, loop="asyncio", lifespan="on")
    server = uvicorn.Server(config)
    await server.serve()

