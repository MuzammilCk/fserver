from fastapi import WebSocket, WebSocketDisconnect, Depends, APIRouter
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
import json
import asyncio
from datetime import datetime

from app.database import get_db
from app.models import Machine, User
from app.config import settings
from jose import JWTError, jwt

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_machines: Dict[str, List[WebSocket]] = {}
        # ✅ FIX: Initialize admin list
        self.active_admins: List[WebSocket] = [] 

    # --- Machine Logic ---
    async def connect_machine(self, websocket: WebSocket, machine_id: str):
        if machine_id not in self.active_machines:
            self.active_machines[machine_id] = []
        self.active_machines[machine_id].append(websocket)

    def disconnect_machine(self, websocket: WebSocket, machine_id: str):
        if machine_id in self.active_machines:
            if websocket in self.active_machines[machine_id]:
                self.active_machines[machine_id].remove(websocket)

    async def send_command(self, machine_id: str, command: str):
        """Sends command to a specific machine"""
        if machine_id in self.active_machines:
            message = json.dumps({"type": "command", "action": command})
            active_ws = [ws for ws in self.active_machines[machine_id] if ws.client_state.name == "CONNECTED"]
            if active_ws:
                await asyncio.gather(*[ws.send_text(message) for ws in active_ws])
                return True
        return False

    # --- ✅ FIX: Admin Logic (Was missing) ---
    async def connect_admin(self, websocket: WebSocket):
        await websocket.accept()
        self.active_admins.append(websocket)
        print(f"👨‍💼 Admin connected. Total admins: {len(self.active_admins)}")

    def disconnect_admin(self, websocket: WebSocket):
        if websocket in self.active_admins:
            self.active_admins.remove(websocket)
            print("👨‍💼 Admin disconnected")

    async def broadcast_alert(self, title: str, message: str, severity: str = "info"):
        """Sends a notification to ALL connected admins immediately."""
        if not self.active_admins:
            return

        payload = json.dumps({
            "type": "notification",
            "title": title,
            "message": message,
            "severity": severity,
            "timestamp": str(datetime.utcnow())
        })
        
        to_remove = []
        for ws in self.active_admins:
            if ws.client_state.name == "CONNECTED":
                try:
                    await ws.send_text(payload)
                except:
                    to_remove.append(ws)
            else:
                to_remove.append(ws)
        
        for ws in to_remove:
            self.disconnect_admin(ws)

manager = ConnectionManager()

# --- Helper to get Machine Token ---
async def get_machine_token(websocket: WebSocket) -> Optional[str]:
    for key, value in websocket.headers.items():
        if key.lower() == "x-machine-token":
            return value
    return None

# --- Endpoint 1: Machines ---
@router.websocket("/fserver/ws/{machine_id}")
async def websocket_machine_endpoint(
    websocket: WebSocket, 
    machine_id: str, 
    db: Session = Depends(get_db)
):
    await websocket.accept()
    token = await get_machine_token(websocket)
    
    if not token:
        await websocket.close(code=1008)
        return
    
    machine = db.query(Machine).filter(Machine.api_key == token).first()
    if not machine or machine.id != machine_id:
        await websocket.close(code=1008)
        return

    await manager.connect_machine(websocket, machine_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_machine(websocket, machine_id)


# --- Endpoint 2: Admins ---
@router.websocket("/fserver/ws-admin")
async def websocket_admin_endpoint(
    websocket: WebSocket,
    token: str,  
    db: Session = Depends(get_db)
):
    # Verify Token
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            print("❌ WS Auth: No email")
            await websocket.close(code=1008)
            return
            
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print("❌ WS Auth: User not found")
            await websocket.close(code=1008)
            return
            
    except JWTError:
        print("❌ WS Auth: Invalid Token")
        await websocket.close(code=1008)
        return

    await manager.connect_admin(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_admin(websocket)
